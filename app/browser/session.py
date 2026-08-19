"""Chromium oturumu ve CDP istemcisi.

**Ayrı profil.** Kullanıcının günlük Chrome profili KULLANILMIYOR (kullanıcının
kararı). Otomasyonun kendi profili var; Gmail'e bir kez giriliyor, oturum orada
kalıyor. Böylece ajanın eli yalnızca izin verilen hesaplara uzanıyor — günlük
tarayıcıdaki banka/sosyal medya oturumlarına değil.

**`element.click()` yasak.** Tıklama her zaman `Input.dispatchMouseEvent` ile,
yani gerçek fare olayıyla yapılıyor. JS'ten tetiklenen tıklama hit-testing'i
atlar: sayfayı kaplayan görünmez bir örtü varsa onu görmez ve "tıkladım" der.
Bu ders projede bir kez alındı (DECISIONS.md); burada aynı hata sessizce
yanlış veri girmek demek olurdu.
"""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import sys
import subprocess
import urllib.request
from urllib.parse import urlparse
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

import websockets

from app.settings import settings

CDP_PORT = 9333
LAUNCH_TIMEOUT = 20.0
COMMAND_TIMEOUT = 30.0


class BrowserError(RuntimeError):
    """Tarayıcı tarafındaki hatalar — UI'da kullanıcıya gösterilir."""


class BlockedHost(BrowserError):
    """Adres otomasyonun beyaz listesinde değil.

    Ayrı bir tip, çünkü UI bunu farklı ele alıyor: kullanıcıya "şu siteye
    izin ver" düğmesi gösteriyor. Hata METNİNİ eşleştirerek anlamak
    kırılgandı — iki ayrı yerde iki farklı cümle vardı ve biri düğmeyi hiç
    tetiklemiyordu.
    """

    def __init__(self, host: str, url: str, allowlist: list[str]) -> None:
        super().__init__(
            f"{url} bu otomasyonun izinli siteleri arasında değil. "
            f"İzinliler: {', '.join(allowlist) or '(liste boş)'}"
        )
        self.host = host
        self.url = url
        self.allowlist = list(allowlist)


@dataclass
class Element:
    """DOM indeksindeki tek bir etkileşimli öğe.

    Model sayfayı BÖYLE görüyor: piksel değil, numaralı bir liste. Gerekçe
    docs/OTOMASYON.md §3 — özetle: görme modeli gerekmiyor, model koordinat
    uyduramıyor ve token maliyeti ~7 kat düşük.
    """

    index: int
    tag: str
    kind: str
    text: str
    x: int
    y: int

    def render(self) -> str:
        kind = f" type={self.kind}" if self.kind else ""
        return f"[{self.index}] <{self.tag}{kind}> {self.text!r}"


# Sayfadaki etkileşimli öğeleri numaralayan betik.
#
# Görünmeyeni listelemiyoruz: modelin tıklayamayacağı bir öğeyi göstermek onu
# yanlış adıma iter. `checkVisibility` desteklenmeyen tarayıcılarda kutu
# boyutuna düşüyoruz.
_INDEX_JS = """
(() => {
  const SEL = 'a,button,input,textarea,select,summary,[role=button],[role=link],'
            + '[role=textbox],[role=checkbox],[role=tab],[contenteditable=true],[onclick]';
  const seen = [];
  for (const el of document.querySelectorAll(SEL)) {
    const r = el.getBoundingClientRect();
    if (r.width < 3 || r.height < 3) continue;
    if (r.bottom < 0 || r.top > innerHeight * 3) continue;
    const style = getComputedStyle(el);
    if (style.visibility === 'hidden' || style.display === 'none' || style.opacity === '0') continue;
    const label = (el.getAttribute('aria-label') || el.innerText || el.value
                  || el.placeholder || el.title || el.name || '').replace(/\\s+/g, ' ').trim();
    seen.push({
      tag: el.tagName.toLowerCase(),
      kind: el.getAttribute('type') || el.getAttribute('role') || '',
      text: label.slice(0, 80),
      x: Math.round(r.x + r.width / 2),
      y: Math.round(r.y + r.height / 2),
    });
    if (seen.length >= 120) break;
  }
  return JSON.stringify({
    url: location.href,
    title: document.title,
    text: (document.body ? document.body.innerText : '').slice(0, 4000),
    elements: seen,
  });
})()
"""


