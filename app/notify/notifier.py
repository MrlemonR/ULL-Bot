"""OS'un kendi bildirim sistemine mesaj gönder.

Kullanıcının kararı: "kullanılan OS default mesaj sistemi neyse ordan mesaj
gelicek, örn şuan dunst kullanıyorum". Yani kendi bildirim penceremizi
çizmiyoruz — freedesktop bildirim protokolüne (`org.freedesktop.Notifications`)
mesaj bırakıyoruz, onu hangi daemon karşılarsa (dunst, mako, GNOME Shell,
KDE Plasma) o gösteriyor. Bildirim teması, konumu, süresi kullanıcının kendi
dunst yapılandırmasından gelir; biz sadece içerik ve aciliyet veriyoruz.

Öncelik sırası:
1. `dunstify` — dunst'ın kendi istemcisi. Tek üstünlüğü `--replace`: aynı
   etkinliğin bildirimi güncellenince yenisi eskisinin üstüne yazılır,
   bildirim yığını şişmez.
2. `notify-send` — libnotify'ın standart istemcisi, her masaüstünde var.
3. Hiçbiri yoksa sessizce başarısız — bildirim yokluğu uygulamayı düşürmez.
"""

from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass
from typing import Literal

from app.settings import settings

Urgency = Literal["low", "normal", "critical"]

APP_NAME = "ULL-Bot"
# Bildirim komutu asılırsa hatırlatıcı döngüsü tıkanmasın.
COMMAND_TIMEOUT = 10


@dataclass
class NotifyResult:
    ok: bool
    backend: str
    detail: str = ""


def _client() -> tuple[str, str] | None:
    """(komut yolu, arka uç adı) — hiçbiri yoksa None."""
    for name in ("dunstify", "notify-send"):
        path = shutil.which(name)
        if path:
            return path, name
    return None


def is_available() -> bool:
    return _client() is not None


def backend_name() -> str:
    client = _client()
    return client[1] if client else "yok"


def notify(
    title: str,
    body: str = "",
    *,
    urgency: Urgency = "normal",
    icon: str = "appointment-soon",
    timeout_ms: int | None = None,
    replace_key: str | None = None,
) -> NotifyResult:
    """Bir bildirim gönder. Hiçbir koşulda istisna fırlatmaz.

    `replace_key`: aynı anahtarla gönderilen bildirimler birbirinin üstüne
    yazılır (yalnızca dunstify'da). Etkinlik kimliği gibi kararlı bir değer
    ver — string'in hash'i bildirim kimliğine çevrilir.
    """
    if not settings.notifications_enabled:
        return NotifyResult(False, "kapalı", "Bildirimler ayarlardan kapatılmış.")

    client = _client()
    if client is None:
        return NotifyResult(
            False, "yok", "Ne dunstify ne notify-send bulundu — bildirim gönderilemiyor."
        )

    path, name = client
    command = [path, "--app-name", APP_NAME, "--urgency", urgency, "--icon", icon]
    if timeout_ms is not None:
        command += ["--expire-time", str(timeout_ms)]
    if replace_key and name == "dunstify":
        # dunstify replace-id'yi 32 bit pozitif tamsayı bekliyor.
        command += ["--replace", str(abs(hash(replace_key)) % 2_000_000_000 + 1)]
    command.append(title)
    if body:
        command.append(body)

    try:
        result = subprocess.run(
            command, capture_output=True, text=True, timeout=COMMAND_TIMEOUT, check=False
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return NotifyResult(False, name, f"Bildirim komutu çalıştırılamadı: {exc}")

    if result.returncode != 0:
        return NotifyResult(False, name, result.stderr.strip() or f"çıkış kodu {result.returncode}")
    return NotifyResult(True, name)
