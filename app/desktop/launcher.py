"""Native pencere: açılış ekranı → servisler → UI.

Tarayıcı açmıyoruz; pywebview sistemin WebKitGTK'sını kullanarak gerçek bir
uygulama penceresi çiziyor (kullanıcı "web arayüzü olmadan bir uygulama
olarak çalışsın" dedi). Adres çubuğu, sekme, tarayıcı menüsü yok.

Akış:

1. Pencere hemen açılır ve bir **açılış ekranı** gösterir — servisler 10-20
   saniye sürebiliyor, boş ekran beklemek kötü.
2. Arka planda süpervizör LiteLLM ve API'yi başlatır, ilerlemeyi açılış
   ekranına yazar.
3. Hazır olunca pencere `http://127.0.0.1:8080`e gider.
4. Pencere kapanınca süpervizör her şeyi durdurur — `finally` ve ayrıca
   SIGINT/SIGTERM yakalayıcıları, hangi yolla kapanırsa kapansın.

`gi` (PyGObject) sistem paketidir, venv'de yok; `_ensure_system_gi()` onu
`sys.path`e ekler. `uv sync` bunu bozmaz çünkü venv'e hiçbir şey yazmıyoruz.
"""

from __future__ import annotations

import html
import logging
import signal
import sys
import sysconfig
import threading
from pathlib import Path

from app.desktop.supervisor import Supervisor, check_environment
from app.settings import settings

logger = logging.getLogger("ull-bot.launcher")

WEB_DIR = Path(__file__).resolve().parent.parent.parent / "web"


def _ensure_system_gi() -> bool:
    """PyGObject'i sistem site-packages'ından görünür kıl.

    Proje venv'i `include-system-site-packages = false` ile kurulu (uv'nin
    varsayılanı) ama `gi` bir sistem paketi ve PyPI'dan kurulması derleme
    bağımlılıkları istiyor. En az sürtünmeli çözüm: aynı Python sürümünün
    sistem dizinini `sys.path`e eklemek.
    """
    try:
        import gi  # noqa: F401

        return True
    except ImportError:
        pass

    version = f"{sys.version_info.major}.{sys.version_info.minor}"
    candidates = [
        Path(sysconfig.get_paths()["purelib"].replace(sys.prefix, sys.base_prefix)),
        Path(f"/usr/lib/python{version}/site-packages"),
        Path(f"/usr/lib/python3/dist-packages"),
        Path(f"/usr/lib64/python{version}/site-packages"),
    ]
    for path in candidates:
        if (path / "gi").is_dir():
            sys.path.append(str(path))
            try:
                import gi  # noqa: F401

                logger.info("PyGObject bulundu: %s", path)
                return True
            except ImportError:
                sys.path.remove(str(path))
    return False


# Pencere arka ucunun gerçekten çalışıp çalışmadığını sınayan alt süreç.
# Sadece GTK penceresi açmak yetmiyor — bu makinede (Hyprland/Wayland) düz bir
# GTK penceresi sorunsuz açılıyor ama içine bir WebKit2 WebView konulduğunda
# GDK "Error 71 (Protocol error)" verip süreci öldürüyor. O yüzden probe
# gerçek yapılandırmayı, WebView dahil, kuruyor.
_BACKEND_PROBE = """
import sys
sys.path[:0] = %(paths)r
import gi
gi.require_version('Gtk', '3.0'); gi.require_version('WebKit2', '4.1')
from gi.repository import Gtk, WebKit2, GLib
if not Gtk.init_check()[0]:
    sys.exit(3)
window = Gtk.Window(); window.set_default_size(80, 60)
view = WebKit2.WebView(); view.load_html('<i>p</i>', None)
window.add(view); window.show_all()
GLib.timeout_add(400, Gtk.main_quit)
Gtk.main()
window.destroy()
print('GTK-OK')
"""


