"""Model seçimi: kota + cooldown + sağlık → hangi sağlayıcı (spec §5.3).

Faz 3'te sınıflandırıcı henüz yok, o yüzden herkes `default` zincirini
kullanıyor. Faz 4'te `task_type` gelince aynı fonksiyon `routing.yaml`'daki
o bloğu okuyacak — imza şimdiden `task_type` alıyor.

Seçim kararı **loglanır ve UI'a taşınır**: kullanıcı "neden bu model?" diye
sorabilmeli (spec §5.3).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

import yaml

from app.quota.models import QuotaConfig, get_quota_config
from app.quota.state import ProviderState, get_state, utc_now
from app.quota.tracker import free_ratio
from app.settings import settings

CONFIG_PATH = Path(__file__).resolve().parent.parent.parent / "config" / "routing.yaml"


@dataclass(frozen=True)
class Candidate:
    provider: str
    model: str


@dataclass
class Rejection:
    provider: str
    reason: str


@dataclass
class Selection:
    """Seçilen aday + neden seçildiği + elenenler."""

    provider: str
    model: str
    reason: str
    rejected: list[Rejection] = field(default_factory=list)
    forced: bool = False

    def explain(self) -> str:
        parts = [f"{self.provider} seçildi ({self.reason})"]
        for item in self.rejected:
            parts.append(f"{item.provider} elendi: {item.reason}")
        return "; ".join(parts)


class NoProviderAvailable(RuntimeError):
    def __init__(self, rejected: list[Rejection]) -> None:
        self.rejected = rejected
        detail = "; ".join(f"{r.provider}: {r.reason}" for r in rejected) or "aday yok"
        super().__init__(f"Uygun sağlayıcı kalmadı — {detail}")


@dataclass(frozen=True)
class RoutingConfig:
    profiles: dict[str, dict[str, tuple[Candidate, ...]]] = field(default_factory=dict)
    fallback_behaviour: str = "force_first"

    def chain(self, profile: str, task_type: str = "default") -> tuple[Candidate, ...]:
        blocks = self.profiles.get(profile) or {}
        return blocks.get(task_type) or blocks.get("default") or ()


def load_routing_config(path: Path | None = None) -> RoutingConfig:
    config_path = path or CONFIG_PATH
    raw: dict = {}
    if config_path.is_file():
        raw = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}

    profiles: dict[str, dict[str, tuple[Candidate, ...]]] = {}
    for profile, blocks in (raw.get("profiles") or {}).items():
        entries: dict[str, tuple[Candidate, ...]] = {}
        for task_type, candidates in (blocks or {}).items():
            entries[task_type] = tuple(
                Candidate(provider=item["provider"], model=item["model"])
                for item in candidates or []
                if item.get("provider") and item.get("model")
            )
        profiles[profile] = entries

    return RoutingConfig(
        profiles=profiles,
        fallback_behaviour=raw.get("fallback_behaviour", "force_first"),
    )


_cached: RoutingConfig | None = None


def get_routing_config(*, refresh: bool = False) -> RoutingConfig:
    global _cached
    if _cached is None or refresh:
        _cached = load_routing_config()
    return _cached


def _has_api_key(provider: str) -> bool:
    """Anahtarı olmayan sağlayıcı aday bile değil — 401 almanın anlamı yok."""
    return bool(settings.api_key_for(provider))


def evaluate(
    candidate: Candidate,
    *,
    quota_config: QuotaConfig,
    reserve_ratio: float,
    state: ProviderState | None = None,
    now: datetime | None = None,
) -> str | None:
    """Aday elenmeliyse sebebini döndür, uygunsa `None`."""
    now = now or utc_now()
    if not _has_api_key(candidate.provider):
        return "API anahtarı tanımlı değil"

    state = state or get_state(candidate.provider)
    if state.health == "down":
        return state.note or "devre dışı"
    if state.in_cooldown(now):
        return f"cooldown ({state.cooldown_seconds_left(now)} sn kaldı, sebep: {state.note or '429'})"

    ratio = free_ratio(candidate.provider, config=quota_config, state=state, now=now)
    if ratio <= reserve_ratio:
        percent = int(ratio * 100)
        return f"kota bitti/azaldı (kalan %{percent}, eşik %{int(reserve_ratio * 100)})"
    return None


def choose(
    *,
    task_type: str = "default",
    profile: str | None = None,
    exclude: set[str] | None = None,
    now: datetime | None = None,
) -> Selection:
    """Zincirdeki ilk uygun sağlayıcıyı seç.

    `exclude`: bu turda zaten denenip başarısız olan sağlayıcılar (429 aldık,
    tekrar denemeyelim).
    """
    profile = profile or settings.profile
    exclude = exclude or set()
    routing = get_routing_config()
    quota_config = get_quota_config()
    reserve_ratio = settings.reserve_ratio or quota_config.reserve_ratio

    chain = routing.chain(profile, task_type)
    rejected: list[Rejection] = []

    for candidate in chain:
        reason = evaluate(
            candidate, quota_config=quota_config, reserve_ratio=reserve_ratio, now=now
        )
        if candidate.provider in exclude:
            # Elenme sebebini yine de hesapla: 429 sonrası kullanıcıya
            # "bu turda zaten denendi" demek yerine gerçek sebebi (cooldown,
            # kota) göstermek istiyoruz.
            rejected.append(Rejection(candidate.provider, reason or "bu turda zaten denendi"))
            continue
        if reason is None:
            return Selection(
                provider=candidate.provider,
                model=candidate.model,
                reason="kota uygun" if not rejected else "sıradaki uygun sağlayıcı",
                rejected=rejected,
            )
        rejected.append(Rejection(candidate.provider, reason))

    # Hepsi elendi. Son çare: kotaya rağmen ilk adayı dene (429 riski göze
    # alınır) — hiç cevap vermemektense denemek daha iyi.
    #
    # Ama kullanıcının elle kapattığı sağlayıcı (health="down") zorlanmaz:
    # kota tahmini bizim, kapatma kararı kullanıcının. Paneldeki düğmenin
    # anlamı kalmazsa düğme yalan söylüyor demektir.
    if routing.fallback_behaviour == "force_first":
        for candidate in chain:
            if candidate.provider in exclude or not _has_api_key(candidate.provider):
                continue
            if get_state(candidate.provider).health == "down":
                continue
            return Selection(
                provider=candidate.provider,
                model=candidate.model,
                reason="tüm sağlayıcılar elendi, son çare olarak deneniyor",
                rejected=rejected,
                forced=True,
            )
    raise NoProviderAvailable(rejected)


def describe_chain(profile: str | None = None, task_type: str = "default") -> list[dict[str, Any]]:
    """UI için: zincirdeki her adayın anlık durumu."""
    profile = profile or settings.profile
    quota_config = get_quota_config()
    reserve_ratio = settings.reserve_ratio or quota_config.reserve_ratio
    rows: list[dict[str, Any]] = []
    for candidate in get_routing_config().chain(profile, task_type):
        reason = evaluate(candidate, quota_config=quota_config, reserve_ratio=reserve_ratio)
        rows.append(
            {
                "provider": candidate.provider,
                "model": candidate.model,
                "available": reason is None,
                "reason": reason,
            }
        )
    return rows
