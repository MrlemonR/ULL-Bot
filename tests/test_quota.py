"""Kota sayacı testleri (spec §10: pencere hesabı, gün dönümü, timezone)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from app.quota import state as state_module
from app.quota.models import (
    Limit,
    WindowUsage,
    get_quota_config,
    window_reset_at,
    window_start,
)
from app.quota.probes import (
    parse_duration,
    parse_groq_headers,
    parse_retry_after,
    record_rate_limit,
)
from app.quota.state import (
    disable,
    enable,
    get_state,
    iso,
    save_probe,
    set_cooldown,
)
from app.quota.tracker import (
    free_ratio,
    has_capacity,
    record_usage,
    snapshot,
    usage_in_window,
)

UTC = timezone.utc


# --- pencere sınırları ----------------------------------------------------


def test_rolling_window_is_relative_to_now() -> None:
    now = datetime(2026, 8, 16, 15, 30, tzinfo=UTC)
    assert window_start("minute", "rolling", now) == now - timedelta(minutes=1)
    assert window_start("day", "rolling", now) == now - timedelta(days=1)


def test_utc_midnight_reset_snaps_to_midnight() -> None:
    now = datetime(2026, 8, 16, 15, 30, tzinfo=UTC)
    assert window_start("day", "rolling_utc_midnight", now) == datetime(2026, 8, 16, tzinfo=UTC)


def test_minute_window_ignores_reset_policy() -> None:
    """Dakikalık limitler gün dönümüne bağlı değil — sağlayıcılar öyle işletmiyor."""
    now = datetime(2026, 8, 16, 0, 0, 30, tzinfo=UTC)
    for policy in ("rolling", "rolling_utc_midnight", "pacific_midnight"):
        assert window_start("minute", policy, now) == now - timedelta(minutes=1)


def test_pacific_midnight_uses_pacific_not_utc() -> None:
    """Google'ın RPD'si Pasifik gece yarısında sıfırlanıyor (canlı dokümandan)."""
    # 16 Ağustos 05:00 UTC = 15 Ağustos 22:00 PDT → Pasifik günü hâlâ 15 Ağustos.
    now = datetime(2026, 8, 16, 5, 0, tzinfo=UTC)
    start = window_start("day", "pacific_midnight", now)
    assert start == datetime(2026, 8, 15, 7, 0, tzinfo=UTC)  # 15 Ağustos 00:00 PDT
    # UTC'ye göre hesaplansaydı 16 Ağustos 00:00 çıkardı — fark tam olarak bu.
    assert start != datetime(2026, 8, 16, tzinfo=UTC)


def test_pacific_and_utc_agree_midday() -> None:
    """Gün ortasında iki politika aynı güne düşer; fark sadece dönüm anında."""
    now = datetime(2026, 8, 16, 20, 0, tzinfo=UTC)  # 13:00 PDT
    pacific = window_start("day", "pacific_midnight", now)
    assert pacific == datetime(2026, 8, 16, 7, 0, tzinfo=UTC)
    assert pacific > window_start("day", "rolling_utc_midnight", now)


def test_reset_at_is_one_window_after_start() -> None:
    now = datetime(2026, 8, 16, 15, 30, tzinfo=UTC)
    assert window_reset_at("day", "rolling_utc_midnight", now) == datetime(2026, 8, 17, tzinfo=UTC)


# --- sayım ----------------------------------------------------------------


def test_usage_is_counted_within_window(workspace: Path) -> None:
    now = datetime(2026, 8, 16, 12, 0, tzinfo=UTC)
    for offset in (5, 30, 90):  # dakika önce
        record_usage(
            provider="groq",
            model="chat-groq",
            prompt_tokens=100,
            completion_tokens=50,
            ts=now - timedelta(minutes=offset),
        )
    requests, tokens = usage_in_window("groq", "day", "rolling", now=now)
    assert requests == 3 and tokens == 450

    requests, tokens = usage_in_window("groq", "minute", "rolling", now=now)
    assert requests == 0, "hepsi 1 dakikadan eski"


def test_day_boundary_resets_the_counter(workspace: Path) -> None:
    """Gün dönümünde sayaç sıfırlanmalı (sabit reset politikası)."""
    before_midnight = datetime(2026, 8, 16, 23, 59, tzinfo=UTC)
    after_midnight = datetime(2026, 8, 17, 0, 1, tzinfo=UTC)
    record_usage(provider="openrouter", model="chat-openrouter", ts=before_midnight)

    requests, _ = usage_in_window("openrouter", "day", "rolling_utc_midnight", now=before_midnight)
    assert requests == 1
    requests, _ = usage_in_window("openrouter", "day", "rolling_utc_midnight", now=after_midnight)
    assert requests == 0, "gün döndü, sayaç sıfırlanmalıydı"

    # Kayan pencerede ise aynı olay hâlâ sayılır — iki politikanın farkı bu.
    requests, _ = usage_in_window("openrouter", "day", "rolling", now=after_midnight)
    assert requests == 1


def test_our_own_errors_do_not_consume_quota(workspace: Path) -> None:
    now = datetime(2026, 8, 16, 12, 0, tzinfo=UTC)
    record_usage(provider="groq", model="m", status="ok", ts=now)
    record_usage(provider="groq", model="m", status="error", ts=now)
    record_usage(provider="groq", model="m", status="rate_limited", ts=now)

    requests, _ = usage_in_window("groq", "day", "rolling", now=now)
    assert requests == 2, "error sayılmamalı, rate_limited sayılmalı"

    requests, _ = usage_in_window("groq", "day", "rolling", now=now, count_failures=True)
    assert requests == 3


def test_usage_is_isolated_per_provider(workspace: Path) -> None:
    now = datetime(2026, 8, 16, 12, 0, tzinfo=UTC)
    record_usage(provider="groq", model="m", ts=now)
    record_usage(provider="openrouter", model="m", ts=now)
    assert usage_in_window("groq", "day", "rolling", now=now)[0] == 1
    assert usage_in_window("openrouter", "day", "rolling", now=now)[0] == 1


# --- oran ve eşik ---------------------------------------------------------


def test_free_ratio_reflects_usage(workspace: Path) -> None:
    now = datetime(2026, 8, 16, 12, 0, tzinfo=UTC)
    # Günlük oranı ölçmek istiyoruz, o yüzden olaylar dakikalık pencerenin
    # (20 istek/dk) dışına yayılıyor — aksi hâlde en dar pencere kazanırdı.
    for index in range(25):  # openrouter günlük limiti 50
        record_usage(provider="openrouter", model="m", ts=now - timedelta(minutes=index + 2))
    assert free_ratio("openrouter", now=now) == pytest.approx(0.5, abs=0.01)


def test_reserve_ratio_is_the_elimination_threshold(workspace: Path) -> None:
    """Kotanın son %10'u acil işler için saklanır (spec §5.3)."""
    now = datetime(2026, 8, 16, 12, 0, tzinfo=UTC)
    for index in range(44):  # 50 limitin 44'ü → %12 kaldı
        record_usage(provider="openrouter", model="m", ts=now - timedelta(minutes=index + 2))
    assert has_capacity("openrouter", reserve_ratio=0.1, now=now)

    record_usage(provider="openrouter", model="m", ts=now - timedelta(minutes=2))  # 45 → %10
    assert not has_capacity("openrouter", reserve_ratio=0.1, now=now)


