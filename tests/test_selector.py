"""Sağlayıcı seçimi testleri (spec §10: hepsi cooldown'dayken davranış,
reserve_ratio sınırı) ve ajan döngüsündeki sağlayıcı devri (spec §9 Faz 3)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from app.agent.llm import LLMResponse, RateLimited
from app.quota.state import disable, set_cooldown
from app.quota.tracker import record_usage, recent_events
from app.router.selector import (
    NoProviderAvailable,
    choose,
    describe_chain,
    get_routing_config,
)
from app.settings import settings
from tests.test_loop import FakeLLM, Recorder, build, tool_response

UTC = timezone.utc


# --- seçim sırası ---------------------------------------------------------


def test_first_in_chain_wins_when_everything_is_healthy(workspace: Path, all_providers) -> None:
    selection = choose()
    assert selection.provider == "groq", "routing.yaml'daki sıra: groq önce"
    assert selection.model == "chat-groq"
    assert selection.rejected == []


def test_provider_without_api_key_is_not_a_candidate(workspace: Path) -> None:
    """Varsayılan fixture'da sadece OpenRouter'ın anahtarı var."""
    selection = choose()
    assert selection.provider == "openrouter"
    reasons = {item.provider: item.reason for item in selection.rejected}
    assert "API anahtarı" in reasons["groq"]


def test_cooldown_pushes_to_next_provider(workspace: Path, all_providers) -> None:
    set_cooldown("groq", 120, note="429")
    selection = choose()
    assert selection.provider == "openrouter"
    assert "cooldown" in selection.rejected[0].reason
    assert "429" in selection.rejected[0].reason


def test_expired_cooldown_is_ignored(workspace: Path, all_providers) -> None:
    set_cooldown("groq", 30, note="429")
    later = datetime.now(UTC) + timedelta(seconds=31)
    assert choose(now=later).provider == "groq"


def test_manually_disabled_provider_is_skipped(workspace: Path, all_providers) -> None:
    disable("groq")
    selection = choose()
    assert selection.provider == "openrouter"
    assert "devre dışı" in selection.rejected[0].reason


def test_exhausted_quota_pushes_to_next_provider(workspace: Path, all_providers) -> None:
    now = datetime(2026, 8, 16, 12, 0, tzinfo=UTC)
    # groq günlük limiti 1000 → %10 eşiğinin altına inmek için 901 istek
    for index in range(901):
        record_usage(provider="groq", model="m", ts=now - timedelta(seconds=index + 61))
    selection = choose(now=now)
    assert selection.provider == "openrouter"
    assert "kota" in selection.rejected[0].reason


def test_excluded_providers_are_skipped(workspace: Path, all_providers) -> None:
    selection = choose(exclude={"groq"})
    assert selection.provider == "openrouter"
    assert selection.rejected[0].reason == "bu turda zaten denendi"


# --- hepsi elendiğinde ----------------------------------------------------


def test_all_providers_down_falls_back_to_first(workspace: Path, all_providers) -> None:
    """Hiç cevap vermemektense kotaya rağmen denemek daha iyi (force_first)."""
    for provider in ("groq", "openrouter", "gemini"):
        set_cooldown(provider, 300, note="429")
    selection = choose()
    assert selection.forced is True
    assert selection.provider == "groq"
    assert len(selection.rejected) == 3


def test_manually_disabled_provider_is_never_forced(workspace: Path, all_providers) -> None:
    """Kota tahmini bizim, kapatma kararı kullanıcının: force_first onu ezmez."""
    disable("groq")
    for provider in ("openrouter", "gemini"):
        set_cooldown(provider, 300, note="429")
    selection = choose()
    assert selection.forced is True
    assert selection.provider == "openrouter"  # groq atlandı, sıradaki zorlandı


def test_all_disabled_raises_even_with_force_first(workspace: Path, all_providers) -> None:
    for provider in ("groq", "openrouter", "gemini"):
        disable(provider)
    with pytest.raises(NoProviderAvailable):
        choose()


def test_error_behaviour_raises_when_configured(workspace: Path, all_providers, monkeypatch) -> None:
    from dataclasses import replace

    from app.router import selector as selector_module

    strict = replace(get_routing_config(), fallback_behaviour="error")
    monkeypatch.setattr(selector_module, "_cached", strict)

    for provider in ("groq", "openrouter", "gemini"):
        set_cooldown(provider, 300, note="429")
    with pytest.raises(NoProviderAvailable) as exc:
        choose()
    assert "groq" in str(exc.value)


def test_all_excluded_raises(workspace: Path, all_providers) -> None:
    with pytest.raises(NoProviderAvailable):
        choose(exclude={"groq", "openrouter", "gemini"})


def test_describe_chain_reports_status(workspace: Path, all_providers) -> None:
    set_cooldown("groq", 60, note="429")
    rows = {row["provider"]: row for row in describe_chain()}
    assert rows["groq"]["available"] is False
    assert "cooldown" in rows["groq"]["reason"]
    assert rows["openrouter"]["available"] is True
    assert rows["openrouter"]["reason"] is None


# --- döngüde sağlayıcı devri ---------------------------------------------


async def test_loop_switches_provider_on_429(workspace: Path, all_providers) -> None:
    """Faz 3 kabul kriteri: limit dolunca sessizce diğerine geç, sebebi görünsün."""
    llm = FakeLLM(
        RateLimited("limit doldu", provider="groq", retry_after="60"),
        LLMResponse(content="openrouter cevapladı"),
    )
    rec = Recorder()
    answer = await build(workspace, llm, rec).run("merhaba")

    assert answer == "openrouter cevapladı"
    assert llm.seen_models == ["chat-groq", "chat-openrouter"]

    switches = rec.of_type("model_switch")
    assert [s["provider"] for s in switches] == ["groq", "openrouter"]
    assert switches[1]["previous"] == "groq"
    assert "cooldown" in switches[1]["explanation"]

    # Kullanıcıya hata gösterilmedi — "sessizce" geçmesi gereken buydu.
    assert rec.of_type("error") == []


async def test_rate_limited_provider_goes_into_cooldown(workspace: Path, all_providers) -> None:
    llm = FakeLLM(
        RateLimited("limit doldu", provider="groq", retry_after="90"),
        LLMResponse(content="tamam"),
    )
    await build(workspace, llm, Recorder()).run("merhaba")

    from app.quota.state import get_state

    state = get_state("groq")
    assert state.in_cooldown()
    assert 80 <= state.cooldown_seconds_left() <= 90


async def test_usage_is_recorded_for_every_attempt(workspace: Path, all_providers) -> None:
    llm = FakeLLM(
        RateLimited("limit doldu", provider="groq"),
        LLMResponse(content="tamam", prompt_tokens=120, completion_tokens=30),
    )
    await build(workspace, llm, Recorder()).run("merhaba")

    events = recent_events()
    by_provider = {event["provider"]: event for event in events}
    assert by_provider["groq"]["status"] == "rate_limited"
    assert by_provider["openrouter"]["status"] == "ok"
    assert by_provider["openrouter"]["prompt_tokens"] == 120
    assert by_provider["openrouter"]["completion_tokens"] == 30


async def test_all_providers_rate_limited_reports_error(workspace: Path, all_providers, monkeypatch) -> None:
    monkeypatch.setattr(settings, "max_provider_attempts", 3)
    llm = FakeLLM(*[RateLimited("dolu", provider=p) for p in ("groq", "openrouter", "gemini")])
    rec = Recorder()
    await build(workspace, llm, rec).run("merhaba")

    errors = rec.of_type("error")
    assert errors, "hiçbir sağlayıcı kalmayınca kullanıcıya söylenmeli"
    assert "sağlayıcı" in errors[0]["message"].lower()


async def test_provider_stays_the_same_across_steps(workspace: Path, all_providers) -> None:
    """Aynı tur içinde sağlayıcı değişmediyse tekrar rozet gösterilmez."""
    (workspace / "a.txt").write_text("x", encoding="utf-8")
    llm = FakeLLM(
        tool_response("list_dir", path=str(workspace)),
        LLMResponse(content="bitti"),
    )
    rec = Recorder()
    await build(workspace, llm, rec).run("listele")
    assert len(rec.of_type("model_switch")) == 1
