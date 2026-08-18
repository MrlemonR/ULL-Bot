"""IMAP parolası nerede durur.

Sıralama bilinçli: **önce sistem anahtarlığı** (libsecret / `secret-tool`,
GNOME Keyring veya KWallet arkasında), o yoksa `data_dir` altında 0600 bir
dosya. Parola hiçbir durumda SQLite'a yazılmaz — `mail_accounts` tablosunda
sadece hangi arka ucun kullanıldığı (`secret_backend`) tutulur, böylece
veritabanını yedeklemek/kopyalamak parolayı sızdırmaz.

Dosya yedeğinin kendisi düz metindir; bunu gizlemiyoruz — hesap eklerken UI
hangi arka ucun kullanıldığını gösterir, kullanıcı libsecret yoksa bunu bilir.
`audit.log` ile aynı model: 0600, kullanıcının kendi ev dizininde.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from typing import Literal

from app.settings import settings

SERVICE = "ull-bot-mail"
Backend = Literal["libsecret", "file"]

# `secret-tool` çağrıları anahtarlık kilitliyse asılabilir; kullanıcı arayüzü
# donmasın diye kısa bir tavan koyuyoruz.
SECRET_TOOL_TIMEOUT = 10


def _secret_tool() -> str | None:
    return shutil.which("secret-tool")


def _fallback_path():
    return settings.data_dir / "mail_secrets.json"


def _read_fallback() -> dict[str, str]:
    path = _fallback_path()
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def _write_fallback(data: dict[str, str]) -> None:
    path = _fallback_path()
    # Önce izinleri daralt, sonra yaz: 0600 dosya hiçbir an 0644 görünmesin.
    handle = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(handle, "w", encoding="utf-8") as file:
        json.dump(data, file, ensure_ascii=False, indent=2)
    os.chmod(path, 0o600)


def available_backend() -> Backend:
    """Bu makinede parolanın nereye yazılacağı."""
    return "libsecret" if _secret_tool() else "file"


def store_password(account_key: str, password: str) -> Backend:
    """Parolayı sakla, kullanılan arka ucu döndür."""
    tool = _secret_tool()
    if tool:
        try:
            result = subprocess.run(
                [tool, "store", "--label", f"ULL-Bot mail: {account_key}",
                 "service", SERVICE, "account", account_key],
                input=password,
                text=True,
                capture_output=True,
                timeout=SECRET_TOOL_TIMEOUT,
                check=False,
            )
            if result.returncode == 0:
                return "libsecret"
        except (OSError, subprocess.TimeoutExpired):
            pass  # anahtarlık yok/kilitli — dosyaya düş

    data = _read_fallback()
    data[account_key] = password
    _write_fallback(data)
    return "file"


def get_password(account_key: str, backend: Backend | None = None) -> str | None:
    """Parolayı oku. `backend` verilmezse ikisi de denenir."""
    tool = _secret_tool()
    if tool and backend in (None, "libsecret"):
        try:
            result = subprocess.run(
                [tool, "lookup", "service", SERVICE, "account", account_key],
                capture_output=True,
                text=True,
                timeout=SECRET_TOOL_TIMEOUT,
                check=False,
            )
            # secret-tool bulduğunu sonda newline OLMADAN basar; yine de temizle.
            if result.returncode == 0 and result.stdout:
                return result.stdout.rstrip("\n")
        except (OSError, subprocess.TimeoutExpired):
            pass

    if backend in (None, "file"):
        return _read_fallback().get(account_key)
    return None


def delete_password(account_key: str) -> None:
    """Hesap silinince parolayı da sil — ikisinden de, hangisi varsa."""
    tool = _secret_tool()
    if tool:
        try:
            subprocess.run(
                [tool, "clear", "service", SERVICE, "account", account_key],
                capture_output=True,
                timeout=SECRET_TOOL_TIMEOUT,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired):
            pass

    data = _read_fallback()
    if data.pop(account_key, None) is not None:
        _write_fallback(data)