def test_unknown_limits_never_eliminate_a_provider(workspace: Path) -> None:
    """Gemini limitlerini yayınlamıyor (null) — bilmediğimiz limit yüzünden elemeyiz."""
    now = datetime(2026, 8, 16, 12, 0, tzinfo=UTC)
    for _ in range(500):
        record_usage(provider="gemini", model="m", ts=now)
    assert free_ratio("gemini", now=now) == 1.0
    assert has_capacity("gemini", now=now)


def test_window_usage_helpers() -> None:
    usage = WindowUsage(window="day", requests=10, max_requests=50)
    assert usage.remaining_requests == 40
    assert usage.free_ratio() == pytest.approx(0.8)
    assert usage.known

    unknown = WindowUsage(window="day", requests=999)
    assert unknown.remaining_requests is None
    assert unknown.free_ratio() == 1.0
    assert not unknown.known


def test_narrowest_window_wins(workspace: Path) -> None:
    """Dakikalık limit dolduysa günlük boş olsa da sağlayıcı elenmeli."""
    now = datetime(2026, 8, 16, 12, 0, tzinfo=UTC)
    for _ in range(20):  # openrouter: dakikada 20 istek
        record_usage(provider="openrouter", model="m", ts=now - timedelta(seconds=10))
    assert free_ratio("openrouter", now=now) == 0.0


# --- canlı veri (probe otoritedir) ---------------------------------------


def test_live_headers_override_local_counter(workspace: Path) -> None:
    now = datetime(2026, 8, 16, 12, 0, tzinfo=UTC)
    record_usage(provider="groq", model="m", ts=now)  # yerel sayaç: 1

    save_probe(
        "groq",
        {
            "live": [
                {
                    "window": "day",
                    "limit_requests": 1000,
                    "remaining_requests": 400,
                    "ts": iso(now),
                }
            ]
        },
    )
    day = next(u for u in snapshot("groq", now=now) if u.window == "day")
    assert day.source == "live"
    assert day.requests == 600, "sağlayıcının söylediği kazanmalı, yerel sayaç değil"
    assert day.remaining_requests == 400


