"""Kota veri yapıları ve `config/quotas.yaml` yükleme (spec §4.3).

Sayılar burada değil config'de: limitler sık değişiyor ve kodda sabit sayı
tutmak spec §12'nin açıkça yasakladığı şey.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Literal
from zoneinfo import ZoneInfo

import yaml

CONFIG_PATH = Path(__file__).resolve().parent.parent.parent / "config" / "quotas.yaml"

Window = Literal["minute", "hour", "day", "month"]
ResetPolicy = Literal["rolling", "rolling_utc_midnight", "pacific_midnight"]
Source = Literal["local", "live"]

PACIFIC = ZoneInfo("America/Los_Angeles")

_WINDOW_LENGTH: dict[str, timedelta] = {
    "minute": timedelta(minutes=1),
    "hour": timedelta(hours=1),
    "day": timedelta(days=1),
    "month": timedelta(days=30),
}


@dataclass(frozen=True)
class Limit:
    window: Window
    max_requests: int | None = None
    max_tokens: int | None = None
    # OpenRouter'a özel: hesap kredi satın aldıysa günlük limit yükseliyor.
    max_requests_if_funded: int | None = None

    def requests_cap(self, *, funded: bool = False) -> int | None:
        if funded and self.max_requests_if_funded is not None:
            return self.max_requests_if_funded
        return self.max_requests


@dataclass(frozen=True)
class ProviderQuota:
    name: str
    probe: str = "none"
    limits: tuple[Limit, ...] = ()
    reset: ResetPolicy = "rolling"
    reset_verified: bool = True
    model_note: str = ""

    def limit_for(self, window: Window) -> Limit | None:
        for limit in self.limits:
            if limit.window == window:
                return limit
        return None


@dataclass(frozen=True)
class QuotaConfig:
    providers: dict[str, ProviderQuota] = field(default_factory=dict)
    reserve_ratio: float = 0.1
    default_cooldown_seconds: int = 60

    def get(self, provider: str) -> ProviderQuota | None:
        return self.providers.get(provider)


def window_start(window: Window, reset: ResetPolicy, now: datetime) -> datetime:
    """Sayımın başlayacağı an.

    - `rolling`: kayan pencere (son 1 dakika / son 24 saat)
    - `rolling_utc_midnight`: gün penceresi UTC gece yarısında sıfırlanır
    - `pacific_midnight`: gün penceresi Pasifik gece yarısında sıfırlanır
      (Google'ın RPD davranışı)

    Gün dışındaki pencereler (dakika/saat) her zaman kayan pencere — sağlayıcılar
    dakikalık limitleri gün dönümüne bağlamıyor.
    """
    now = now.astimezone(timezone.utc)
    if window != "day" or reset == "rolling":
        return now - _WINDOW_LENGTH.get(window, timedelta(days=1))
    if reset == "pacific_midnight":
        local = now.astimezone(PACIFIC)
        midnight = local.replace(hour=0, minute=0, second=0, microsecond=0)
        return midnight.astimezone(timezone.utc)
    # rolling_utc_midnight
    return now.replace(hour=0, minute=0, second=0, microsecond=0)


def window_reset_at(window: Window, reset: ResetPolicy, now: datetime) -> datetime:
    """Pencerenin sıfırlanacağı an (UI'daki geri sayım için)."""
    now = now.astimezone(timezone.utc)
    if window != "day" or reset == "rolling":
        return now + _WINDOW_LENGTH.get(window, timedelta(days=1))
    return window_start(window, reset, now) + timedelta(days=1)


@dataclass
class WindowUsage:
    """Bir sağlayıcının tek bir penceredeki durumu."""

    window: Window
    requests: int = 0
    tokens: int = 0
    max_requests: int | None = None
    max_tokens: int | None = None
    source: Source = "local"
    resets_at: datetime | None = None

    @property
    def remaining_requests(self) -> int | None:
        if self.max_requests is None:
            return None
        return max(0, self.max_requests - self.requests)

    @property
    def remaining_tokens(self) -> int | None:
        if self.max_tokens is None:
            return None
        return max(0, self.max_tokens - self.tokens)

    @property
    def known(self) -> bool:
        """Limit biliniyor mu? Bilinmiyorsa 'ne kadar kaldı' hesaplanamaz."""
        return self.max_requests is not None or self.max_tokens is not None

    def free_ratio(self) -> float:
        """Kotanın ne kadarı boş (0.0 = doldu, 1.0 = hiç kullanılmadı).

        Limit bilinmiyorsa 1.0 döner: bilmediğimiz bir limit yüzünden
        sağlayıcıyı elemeyiz, 429 gelene kadar kullanmayı deneriz.
        """
        ratios: list[float] = []
        if self.max_requests:
            ratios.append(1.0 - self.requests / self.max_requests)
        if self.max_tokens:
            ratios.append(1.0 - self.tokens / self.max_tokens)
        if not ratios:
            return 1.0
        return max(0.0, min(ratios))


def _parse_limit(raw: dict) -> Limit:
    return Limit(
        window=raw.get("window", "day"),
        max_requests=raw.get("max_requests"),
        max_tokens=raw.get("max_tokens"),
        max_requests_if_funded=raw.get("max_requests_if_funded"),
    )


def load_quota_config(path: Path | None = None) -> QuotaConfig:
    config_path = path or CONFIG_PATH
    raw: dict = {}
    if config_path.is_file():
        raw = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}

    providers: dict[str, ProviderQuota] = {}
    for name, block in (raw.get("providers") or {}).items():
        block = block or {}
        providers[name] = ProviderQuota(
            name=name,
            probe=block.get("probe", "none"),
            limits=tuple(_parse_limit(item) for item in block.get("limits") or []),
            reset=block.get("reset", "rolling"),
            reset_verified=bool(block.get("reset_verified", True)),
            model_note=block.get("model_note", ""),
        )

    return QuotaConfig(
        providers=providers,
        reserve_ratio=float(raw.get("reserve_ratio", 0.1)),
        default_cooldown_seconds=int(raw.get("default_cooldown_seconds", 60)),
    )


_cached: QuotaConfig | None = None


def get_quota_config(*, refresh: bool = False) -> QuotaConfig:
    global _cached
    if _cached is None or refresh:
        _cached = load_quota_config()
    return _cached
