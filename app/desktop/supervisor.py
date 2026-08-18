"""Servis süpervizörü: uygulama açılınca başlat, kapanınca durdur.

İki süreç yönetiliyor — LiteLLM proxy (:4000) ve FastAPI orchestrator
(:8080). Üçüncüsü olan Ollama (:11434) **yönetilmiyor**: o ayrı bir sistem
servisi, bizim uygulamamızdan önce de sonra da çalışıyor olabilir; onu
kapatmak kullanıcının başka işlerini bozardı.

Üç davranış kuralı:

1. **Zaten çalışan bir servis benimsenir, öldürülmez.** Kullanıcı Faz 7'nin
   systemd birimlerini `enable` etmişse portlar zaten dinleniyordur.
   Süpervizör bunu görür, o servisi başlatmaz ve kapanışta ona DOKUNMAZ —
   açmadığımız bir şeyi kapatmayız.
2. **Süreç grubu ile öldürme.** `uv run litellm` aslında bir sarmalayıcı;
   sadece onu öldürmek asıl süreci yetim bırakır. Bu yüzden her çocuk kendi
   süreç grubunda (`start_new_session=True`) başlatılır ve sinyal tüm gruba
   (`killpg`) gönderilir.
3. **Önce nazik, sonra sert.** SIGTERM → bekle → SIGKILL. LiteLLM'in
   kapanışı birkaç saniye sürebiliyor.
"""

from __future__ import annotations

import json
import logging
import os
import signal
import socket
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

import httpx

from app.settings import settings

logger = logging.getLogger("ull-bot.supervisor")

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
# SIGTERM'den sonra SIGKILL'e geçmeden önce beklenen süre.
GRACE_SECONDS = 8
# Hazırlık kontrolleri arasındaki aralık.
POLL_INTERVAL = 0.4


def port_is_open(port: int, host: str = "127.0.0.1", timeout: float = 0.4) -> bool:
    """Portu dinleyen biri var mı? (bizim süreç olması gerekmiyor)"""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(timeout)
        return sock.connect_ex((host, port)) == 0


def _process_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True  # var ama bize ait değil
    return True


def _cmdline_matches(pid: int, expected: list[str]) -> bool:
    """PID geri dönüşümü koruması: bu süreç gerçekten bizim başlattığımız mı?

    Linux bir PID'i yeniden kullanabilir; kayıttaki numara artık bambaşka
    bir programa ait olabilir ve onu öldürmek kabul edilemez. Komut satırının
    ilk birkaç kelimesi eşleşmiyorsa dokunmuyoruz.
    """
    if not expected:
        return False
    try:
        raw = Path(f"/proc/{pid}/cmdline").read_bytes()
    except OSError:
        return False  # /proc yoksa (başka OS) riske girme
    parts = [chunk.decode("utf-8", "replace") for chunk in raw.split(b"\0") if chunk]
    return parts[: len(expected)] == list(expected)


def _kill_tree(pid: int) -> None:
    """Süreci ve grubunu durdur: önce SIGTERM, direnirse SIGKILL."""
    for sig in (signal.SIGTERM, signal.SIGKILL):
        try:
            os.killpg(os.getpgid(pid), sig)
        except (ProcessLookupError, PermissionError):
            try:
                os.kill(pid, sig)
            except (ProcessLookupError, PermissionError):
                return
        deadline = time.monotonic() + (GRACE_SECONDS if sig == signal.SIGTERM else 2)
        while time.monotonic() < deadline:
            if not _process_alive(pid):
                return
            time.sleep(0.15)


def _http_ok(url: str, timeout: float = 2.0) -> bool:
    try:
        response = httpx.get(url, timeout=timeout)
    except (httpx.HTTPError, OSError):
        return False
    # LiteLLM readiness 200, FastAPI /api/config 200. 4xx bile "ayakta"
    # sayılır — soket cevap veriyorsa süreç yaşıyordur.
    return response.status_code < 500


@dataclass
class ServiceSpec:
    name: str
    label: str
    command: list[str]
    port: int
    health_url: str
    # Bu servis olmadan uygulama açılabilir mi?
    required: bool = True