def test_stale_live_data_falls_back_to_local_counter(workspace: Path) -> None:
    now = datetime(2026, 8, 16, 12, 0, tzinfo=UTC)
    record_usage(provider="groq", model="m", ts=now)
    save_probe(
        "groq",
        {
            "live": [
                {
                    "window": "day",
                    "limit_requests": 1000,
                    "remaining_requests": 400,
                    "ts": iso(now - timedelta(hours=2)),  # bayat
                }
            ]
        },
    )
    day = next(u for u in snapshot("groq", now=now) if u.window == "day")
    assert day.source == "local"
    assert day.requests == 1


def test_openrouter_funded_account_gets_higher_daily_cap(workspace: Path) -> None:
    now = datetime(2026, 8, 16, 12, 0, tzinfo=UTC)
    day = next(u for u in snapshot("openrouter", now=now) if u.window == "day")
    assert day.max_requests == 50

    save_probe("openrouter", {"funded": True})
    day = next(u for u in snapshot("openrouter", now=now) if u.window == "day")
    assert day.max_requests == 1000, "kredi almış hesapta günlük limit 1000"


# --- header ve süre ayrıştırma -------------------------------------------


@pytest.mark.parametrize(
    "text,expected",
    [
        ("2m59.56s", 179.56),
        ("1.5s", 1.5),
        ("88ms", 0.088),
        ("30", 30.0),
        ("1h", 3600.0),
        ("", None),
        ("abc", None),
    ],
)
def test_parse_duration(text: str, expected: float | None) -> None:
    result = parse_duration(text)
    if expected is None:
        assert result is None
    else:
        assert result == pytest.approx(expected)


def test_parse_retry_after_seconds_and_date() -> None:
    assert parse_retry_after("120", default=60) == 120
    assert parse_retry_after(None, default=60) == 60
    assert parse_retry_after("garbage", default=45) == 45
    # HTTP tarihi biçimi
    future = datetime.now(UTC) + timedelta(seconds=90)
    stamp = future.strftime("%a, %d %b %Y %H:%M:%S GMT")
    assert 80 <= parse_retry_after(stamp, default=1) <= 95


def test_groq_headers_map_to_two_different_windows() -> None:
    """Groq'ta istek limiti GÜNLÜK, token limiti DAKİKALIK — ayrı pencereler."""
    entries = parse_groq_headers(
        {
            "x-ratelimit-limit-requests": "1000",
            "x-ratelimit-remaining-requests": "997",
            "x-ratelimit-reset-requests": "2m59.56s",
            "x-ratelimit-limit-tokens": "12000",
            "x-ratelimit-remaining-tokens": "11500",
            "x-ratelimit-reset-tokens": "7.66s",
        }
    )
    by_window = {entry["window"]: entry for entry in entries}
    assert by_window["day"]["remaining_requests"] == 997
    assert by_window["minute"]["remaining_tokens"] == 11500
    assert by_window["minute"]["resets_in"] == pytest.approx(7.66)


def test_groq_headers_absent_yields_nothing() -> None:
    assert parse_groq_headers({"content-type": "application/json"}) == []


# --- cooldown ve sağlık ---------------------------------------------------


def test_cooldown_blocks_then_expires(workspace: Path) -> None:
    set_cooldown("groq", 60, note="429")
    state = get_state("groq")
    assert state.in_cooldown()
    assert 55 <= state.cooldown_seconds_left() <= 60
    assert state.note == "429"

    future = state_module.utc_now() + timedelta(seconds=61)
    assert not state.in_cooldown(future)


def test_429_sets_cooldown_from_retry_after(workspace: Path) -> None:
    until = record_rate_limit("groq", retry_after="120")
    delta = (until - state_module.utc_now()).total_seconds()
    assert 110 <= delta <= 121
    assert get_state("groq").note == "429"


def test_429_without_retry_after_uses_config_default(workspace: Path) -> None:
    until = record_rate_limit("openrouter", retry_after=None)
    delta = (until - state_module.utc_now()).total_seconds()
    expected = get_quota_config().default_cooldown_seconds
    assert expected - 5 <= delta <= expected + 1


def test_manual_disable_and_enable(workspace: Path) -> None:
    disable("groq")
    assert get_state("groq").health == "down"
    enable("groq")
    state = get_state("groq")
    assert state.health == "ok" and not state.in_cooldown()


def test_quota_config_numbers_come_from_yaml(workspace: Path) -> None:
    """Sayılar kodda değil config'de (spec §12)."""
    config = get_quota_config()
    openrouter = config.get("openrouter")
    assert openrouter is not None
    assert openrouter.limit_for("minute") == Limit(window="minute", max_requests=20)
    day = openrouter.limit_for("day")
    assert day.max_requests == 50 and day.max_requests_if_funded == 1000

    gemini = config.get("gemini")
    assert gemini.reset == "pacific_midnight"
    assert gemini.limit_for("day").max_requests is None, "yayınlanmayan limit null kalmalı"
