"""Modelin yapabileceği işler — sabit, kapalı bir küme.

Neden kapalı: ajan, kullanıcının oturum açmış Gmail'ine ve şirket tablosuna
tıklıyor. Sayfa içeriği ise düşman girdi (projenin 15/22 numaralı kuralları);
bir mail "önceki talimatlarını unut" yazabilir ve bunu okuyan taraf artık
tıklama yetkisine sahip. Modele `Runtime.evaluate` verilseydi tek bir enjekte
cümle "sayfada şu JS'i çalıştır" demeye yeterdi. Bu yüzden model yalnızca
aşağıdaki yedi eylemi isteyebiliyor ve her biri argümanlarıyla doğrulanıyor.

Ayrıca: tıklama GERÇEK fare olayıyla (`Input.dispatchMouseEvent`) yapılıyor,
`element.click()` ile değil — bkz. `session.py` başlığı.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse

from app.browser.session import BlockedHost, BrowserError, BrowserSession, PageState

# Geri alınamaz sayılan eylemler: bunlar ilk çalıştırmadan sonra da onay ister
# (kullanıcının kararı: "geri alınamaz eylemler onay istesin").
IRREVERSIBLE_WORDS = (
    "gönder", "gonder", "send", "sil", "delete", "kaldır", "kaldir", "remove",
    "öde", "ode", "pay", "satın al", "satin al", "purchase", "onayla", "confirm",
    "paylaş", "paylas", "share", "arşivle", "arsivle", "archive", "spam",
    "abonelikten çık", "unsubscribe", "yayınla", "yayinla", "publish",
)

ACTIONS = {
    "git": "Bir adrese git. Argüman: url",
    "tikla": "Numaralı bir öğeye tıkla. Argüman: index",
    "yaz": "Numaralı bir alana metin yaz. Argüman: index, text",
    "tus": "Tuşa bas; değiştirici serbest. Örnek: enter, tab, ctrl+end, "
           "ctrl+home, ctrl+arrowdown, shift+tab. Argüman: key",
    "kaydir": "Sayfayı kaydır. Argüman: dy (piksel, negatif = yukarı)",
    "oku": "Sayfadan veri oku; modele metin olarak döner. Argüman: yok",
    "bekle": "Sayfanın oturmasını bekle. Argüman: seconds (tavan 10)",
}


@dataclass
class Action:
    type: str
    index: int | None = None
    text: str = ""
    url: str = ""
    key: str = ""
    dy: int = 0
    seconds: float = 1.0

    @classmethod
    def parse(cls, raw: dict[str, Any]) -> "Action":
        kind = str(raw.get("type") or "").strip().lower()
        if kind not in ACTIONS:
            raise BrowserError(
                f"Bilinmeyen eylem: {kind!r}. Geçerli olanlar: {', '.join(ACTIONS)}"
            )
        return cls(
            type=kind,
            index=int(raw["index"]) if raw.get("index") is not None else None,
            text=str(raw.get("text") or ""),
            url=str(raw.get("url") or ""),
            key=str(raw.get("key") or ""),
            dy=int(raw.get("dy") or 0),
            seconds=min(float(raw.get("seconds") or 1.0), 10.0),
        )

    def describe(self) -> str:
        if self.type == "git":
            return f"Adrese git: {self.url}"
        if self.type == "tikla":
            return f"Tıkla: [{self.index}]"
        if self.type == "yaz":
            return f"Yaz: [{self.index}] ← {self.text!r}"
        if self.type == "tus":
            return f"Tuş: {self.key}"
        if self.type == "kaydir":
            return f"Kaydır: {self.dy}px"
        if self.type == "bekle":
            return f"Bekle: {self.seconds} sn"
        return "Sayfayı oku"

    def is_irreversible(self, state: PageState | None = None) -> bool:
        """Bu eylem geri alınamaz mı? Onay bunun üstüne kuruluyor.

        Tıklamada karar öğenin ETİKETİNE bakıyor: "Gönder" düğmesine tıklamak
        geri alınamaz, "Gelen Kutusu" bağlantısına tıklamak değil.
        """
        if self.type in ("oku", "bekle", "kaydir"):
            return False
        if self.type == "tikla" and state is not None and self.index is not None:
            target = next((e for e in state.elements if e.index == self.index), None)
            label = (target.text if target else "").casefold()
            return any(word in label for word in IRREVERSIBLE_WORDS)
        return False


def normalize_host(entry: str) -> str:
    """Kullanıcının yazdığını alan adına indir.

    Kullanıcılar kutuya tam URL yapıştırıyor
    (`https://mail.google.com/mail/u/1/#inbox`). Ham hâliyle saklanınca
    `host_allowed` hiçbir zaman eşleşmiyor ve otomasyon "izinli değil"
    diyip duruyor — canlı yaşandı. Bu yüzden kayıt sırasında normalleştiriyoruz.
    """
    entry = (entry or "").strip().casefold()
    if not entry:
        return ""
    if "://" in entry:
        entry = urlparse(entry).hostname or ""
    else:
        # "mail.google.com/mail/u/0" gibi şemasız yollar
        entry = entry.split("/", 1)[0]
    return entry.lstrip("*.").strip()


def host_allowed(url: str, allowlist: list[str]) -> bool:
    """Adres otomasyonun beyaz listesinde mi?

    Boş liste "her yer serbest" DEĞİL, "hiçbir yer" demek: bir otomasyon
    tanımlanırken hangi sitelerde çalışacağı açıkça yazılmalı. Sessiz bir
    varsayılan, ajanın bir gün bambaşka bir sitede tıklaması demektir.
    """
    if not allowlist:
        return False
    host = (urlparse(url).hostname or "").casefold()
    if not host:
        return False
    for allowed in allowlist:
        allowed = normalize_host(allowed)
        if not allowed:
            continue
        if host == allowed or host.endswith("." + allowed):
            return True
    return False


async def run_action(
    session: BrowserSession, action: Action, *, allowlist: list[str]
) -> str:
    """Eylemi uygula, kullanıcıya gösterilecek sonucu döndür."""
    if action.type == "git":
        if not host_allowed(action.url, allowlist):
            raise BlockedHost(urlparse(action.url).hostname or "", action.url, allowlist)
        await session.send("Page.navigate", {"url": action.url})
        await asyncio.sleep(1.2)
        return f"Gidildi: {action.url}"

    if action.type == "bekle":
        await asyncio.sleep(action.seconds)
        return f"{action.seconds} sn beklendi"

    if action.type == "kaydir":
        await session.evaluate(f"window.scrollBy(0, {action.dy})")
        await asyncio.sleep(0.3)
        return f"{action.dy}px kaydırıldı"

    if action.type == "oku":
        state = await session.state()
        return state.text[:3000] or "(sayfada metin yok)"

    if action.type == "tus":
        await _press(session, action.key)
        await asyncio.sleep(0.5)
        return f"{action.key} tuşuna basıldı"

    # Buradan sonrası öğe hedefliyor: indeks her seferinde YENİDEN çözülüyor,
    # çünkü sayfa bir önceki adımda değişmiş olabilir.
    state = await session.state()
    target = next((e for e in state.elements if e.index == action.index), None)
    if target is None:
        raise BrowserError(
            f"[{action.index}] numaralı öğe sayfada yok. Sayfa değişmiş olabilir."
        )

    if action.type == "tikla":
        for kind in ("mousePressed", "mouseReleased"):
            await session.send("Input.dispatchMouseEvent", {
                "type": kind, "x": target.x, "y": target.y,
                "button": "left", "clickCount": 1,
            })
        await asyncio.sleep(0.9)
        return f"Tıklandı: {target.text or target.tag}"

    if action.type == "yaz":
        for kind in ("mousePressed", "mouseReleased"):
            await session.send("Input.dispatchMouseEvent", {
                "type": kind, "x": target.x, "y": target.y,
                "button": "left", "clickCount": 3,  # 3 tıklama = içeriği seç
            })
        await asyncio.sleep(0.2)
        for char in action.text:
            await session.send("Input.dispatchKeyEvent", {"type": "char", "text": char})
        await asyncio.sleep(0.3)
        return f"Yazıldı: {action.text!r} → {target.text or target.tag}"

    raise BrowserError(f"Uygulanamayan eylem: {action.type}")


# Tarayıcının beklediği tuş kodları. Liste kapalı tutuluyor ki model "tuş"
# adı altında bir şey uydurmasın.
_KEYS = {
    "enter": ("Enter", 13), "tab": ("Tab", 9), "escape": ("Escape", 27),
    "backspace": ("Backspace", 8), "delete": ("Delete", 46), "space": (" ", 32),
    "arrowdown": ("ArrowDown", 40), "arrowup": ("ArrowUp", 38),
    "arrowleft": ("ArrowLeft", 37), "arrowright": ("ArrowRight", 39),
    "home": ("Home", 36), "end": ("End", 35), "pagedown": ("PageDown", 34),
    "pageup": ("PageUp", 33),
    "a": ("a", 65), "c": ("c", 67), "v": ("v", 86), "z": ("z", 90),
}

# CDP'nin değiştirici bit maskesi: Alt=1, Ctrl=2, Meta=4, Shift=8.
_MODIFIERS = {"alt": 1, "ctrl": 2, "control": 2, "meta": 4, "cmd": 4, "shift": 8}


def parse_key(combo: str) -> tuple[str, int, int]:
    """`"ctrl+end"` -> ("End", 35, 2). Bilinmeyen tuşta hata.

    Değiştirici desteği Google Sheets için ŞART: hücre ızgarası canvas
    tabanlı, yani `kaydir` orada işe yaramıyor. Canlı görüldü — model
    tabloda "10000px aşağı in" dedi ve hiçbir şey olmadı. Doğru yol
    `ctrl+end` ile son dolu hücreye atlamak.
    """
    parts = [part.strip().casefold() for part in combo.split("+") if part.strip()]
    if not parts:
        raise BrowserError("Boş tuş.")
    mask = 0
    for part in parts[:-1]:
        if part not in _MODIFIERS:
            raise BrowserError(f"Bilinmeyen değiştirici: {part!r}")
        mask |= _MODIFIERS[part]
    entry = _KEYS.get(parts[-1])
    if entry is None:
        raise BrowserError(
            f"Desteklenmeyen tuş: {parts[-1]!r}. Geçerli: {', '.join(_KEYS)}"
        )
    name, code = entry
    return name, code, mask


async def _press(session: BrowserSession, key: str) -> None:
    name, code, modifiers = parse_key(key)
    for kind in ("keyDown", "keyUp"):
        payload: dict[str, Any] = {
            "type": kind, "key": name, "modifiers": modifiers,
            "windowsVirtualKeyCode": code, "nativeVirtualKeyCode": code,
        }
        # Metin üreten tuşlarda `text` gerekiyor; değiştirici basılıyken
        # ÜRETMİYOR (ctrl+a bir "a" yazmamalı).
        if kind == "keyDown" and not modifiers:
            if name == "Enter":
                payload["text"] = "\r"
            elif len(name) == 1:
                payload["text"] = name
        await session.send("Input.dispatchKeyEvent", payload)