@dataclass
class ServiceHandle:
    spec: ServiceSpec
    process: subprocess.Popen | None = None
    # Port zaten dinleniyordu, biz başlatmadık → kapanışta dokunma.
    adopted: bool = False
    log_path: Path | None = None
    error: str = ""

    @property
    def running(self) -> bool:
        if self.adopted:
            return port_is_open(self.spec.port)
        return self.process is not None and self.process.poll() is None

    def status(self) -> str:
        if self.adopted:
            return "benimsendi"
        if self.process is None:
            return "başlatılmadı"
        code = self.process.poll()
        return "çalışıyor" if code is None else f"öldü (çıkış {code})"


def build_specs() -> list[ServiceSpec]:
    """Başlatılacak servisler. Komutlar `uv run` üzerinden — venv garantisi."""
    config_name = f"litellm.{settings.profile}.yaml"
    config_path = PROJECT_ROOT / "config" / config_name
    if not config_path.is_file():
        config_path = PROJECT_ROOT / "config" / "litellm.desktop.yaml"

    return [
        ServiceSpec(
            name="litellm",
            label="LiteLLM proxy",
            command=[
                "uv", "run", "litellm",
                "--config", str(config_path),
                "--port", str(settings.litellm_port),
            ],
            port=settings.litellm_port,
            health_url=f"http://127.0.0.1:{settings.litellm_port}/health/readiness",
        ),
        ServiceSpec(
            name="api",
            label="ULL-Bot API",
            command=[
                "uv", "run", "uvicorn", "app.main:app",
                "--host", "127.0.0.1",
                "--port", str(settings.api_port),
            ],
            port=settings.api_port,
            health_url=f"http://127.0.0.1:{settings.api_port}/api/config",
        ),
    ]