@dataclass
class PageState:
    """Modelin gördüğü sayfa: adres, başlık, metin ve numaralı öğeler."""

    url: str = ""
    title: str = ""
    text: str = ""
    elements: list[Element] = field(default_factory=list)

    def render(self, limit: int = 60) -> str:
        lines = [f"Sayfa: {self.title} — {self.url}", "", "Etkileşimli öğeler:"]
        lines.extend(element.render() for element in self.elements[:limit])
        if len(self.elements) > limit:
            lines.append(f"… ve {len(self.elements) - limit} öğe daha")
        return "\n".join(lines)


class BrowserSession:
    """Tek bir Chromium süreci ve ona giden CDP bağlantısı."""

    def __init__(self, port: int = CDP_PORT) -> None:
        self.port = port
        self.process: subprocess.Popen[bytes] | None = None
        self.socket: Any = None
        self._id = 0
        self._pending: dict[int, asyncio.Future[dict[str, Any]]] = {}
        self._reader: asyncio.Task[None] | None = None
        self._lock = asyncio.Lock()
        # Ekran akışı aboneleri (UI'daki canlı görüntü).
        self._frame_handlers: list[Callable[[str], Any]] = []
        self.streaming = False
        # Açık olan pencere görünür mü? Mod değişince tarayıcıyı yeniden
        # başlatmak gerekiyor: Chromium çalışırken headless↔görünür geçişi
        # yapamıyor. Bunu izlemezsek kullanıcı "Tarayıcıyı aç" dediğinde
        # arka planda duran ESKİ headless süreç benimseniyor ve pencere
        # hiç açılmıyor (canlı yaşandı).
        self.headless: bool | None = None
        # Görünür pencerede ekran görüntüsü döngüsü (aşağıdaki `start_stream`).
        self._poller: asyncio.Task[None] | None = None
        # Bağlı olduğumuz sekmenin CDP kimliği. Kullanıcı yeni sekme açarsa
        # ya da sekmeyi kapatırsa buna bakıp yeniden bağlanıyoruz.
        self.target_id: str = ""
        # Gördüğümüz sekmeler; yeni bir tane belirince ona geçiyoruz.
        self._known_targets: set[str] = set()
        # "Tarayıcı kapandı" aboneleri (UI paneli temizlesin diye).
        self._closed_handlers: list[Callable[[], Any]] = []

    # --- yaşam döngüsü ----------------------------------------------------

    @property
    def profile_dir(self):
        """Otomasyonun KENDİ profili — kullanıcının Chrome'u değil."""
        path = settings.data_dir / "browser-profile"
        path.mkdir(parents=True, exist_ok=True)
        return path

    @property
    def running(self) -> bool:
        """Tarayıcı ayakta mı?

        Süreç tutamacına GÜVENİLMEZ. Chromium, aynı `--user-data-dir` ile
        zaten bir örnek çalışıyorsa yeni süreci ona devredip HEMEN ÇIKIYOR;
        `poll()` bir çıkış kodu döndürüyor ve kod "tarayıcı kapandı" sanıyor
        — oysa pencere ekranda duruyor. Canlı yaşandı: ajan her adımda
        "Tarayıcı kapalı" hatası verdi.

        Otorite CDP bağlantısıdır; süreç tutamacı yalnızca kapatmak için.
        """
        if self.socket is not None:
            return True
        return self.process is not None and self.process.poll() is None

    def _binary(self) -> str:
        """Chromium/Chrome/Edge çalıştırılabilirini bul.

        Windows'ta `PATH`te olmuyorlar; bu yüzden bilinen kurulum yolları da
        taranıyor. Edge de kabul: Chromium tabanlı, aynı CDP'yi konuşuyor ve
        her Windows'ta kurulu geliyor.
        """
        override = os.environ.get("CHROME_PATH", "").strip()
        if override and Path(override).is_file():
            return override

        names = (
            "chromium", "chromium-browser", "google-chrome-stable", "google-chrome",
            "chrome", "msedge",
        )
        for name in names:
            path = shutil.which(name)
            if path:
                return path

        if sys.platform.startswith("win"):
            program_files = [
                os.environ.get("PROGRAMFILES", r"C:\Program Files"),
                os.environ.get("PROGRAMFILES(X86)", r"C:\Program Files (x86)"),
                os.environ.get("LOCALAPPDATA", ""),
            ]
            relative = [
                r"Google\Chrome\Application\chrome.exe",
                r"Microsoft\Edge\Application\msedge.exe",
                r"Chromium\Application\chrome.exe",
            ]
            for base in program_files:
                for rel in relative:
                    if not base:
                        continue
                    candidate = Path(base) / rel
                    if candidate.is_file():
                        return str(candidate)
            raise BrowserError(
                "Chrome/Edge bulunamadı. Chrome kur ya da `CHROME_PATH` ortam "
                "değişkenine tam yolu yaz."
            )

        raise BrowserError("Chromium bulunamadı. Kurulum: sudo pacman -S chromium")

    async def start(self, *, headless: bool = True, width: int = 1280, height: int = 800) -> None:
        """Tarayıcıyı başlat ve CDP'ye bağlan (zaten açıksa dokunma)."""
        if self.running and self.socket is not None:
            if self.headless == headless:
                return
            await self.stop()

        args = [
            self._binary(),
            f"--remote-debugging-port={self.port}",
            f"--user-data-dir={self.profile_dir}",
            f"--window-size={width},{height}",
            "--no-first-run",
            "--no-default-browser-check",
            "--disable-features=Translate,MediaRouter",
            # Otomasyonun açtığı sekmeler arka planda da çizilsin; aksi hâlde
            # pencere gizliyken ekran akışı donuyor.
            "--disable-backgrounding-occluded-windows",
            "--disable-renderer-backgrounding",
        ]
        if headless:
            args.append("--headless=new")
        args.append("about:blank")

        self.process = subprocess.Popen(
            args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True
        )
        self.headless = headless
        await self._connect()

    async def _connect(self) -> None:
        deadline = asyncio.get_running_loop().time() + LAUNCH_TIMEOUT
        last: Exception | None = None
        while asyncio.get_running_loop().time() < deadline:
            try:
                targets = await asyncio.to_thread(
                    lambda: json.load(urllib.request.urlopen(
                        f"http://127.0.0.1:{self.port}/json", timeout=2
                    ))
                )
                pages = [t for t in targets if t.get("type") == "page"]
                if pages:
                    # En son açılan sekmeye bağlanıyoruz: kullanıcı bir bağlantıyı
                    # yeni sekmede açtığında gördüğü sayfa odur.
                    page = pages[-1]
                    self.target_id = page.get("id", "")
                    self._known_targets = {item.get("id", "") for item in pages}
                    self.socket = await websockets.connect(
                        page["webSocketDebuggerUrl"], max_size=None
                    )
                    self._reader = asyncio.create_task(self._read_loop())
                    await self.send("Page.enable")
                    await self.send("Runtime.enable")
                    await self.send("DOM.enable")
                    return
            except Exception as exc:  # tarayıcı henüz ayakta değil
                last = exc
            await asyncio.sleep(0.3)
        raise BrowserError(f"Tarayıcıya bağlanılamadı: {last}")

    async def stop(self, *, close_browser: bool = False) -> None:
        """Bağlantıyı kapat; `close_browser` ise tarayıcıyı da kapat.

        Süreç tutamacımız boş olabilir (yukarıdaki devretme durumu), o
        yüzden kapatma isteği CDP üzerinden de gönderiliyor.
        """
        if close_browser and self.socket is not None:
            try:
                await self.send("Browser.close", timeout=3)
            except Exception:
                pass
        self.streaming = False
        if self._poller:
            self._poller.cancel()
            self._poller = None
        if self._reader:
            self._reader.cancel()
            self._reader = None
        if self.socket is not None:
            try:
                await self.socket.close()
            except Exception:
                pass
            self.socket = None
        self.headless = None
        if self.process is not None:
            self.process.terminate()
            try:
                await asyncio.to_thread(self.process.wait, 5)
            except Exception:
                self.process.kill()
            self.process = None

    def on_closed(self, handler: Callable[[], Any]) -> None:
        self._closed_handlers.append(handler)

    def off_closed(self, handler: Callable[[], Any]) -> None:
        if handler in self._closed_handlers:
            self._closed_handlers.remove(handler)

    async def _notify_closed(self) -> None:
        for handler in list(self._closed_handlers):
            try:
                outcome = handler()
                if asyncio.iscoroutine(outcome):
                    await outcome
            except Exception:
                pass

    async def tabs(self) -> list[dict[str, str]]:
        """Açık sekmeler (UI'daki sekme seçici için)."""
        try:
            targets = await asyncio.to_thread(
                lambda: json.load(urllib.request.urlopen(
                    f"http://127.0.0.1:{self.port}/json", timeout=2
                ))
            )
        except Exception:
            return []
        return [
            {"id": item.get("id", ""), "title": item.get("title", ""),
             "url": item.get("url", ""), "active": item.get("id") == self.target_id}
            for item in targets if item.get("type") == "page"
        ]

    async def focus_tab(self, target_id: str) -> bool:
        """Belirli bir sekmeye geç (kullanıcı seçti ya da otomasyon istedi)."""
        tabs = await self.tabs()
        if not any(tab["id"] == target_id for tab in tabs):
            return False
        self.target_id = target_id
        self._known_targets = {tab["id"] for tab in tabs}
        await self._attach(target_id)
        await self.send("Page.bringToFront")
        return True

    async def open_for(self, url: str) -> None:
        """Otomasyonun çalışacağı sayfayı HAZIRLA.

        Kullanıcının isteği: "otomasyonu başlattığım zaman eğer sayfa açık
        değilse bile sayfayı açsın". Ayrıca birden fazla sekme açık
        olabiliyor — o yüzden önce AYNI SİTEDEN bir sekme aranıyor, yoksa
        yeni sekme açılıyor. Böylece ajan "hangi sekmedeyim" belirsizliğine
        düşmüyor.
        """
        if not url:
            return
        host = (urlparse(url).hostname or "").casefold()
        for tab in await self.tabs():
            if (urlparse(tab["url"]).hostname or "").casefold() == host:
                await self.focus_tab(tab["id"])
                return
        result = await self.send("Target.createTarget", {"url": url})
        new_id = result.get("targetId", "")
        await asyncio.sleep(1.2)
        if new_id:
            await self.focus_tab(new_id)

    async def _attach(self, target_id: str) -> None:
        """Verilen sekmeye CDP bağlantısı kur (eskisini bırakarak)."""
        tabs_raw = await asyncio.to_thread(
            lambda: json.load(urllib.request.urlopen(
                f"http://127.0.0.1:{self.port}/json", timeout=2
            ))
        )
        page = next((item for item in tabs_raw if item.get("id") == target_id), None)
        if page is None:
            raise BrowserError("Sekme bulunamadı.")
        if self._reader:
            self._reader.cancel()
            self._reader = None
        if self.socket is not None:
            try:
                await self.socket.close()
            except Exception:
                pass
        self.socket = await websockets.connect(page["webSocketDebuggerUrl"], max_size=None)
        self._reader = asyncio.create_task(self._read_loop())
        await self.send("Page.enable")
        await self.send("Runtime.enable")
        await self.send("DOM.enable")

    async def ensure_page(self) -> bool:
        """Doğru sekmeye bağlı mıyız? Değilsek yeniden bağlan.

        İki gerçek sorunu birden çözüyor:

        1. **Yeni sekme.** Bağlantı açılışta `pages[0]`a yapılıyordu ve orada
           kalıyordu. Kullanıcı (ya da bir bağlantı) yeni sekme açınca ekranda
           yeni sayfa görünüyor, ama ayna ve ajan hâlâ ESKİ sekmeye bakıyordu
           — "ai test ederken sağdaki arayüz değişmiyor" şikâyeti buydu.
        2. **Kapanan tarayıcı.** Sekme kalmadıysa pencere kapatılmış demektir;
           aboneler haberdar ediliyor ve panel temizleniyor.

        Dönen değer: hâlâ kullanılabilir bir sayfa var mı.
        """
        try:
            targets = await asyncio.to_thread(
                lambda: json.load(urllib.request.urlopen(
                    f"http://127.0.0.1:{self.port}/json", timeout=2
                ))
            )
        except Exception:
            # CDP uç noktası cevap vermiyor: tarayıcı gerçekten kapandı.
            await self.stop()
            await self._notify_closed()
            return False
        pages = [item for item in targets if item.get("type") == "page"]
        if not pages:
            await self.stop()
            await self._notify_closed()
            return False
        ids = [item.get("id", "") for item in pages]
        # YENİ sekme açıldıysa ona geç.
        #
        # Kullanıcı (ya da tıklanan bir bağlantı) yeni sekme açtığında ekranda
        # görünen sayfa odur; biz eski sekmede kalırsak hem ayna donuk kalıyor
        # hem de ajan görünmeyen bir sayfada çalışıyor. "AI test ederken
        # sağdaki arayüz değişmiyor" şikâyeti buydu.
        yeni = [tid for tid in ids if tid and tid not in self._known_targets]
        self._known_targets = set(ids)
        if yeni:
            hedef = next(item for item in pages if item.get("id") == yeni[-1])
        elif self.target_id and self.target_id in ids:
            return True
        else:
            hedef = pages[-1]
        # Sekme değişti: eskiye olan bağlantıyı bırakıp yenisine geç.
        if self._reader:
            self._reader.cancel()
            self._reader = None
        if self.socket is not None:
            try:
                await self.socket.close()
            except Exception:
                pass
            self.socket = None
        self.target_id = hedef.get("id", "")
        self.socket = await websockets.connect(hedef["webSocketDebuggerUrl"], max_size=None)
        self._reader = asyncio.create_task(self._read_loop())
        await self.send("Page.enable")
        await self.send("Runtime.enable")
        await self.send("DOM.enable")
        return True

    # --- CDP ---------------------------------------------------------------

    async def _read_loop(self) -> None:
        """Tek okuyucu: cevapları eşleştirir, ekran karelerini dağıtır.

        Soketten yalnızca BURASI okuyor. İki yerden okumak cevapların
        karışması demek; ayrıca ekran kareleri okunmazsa soket tamponunda
        birikip akışı durduruyor (kanıt aşamasında bizzat yaşandı).
        """
        try:
            async for raw in self.socket:
                message = json.loads(raw)
                if "id" in message:
                    future = self._pending.pop(message["id"], None)
                    if future and not future.done():
                        future.set_result(message)
                    continue
                if message.get("method") == "Page.screencastFrame":
                    params = message["params"]
                    await self.send_nowait(
                        "Page.screencastFrameAck", {"sessionId": params["sessionId"]}
                    )
                    for handler in list(self._frame_handlers):
                        try:
                            result = handler(params["data"])
                            if asyncio.iscoroutine(result):
                                await result
                        except Exception:
                            pass
        except asyncio.CancelledError:
            raise
        except Exception:
            # Bağlantı düştü; bir sonraki komut bunu hata olarak bildirir.
            self.socket = None

    async def send_nowait(self, method: str, params: dict[str, Any] | None = None) -> None:
        if self.socket is None:
            return
        async with self._lock:
            self._id += 1
            await self.socket.send(json.dumps({"id": self._id, "method": method,
                                               "params": params or {}}))

    async def send(self, method: str, params: dict[str, Any] | None = None,
                   *, timeout: float | None = None) -> dict[str, Any]:
        if self.socket is None:
            raise BrowserError("Tarayıcı bağlı değil.")
        loop = asyncio.get_running_loop()
        async with self._lock:
            self._id += 1
            message_id = self._id
            future: asyncio.Future[dict[str, Any]] = loop.create_future()
            self._pending[message_id] = future
            await self.socket.send(json.dumps({"id": message_id, "method": method,
                                               "params": params or {}}))
        try:
            message = await asyncio.wait_for(future, timeout=timeout or COMMAND_TIMEOUT)
        except asyncio.TimeoutError as exc:
            self._pending.pop(message_id, None)
            raise BrowserError(f"{method} zaman aşımına uğradı.") from exc
        if "error" in message:
            raise BrowserError(f"{method}: {message['error'].get('message', '')}")
        return message.get("result", {})

    async def evaluate(self, expression: str) -> Any:
        result = await self.send(
            "Runtime.evaluate",
            {"expression": expression, "returnByValue": True, "awaitPromise": True},
        )
        return result.get("result", {}).get("value")

    # --- ekran akışı -------------------------------------------------------

    def on_frame(self, handler: Callable[[str], Any]) -> None:
        self._frame_handlers.append(handler)

    def off_frame(self, handler: Callable[[str], Any]) -> None:
        if handler in self._frame_handlers:
            self._frame_handlers.remove(handler)

    async def start_stream(self, *, quality: int = 45, max_width: int = 1100) -> None:
        """Panele canlı görüntü ver.

        İki yol var ve seçim pencerenin moduna bağlı:

        - **headless** → `Page.startScreencast`. Olay tabanlı: yalnızca ekran
          değişince kare gönderiyor, bedava sayılır.
        - **görünür pencere** → periyodik `Page.captureScreenshot`. Ölçüldü:
          görünür moddaki bir pencerede screencast HİÇ kare üretmiyor
          (pencere kompozit edilmediği sürece), ama ekran görüntüsü sorunsuz
          alınıyor. Kullanıcı "tam çalışan bir tarayıcı" istediği için
          varsayılan mod görünür; o yüzden bu yol asıl yol.
        """
        # Ayna zaten AÇIK görünüyor olabilir ama döngüsü ölmüş olabilir:
        # bir abone (kopan WebSocket) hata fırlattığında task sessizce
        # bitiyordu ve bayrak açık kaldığı için bir daha başlatılmıyordu —
        # sonuç: panelde hiç görüntü yok. Bayrağa değil, döngünün canlı
        # olup olmadığına bakıyoruz.
        poller_alive = self._poller is not None and not self._poller.done()
        if self.streaming and (self.headless or poller_alive):
            return
        self.streaming = True
        if self.headless:
            await self.send("Page.startScreencast", {
                "format": "jpeg", "quality": quality, "maxWidth": max_width,
                "everyNthFrame": 1,
            })
            return
        self._poller = asyncio.create_task(self._poll_frames(quality))

    async def _poll_frames(self, quality: int) -> None:
        """Görünür pencereyi düzenli aralıkla fotoğraflayıp aboneye ver.

        Saniyede ~1.5 kare: ayna bilgilendirme amaçlı, kullanıcı asıl işi
        gerçek pencerede yapıyor. Aynı görüntüyü tekrar tekrar göndermemek
        için son karenin uzunluğu karşılaştırılıyor — ucuz ama pratikte
        duran sayfalarda trafiği ciddi düşürüyor.
        """
        last = ""
        try:
            while self.streaming:
                if not await self.ensure_page():
                    self.streaming = False
                    break
                try:
                    result = await self.send(
                        "Page.captureScreenshot",
                        {
                            "format": "jpeg",
                            "quality": quality,
                            # `fromSurface: False` KRİTİK.
                            #
                            # Varsayılan (True) görüntüyü pencere yüzeyinden
                            # alıyor; pencere ekranda görünmüyorsa (başka
                            # masaüstü, küçültülmüş, üstü kapalı) Chromium
                            # yeni bir kare bekliyor ve komut 30 sn takılıyor.
                            # Ölçüldü: Gmail açıkken ayna tamamen susuyordu.
                            # `False` görüntüyü doğrudan render'dan alıyor,
                            # pencere görünmese de çalışıyor.
                            "fromSurface": False,
                            "captureBeyondViewport": False,
                        },
                        # Takılırsa döngüyü kilitlemesin: bir sonraki turda
                        # yeniden dener.
                        timeout=8,
                    )
                    data = result.get("data", "")
                    if data and data != last:
                        last = data
                        for handler in list(self._frame_handlers):
                            # Tek bir abonenin hatası döngüyü ÖLDÜRMESİN:
                            # kopan bir WebSocket yüzünden ayna tamamen
                            # susuyordu (canlı yaşandı).
                            try:
                                outcome = handler(data)
                                if asyncio.iscoroutine(outcome):
                                    await outcome
                            except Exception:
                                pass
                except BrowserError:
                    pass  # sekme geziniyor ya da kare gecikti; sonraki tur
                except Exception:
                    pass
                await asyncio.sleep(0.65)
        except asyncio.CancelledError:
            raise

    async def stop_stream(self) -> None:
        if not self.streaming:
            return
        self.streaming = False
        if self._poller is not None:
            self._poller.cancel()
            self._poller = None
        else:
            try:
                await self.send("Page.stopScreencast")
            except BrowserError:
                pass

    # --- sayfa okuma -------------------------------------------------------

    async def state(self) -> PageState:
        """Sayfanın modele verilecek hâli.

        Önce doğru sekmede olduğumuzu doğruluyoruz: ajan kullanıcının
        gördüğü sayfada çalışmalı, açılışta bağlandığı eski sekmede değil.
        """
        if not await self.ensure_page():
            raise BrowserError("Tarayıcı kapalı.")
        raw = await self.evaluate(_INDEX_JS)
        if not raw:
            return PageState()
        data = json.loads(raw)
        return PageState(
            url=data.get("url", ""),
            title=data.get("title", ""),
            text=data.get("text", ""),
            elements=[
                Element(index=i, tag=item["tag"], kind=item["kind"],
                        text=item["text"], x=item["x"], y=item["y"])
                for i, item in enumerate(data.get("elements", []))
            ],
        )


# Uygulama boyunca tek oturum: otomasyon görünümü ile çalıştırıcı aynı
# tarayıcıyı paylaşıyor, yoksa kullanıcı gördüğü sayfadan başka bir sayfada
# iş yapan bir ajanla karşılaşırdı.
browser = BrowserSession()