def _probe_backend(env_overrides: dict[str, str]) -> bool:
    """Verilen ortamla bir WebView açılabiliyor mu? (ayrı süreçte, ~1 sn)"""
    import os
    import subprocess

    environment = {**os.environ, **env_overrides}
    try:
        result = subprocess.run(
            [sys.executable, "-c", _BACKEND_PROBE % {"paths": sys.path}],
            capture_output=True,
            text=True,
            timeout=25,
            env=environment,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return result.returncode == 0 and "GTK-OK" in result.stdout


def configure_backend() -> dict[str, str]:
    """Pencereyi açmadan ÖNCE çalışan bir GDK arka ucu seç.

    pywebview'in GTK arka ucu GTK**3** istiyor ve GTK3'ün Wayland desteği
    bazı kurulumlarda WebKit ile birlikte çalışmıyor (bu makinede tam olarak
    böyle: `Gdk-Message: Error 71 (Protocol error)` ve süreç ölüyor —
    üstelik pencere kapanış temizliğimizi de çalıştırmadan).

    Sıra:
      1. Kullanıcı `GDK_BACKEND`i kendi ayarladıysa ona dokunma.
      2. Wayland'de değilsek zaten sorun yok.
      3. Wayland'deysek varsayılanı sına; çalışıyorsa native Wayland kalsın
         (ölçekleme ve girdi orada daha iyi).
      4. Çalışmıyorsa ve bir X sunucusu (Xwayland) varsa X11'e düş.

    Dönen sözlük `os.environ`a uygulanacak değişiklikler.
    """
    import os

    overrides: dict[str, str] = {}

    # WebKit'in DMA-BUF renderer'ı bu makinede "Failed to create GBM buffer"
    # basıyor; kapatmak yazılım yoluna düşürüyor ve çıktıyı temizliyor.
    # Kullanıcı kendi değerini verdiyse karışma.
    if "WEBKIT_DISABLE_DMABUF_RENDERER" not in os.environ:
        overrides["WEBKIT_DISABLE_DMABUF_RENDERER"] = "1"

    if os.environ.get("GDK_BACKEND"):
        logger.info("GDK_BACKEND kullanıcı tarafından ayarlanmış: %s", os.environ["GDK_BACKEND"])
        return overrides
    if not os.environ.get("WAYLAND_DISPLAY"):
        return overrides

    logger.info("Wayland oturumu — GTK3 arka ucu sınanıyor…")
    if _probe_backend(overrides):
        logger.info("Wayland arka ucu çalışıyor, olduğu gibi kullanılıyor.")
        return overrides

    if not os.environ.get("DISPLAY"):
        logger.warning(
            "GTK3'ün Wayland arka ucu WebView ile çalışmıyor ve X sunucusu da yok — "
            "pencere açılmayabilir."
        )
        return overrides

    candidate = {**overrides, "GDK_BACKEND": "x11"}
    if _probe_backend(candidate):
        logger.info("Wayland arka ucu WebView ile çalışmadı; X11'e (Xwayland) geçiliyor.")
        return candidate

    logger.warning("Ne Wayland ne X11 arka ucu sınamayı geçti; yine de denenecek.")
    return overrides


SPLASH_TEMPLATE = """<!doctype html>
<html lang="tr"><head><meta charset="utf-8"><title>ULL-Bot</title><style>
  :root {{ color-scheme: dark; }}
  * {{ box-sizing: border-box; }}
  body {{
    margin: 0; height: 100vh; display: grid; place-items: center;
    background: radial-gradient(120% 120% at 50% 0%, #1b2333 0%, #0d1017 60%);
    color: #e8ecf4; font: 15px/1.6 system-ui, "Inter", "Segoe UI", sans-serif;
  }}
  .box {{ width: min(560px, 88vw); text-align: center; }}
  .logo {{
    width: 68px; height: 68px; margin: 0 auto 22px; border-radius: 20px;
    background: linear-gradient(140deg, #5b8cff, #7c5cff 55%, #d16bff);
    display: grid; place-items: center; font-size: 30px; font-weight: 700;
    box-shadow: 0 18px 45px -18px #5b8cff;
    animation: float 2.6s ease-in-out infinite;
  }}
  @keyframes float {{ 0%,100% {{ transform: translateY(0) }} 50% {{ transform: translateY(-7px) }} }}
  h1 {{ margin: 0 0 6px; font-size: 25px; letter-spacing: -.02em; }}
  .sub {{ margin: 0 0 30px; color: #8d97ab; font-size: 13.5px; }}
  .steps {{ text-align: left; display: grid; gap: 9px; margin-bottom: 26px; }}
  .step {{
    display: flex; gap: 11px; align-items: center; padding: 11px 15px;
    border-radius: 11px; background: #151b26; border: 1px solid #212938;
    font-size: 13.5px; color: #b3bccd;
  }}
  .step .dot {{
    width: 8px; height: 8px; border-radius: 50%; background: #3a465c; flex: none;
  }}
  .step.active {{ border-color: #2f4470; background: #17203044; color: #dce4f2; }}
  .step.active .dot {{ background: #5b8cff; animation: pulse 1.2s infinite; }}
  .step.done .dot {{ background: #35c98a; }}
  .step.fail {{ border-color: #6b2b34; }}
  .step.fail .dot {{ background: #ef5f6b; }}
  @keyframes pulse {{ 50% {{ opacity: .35 }} }}
  .bar {{ height: 3px; border-radius: 3px; background: #1c2431; overflow: hidden; }}
  .bar i {{
    display: block; height: 100%; width: 35%; border-radius: 3px;
    background: linear-gradient(90deg, #5b8cff, #d16bff);
    animation: slide 1.5s ease-in-out infinite;
  }}
  @keyframes slide {{ 0% {{ margin-left: -35% }} 100% {{ margin-left: 100% }} }}
  .err {{
    margin-top: 22px; padding: 15px 17px; border-radius: 11px; text-align: left;
    background: #2a1519; border: 1px solid #6b2b34; color: #ffc9ce;
    font-size: 12.5px; white-space: pre-wrap; font-family: ui-monospace, monospace;
  }}
  .hint {{ margin-top: 16px; font-size: 12px; color: #6d7689; }}
</style></head><body>
  <div class="box">
    <div class="logo">U</div>
    <h1>ULL-Bot</h1>
    <p class="sub">Servisler başlatılıyor — uygulama kapanınca hepsi duracak.</p>
    <div class="steps" id="steps">{steps}</div>
    <div class="bar"><i></i></div>
    {error}
    <p class="hint">Kayıtlar: {log_dir}</p>
  </div>
</body></html>"""


def _splash_html(states: dict[str, tuple[str, str]], error: str = "", log_dir: str = "") -> str:
    steps = "".join(
        f'<div class="step {state}"><span class="dot"></span><span>{html.escape(text)}</span></div>'
        for state, text in states.values()
    )
    error_block = f'<div class="err">{html.escape(error)}</div>' if error else ""
    return SPLASH_TEMPLATE.format(steps=steps, error=error_block, log_dir=html.escape(log_dir))


class DesktopApp:
    def __init__(self) -> None:
        self.supervisor = Supervisor()
        self.window = None
        self._stopped = threading.Event()
        self._states: dict[str, tuple[str, str]] = {
            "litellm": ("", "LiteLLM proxy (:4000)"),
            "api": ("", "ULL-Bot API (:8080)"),
            "ui": ("", "Arayüz"),
        }

    # --- açılış ekranı güncellemeleri ------------------------------------

    def _render(self, error: str = "") -> None:
        if self.window is None:
            return
        markup = _splash_html(self._states, error, str(self.supervisor.log_dir))
        try:
            self.window.load_html(markup)
        except Exception:
            pass  # pencere kapanmış olabilir

    def _progress(self, name: str, message: str) -> None:
        logger.info("[%s] %s", name, message)
        if name in self._states:
            lowered = message.lower()
            if "hazır" in lowered or "benimsendi" in lowered:
                state = "done"
            elif "başlatılamadı" in lowered or "durdu" in lowered or "olmadı" in lowered:
                state = "fail"
            else:
                state = "active"
            self._states[name] = (state, message)
            self._render()

    # --- ana akış ---------------------------------------------------------

    def _boot(self) -> None:
        """Arka plan thread'i: servisleri başlat, sonra UI'a geç."""
        problems = check_environment()
        if problems:
            self._render("\n".join(problems))

        ready = self.supervisor.start_all(on_progress=self._progress)
        if not ready:
            self._states["ui"] = ("fail", "Arayüz açılamadı")
            self._render(
                self.supervisor.first_error()
                or "Servisler hazır olmadı. Yukarıdaki log dosyalarına bak."
            )
            return

        self._states["ui"] = ("done", "Arayüz yükleniyor…")
        self._render()
        try:
            self.window.load_url(f"http://127.0.0.1:{settings.api_port}/")
        except Exception as exc:
            self._render(f"Arayüz yüklenemedi: {exc}")

    def shutdown(self) -> None:
        """Servisleri durdur. Birden fazla kez çağrılabilir (idempotent)."""
        if self._stopped.is_set():
            return
        self._stopped.set()
        logger.info("Kapanıyor — servisler durduruluyor…")
        self.supervisor.stop_all()
        logger.info("Servisler durduruldu.")

    def run(self) -> int:
        if not _ensure_system_gi():
            print(
                "HATA: PyGObject (python-gobject) bulunamadı — native pencere açılamıyor.\n"
                "Arch: sudo pacman -S python-gobject webkit2gtk-4.1\n"
                "Debian/Ubuntu: sudo apt install python3-gi gir1.2-webkit2-4.1\n\n"
                "Alternatif: servisleri süpervizörle başlatıp tarayıcıda aç:\n"
                "  uv run python -m app.desktop.supervisor",
                file=sys.stderr,
            )
            return 2

        # Arka uç seçimi `webview` import edilmeden ÖNCE olmalı: GDK ortam
        # değişkenlerini ilk init'te okuyor, sonradan değiştirmek işe yaramaz.
        import os

        os.environ.update(configure_backend())

        # Pencere kimliği: Wayland'de app_id, X11'de WM_CLASS buradan gelir.
        # Ayarlanmazsa "launcher.py" olur ve `.desktop` dosyasındaki
        # `StartupWMClass=ULL-Bot` eşleşmez — pencere görev çubuğunda ayrı,
        # ikonsuz bir giriş olarak görünür.
        try:
            import gi

            gi.require_version("Gtk", "3.0")
            from gi.repository import GLib

            GLib.set_prgname("ULL-Bot")
            GLib.set_application_name("ULL-Bot")
        except Exception:
            pass

        import webview

        # Pencere kapanışı dışındaki yollar (Ctrl-C, kill) için de temizlik.
        for sig in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP):
            try:
                signal.signal(sig, lambda *_: self.shutdown() or sys.exit(0))
            except (ValueError, OSError):
                pass  # ana thread değilse imzalanamaz

        self.window = webview.create_window(
            "ULL-Bot",
            html=_splash_html(self._states, log_dir=str(self.supervisor.log_dir)),
            width=settings.window_width,
            height=settings.window_height,
            min_size=(940, 620),
            background_color="#0d1017",
            text_select=True,
        )
        # `closing` kapanış BAŞLARKEN tetiklenir — servisleri burada
        # durdurursak pencere kaybolduğu anda süreçler de gider.
        self.window.events.closing += lambda: self.shutdown()

        try:
            webview.start(self._boot, private_mode=False, debug=False)
        finally:
            self.shutdown()
        return 0


def main() -> int:
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )
    return DesktopApp().run()


if __name__ == "__main__":
    raise SystemExit(main())