class Supervisor:
    def __init__(self, specs: list[ServiceSpec] | None = None) -> None:
        self.specs = specs or build_specs()
        self.handles: list[ServiceHandle] = []
        self.log_dir = settings.data_dir / "logs"
        self.log_dir.mkdir(parents=True, exist_ok=True)
        self.state_path = settings.data_dir / "services.json"

    # --- kalıntı temizliği -------------------------------------------------
    #
    # Nazik kapanış her zaman çalışmaz: GDK bir protokol hatasında süreci
    # doğrudan `exit()` ile bitiriyor, `kill -9` de aynı şeyi yapıyor. Bu
    # durumda çocuk süreçler (kendi süreç gruplarında oldukları için) hayatta
    # kalır ve portlar açık kalır — "uygulama kapanınca servisler de kapansın"
    # sözü tutulmaz. Bu yüzden başlattığımız servisleri diske yazıyoruz ve
    # bir sonraki açılışta sahipsiz kalanları topluyoruz.

    def _write_state(self) -> None:
        entries = [
            {
                "name": handle.spec.name,
                "pid": handle.process.pid,
                "port": handle.spec.port,
                "cmd": handle.spec.command[:3],
            }
            for handle in self.handles
            if handle.process is not None and not handle.adopted
        ]
        try:
            if entries:
                self.state_path.write_text(
                    json.dumps({"owner_pid": os.getpid(), "services": entries}, indent=2),
                    encoding="utf-8",
                )
            else:
                self.state_path.unlink(missing_ok=True)
        except OSError:
            pass  # durum dosyası yazılamazsa uygulama yine çalışmalı

    def _clear_state(self) -> None:
        try:
            self.state_path.unlink(missing_ok=True)
        except OSError:
            pass

    def reap_orphans(self, on_progress: Callable[[str, str], None] | None = None) -> int:
        """Önceki çalışmadan kalan servisleri topla. Kaç tanesini öldürdüğünü döndürür."""
        try:
            state = json.loads(self.state_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return 0

        owner = state.get("owner_pid")
        if isinstance(owner, int) and _process_alive(owner) and owner != os.getpid():
            # Başka bir ULL-Bot penceresi hâlâ açık — onun servislerine dokunma.
            return 0

        killed = 0
        for entry in state.get("services", []):
            pid = entry.get("pid")
            if not isinstance(pid, int) or not _process_alive(pid):
                continue
            # PID geri dönüşümüne karşı: komut satırı hâlâ bizim servisimiz mi?
            if not _cmdline_matches(pid, entry.get("cmd") or []):
                continue
            message = f"Önceki çalışmadan kalan {entry.get('name')} (pid {pid}) durduruluyor."
            logger.info(message)
            if on_progress:
                on_progress(str(entry.get("name") or "orphan"), message)
            _kill_tree(pid)
            killed += 1

        self._clear_state()
        return killed

    # --- başlatma ---------------------------------------------------------

    def start_all(self, on_progress: Callable[[str, str], None] | None = None) -> bool:
        """Servisleri başlat ve hazır olmalarını bekle.

        `on_progress(servis_adı, mesaj)` çağrılır — açılış ekranı bunu
        gösteriyor. Hepsi hazırsa `True`.
        """
        def report(name: str, message: str) -> None:
            logger.info("[%s] %s", name, message)
            if on_progress:
                on_progress(name, message)

        # Kalıntıları önce topla, yoksa portları dolu bulup onları
        # "zaten çalışıyor" sanıp benimseriz ve kapanışta bırakırız.
        self.reap_orphans(on_progress)

        for spec in self.specs:
            handle = ServiceHandle(spec=spec)
            self.handles.append(handle)

            if port_is_open(spec.port):
                handle.adopted = True
                report(spec.name, f"{spec.label} zaten çalışıyor (:{spec.port}) — benimsendi.")
                continue

            report(spec.name, f"{spec.label} başlatılıyor…")
            try:
                handle.log_path = self.log_dir / f"{spec.name}.log"
                log_file = handle.log_path.open("a", encoding="utf-8")
                log_file.write(f"\n===== {time.strftime('%Y-%m-%d %H:%M:%S')} başlatıldı =====\n")
                log_file.flush()
                handle.process = subprocess.Popen(
                    spec.command,
                    cwd=PROJECT_ROOT,
                    stdout=log_file,
                    stderr=subprocess.STDOUT,
                    stdin=subprocess.DEVNULL,
                    # Kendi süreç grubu: kapanışta tüm ağacı öldürebilelim.
                    start_new_session=True,
                )
            except OSError as exc:
                handle.error = f"{spec.label} başlatılamadı: {exc}"
                report(spec.name, handle.error)
                if spec.required:
                    return False
                continue

        # Süreçler doğdu; hazır olmalarını beklemeden ÖNCE diske yaz —
        # bekleme sırasında çökersek bile bir sonraki açılış toplayabilsin.
        self._write_state()
        return self.wait_until_ready(on_progress=on_progress)

    def wait_until_ready(
        self, timeout: int | None = None, on_progress: Callable[[str, str], None] | None = None
    ) -> bool:
        """Sağlık uçları cevap verene kadar bekle."""
        deadline = time.monotonic() + (timeout or settings.service_startup_timeout)
        pending = {handle.spec.name: handle for handle in self.handles}
        last_report = 0.0

        while pending and time.monotonic() < deadline:
            for name, handle in list(pending.items()):
                # Süreç açılışta patladıysa beklemeye devam etmenin anlamı yok.
                if handle.process is not None and handle.process.poll() is not None:
                    handle.error = (
                        f"{handle.spec.label} açılışta durdu (çıkış kodu "
                        f"{handle.process.returncode}). Log: {handle.log_path}"
                    )
                    if handle.spec.required:
                        if on_progress:
                            on_progress(name, handle.error)
                        return False
                    pending.pop(name)
                    continue

                if _http_ok(handle.spec.health_url):
                    if on_progress:
                        on_progress(name, f"{handle.spec.label} hazır.")
                    pending.pop(name)

            if pending:
                now = time.monotonic()
                if on_progress and now - last_report > 3:
                    last_report = now
                    waiting = ", ".join(handle.spec.label for handle in pending.values())
                    remaining = int(deadline - now)
                    on_progress("wait", f"Bekleniyor: {waiting} ({remaining} sn kaldı)")
                time.sleep(POLL_INTERVAL)

        if pending:
            for handle in pending.values():
                handle.error = (
                    f"{handle.spec.label} {settings.service_startup_timeout} saniyede "
                    f"hazır olmadı. Log: {handle.log_path}"
                )
            return False
        return True

    # --- durdurma ---------------------------------------------------------

    def stop_all(self) -> None:
        """Başlattığımız her şeyi durdur. Benimsenenlere dokunma."""
        ours = [handle for handle in self.handles if handle.process is not None and not handle.adopted]

        # Ters sırada: önce API, sonra LiteLLM — API kapanırken hâlâ
        # LiteLLM'e istek gönderiyor olabilir.
        for handle in reversed(ours):
            self._signal(handle, signal.SIGTERM)

        deadline = time.monotonic() + GRACE_SECONDS
        while time.monotonic() < deadline:
            if all(handle.process is None or handle.process.poll() is not None for handle in ours):
                break
            time.sleep(0.2)

        for handle in reversed(ours):
            if handle.process is not None and handle.process.poll() is None:
                logger.warning("%s SIGTERM'e cevap vermedi, SIGKILL.", handle.spec.label)
                self._signal(handle, signal.SIGKILL)

        for handle in ours:
            if handle.process is not None:
                try:
                    handle.process.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    logger.error("%s hâlâ kapanmadı.", handle.spec.label)
            logger.info("%s durduruldu.", handle.spec.label)

        self._clear_state()

    def _signal(self, handle: ServiceHandle, sig: signal.Signals) -> None:
        process = handle.process
        if process is None or process.poll() is not None:
            return
        try:
            # Süreç grubunun tamamı: `uv run` sarmalayıcısı + asıl süreç.
            os.killpg(os.getpgid(process.pid), sig)
        except (ProcessLookupError, PermissionError):
            try:
                process.send_signal(sig)
            except (ProcessLookupError, OSError):
                pass

    # --- durum ------------------------------------------------------------

    def summary(self) -> list[dict[str, object]]:
        return [
            {
                "name": handle.spec.name,
                "label": handle.spec.label,
                "port": handle.spec.port,
                "status": handle.status(),
                "adopted": handle.adopted,
                "running": handle.running,
                "log": str(handle.log_path or ""),
                "error": handle.error,
            }
            for handle in self.handles
        ]

    def first_error(self) -> str:
        for handle in self.handles:
            if handle.error:
                return handle.error
        return ""


def check_environment() -> list[str]:
    """Açılıştan önce görülebilecek sorunlar — kullanıcıya erken söyle."""
    problems: list[str] = []
    if not (PROJECT_ROOT / ".env").is_file():
        problems.append(
            ".env dosyası yok. `cp .env.example .env` yapıp API anahtarlarını gir."
        )
    import shutil

    if shutil.which("uv") is None:
        problems.append("`uv` komutu PATH'te yok — servisler başlatılamaz.")
    return problems


if __name__ == "__main__":
    # Süpervizörü tek başına çalıştırma yolu: pencere açmadan servisleri
    # ayağa kaldırıp tarayıcıda kullanmak, ya da bir sorunu ayıklamak için.
    #
    # SIGTERM yakalayıcısı şart: `timeout`, `systemctl stop` ya da düz bir
    # `kill` varsayılan olarak `finally`yi ÇALIŞTIRMADAN süreci bitirir ve
    # çocuklar (kendi süreç gruplarında oldukları için) yetim kalır —
    # portlar açık kalır, "uygulama kapanınca servisler de kapansın"
    # sözü tutulmaz.
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    supervisor = Supervisor()

    def _terminate(*_) -> None:
        print("\nSinyal alındı, servisler durduruluyor…")
        supervisor.stop_all()
        sys.exit(0)

    signal.signal(signal.SIGTERM, _terminate)
    signal.signal(signal.SIGHUP, _terminate)

    ready = supervisor.start_all(on_progress=lambda name, msg: print(f"  [{name}] {msg}"))
    for row in supervisor.summary():
        print(f"  {row['label']:<18} :{row['port']}  {row['status']}")
    if not ready:
        print("HATA:", supervisor.first_error())
        supervisor.stop_all()
        sys.exit(1)

    print("\nHazır: http://127.0.0.1:%s  — Ctrl-C ile durdur." % settings.api_port)
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("\nDurduruluyor…")
    finally:
        supervisor.stop_all()
