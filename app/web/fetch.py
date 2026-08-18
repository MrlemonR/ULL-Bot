"""Bir adresi getir ve okunur metne çevir — SSRF savunmasıyla.

Neden savunma gerekiyor: getirilecek adresi **model** seçiyor ve model,
az önce okuduğu bir sayfanın içeriğinden etkilenmiş olabiliyor. Yani
"şu adresi oku" kararı dolaylı olarak bir yabancının etkisi altında.
Savunmasız bırakılsaydı bir sayfa modele `http://169.254.169.254/...`
(bulut metadata servisi) ya da `http://127.0.0.1:8080/api/mail/messages`
(bizim kendi API'miz, kullanıcının maili) okutabilirdi.

Bu yüzden: yalnızca http/https, yalnızca genel (public) IP'ler, her
yönlendirme adımı yeniden kontrol ediliyor.
"""

from __future__ import annotations

import ipaddress
import re
import socket
from dataclasses import dataclass
from urllib.parse import urlparse

import httpx

USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)

TIMEOUT = 20.0
MAX_BYTES = 2_000_000     # 2 MB'tan büyük sayfayı okumuyoruz
MAX_REDIRECTS = 5

# Metne çevirirken tamamen atılacak bloklar.
_DROP = re.compile(
    r"<(script|style|noscript|svg|head|nav|footer|form|iframe)\b.*?</\1>",
    re.IGNORECASE | re.DOTALL,
)
_BLOCK_BREAK = re.compile(
    r"</?(p|div|br|tr|li|h[1-6]|table|section|article|blockquote|hr)\b[^>]*>",
    re.IGNORECASE,
)
_TAG = re.compile(r"<[^>]+>")
_SPACES = re.compile(r"[ \t\r\f\v]+")
_BLANKS = re.compile(r"\n{3,}")
_TITLE = re.compile(r"<title[^>]*>(.*?)</title>", re.IGNORECASE | re.DOTALL)
# `scheme:` öneki — `://` aramaktan daha güvenilir (bkz. `guard_url`).
_SCHEME = re.compile(r"^([a-zA-Z][a-zA-Z0-9+.\-]*):")


class FetchError(RuntimeError):
    """Kullanıcıya/modele gösterilebilir getirme hatası."""


@dataclass
class Page:
    url: str
    title: str
    text: str
    truncated: bool = False


def _is_public_address(host: str) -> tuple[bool, str]:
    """Bu ana bilgisayar genel internette mi? (SSRF kapısı)"""
    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror as exc:
        return False, f"adres çözülemedi: {exc}"

    for info in infos:
        raw = info[4][0]
        try:
            address = ipaddress.ip_address(raw)
        except ValueError:
            return False, f"anlaşılmayan IP: {raw}"
        if (
            address.is_private
            or address.is_loopback
            or address.is_link_local
            or address.is_reserved
            or address.is_multicast
            or address.is_unspecified
        ):
            return False, (
                f"{host} özel/yerel bir adrese ({raw}) çözülüyor — "
                "yerel ağ ve makine içi servisler okunamaz."
            )
    return True, ""


def guard_url(url: str) -> str:
    """Adresi doğrula, normalize et. Uygun değilse `FetchError`."""
    raw = (url or "").strip()
    if not raw:
        raise FetchError("Boş adres.")

    # Şema varsa ONA bak; yoksa https varsay. Bu sıralama önemli:
    # `javascript:alert(1)` içinde "://" yok, körü körüne https eklenseydi
    # `https://javascript:alert(1)` olur ve şema denetiminden geçerdi
    # (sonra DNS'te düşerdi ama sebebi anlaşılmaz bir hata olurdu).
    scheme_match = _SCHEME.match(raw)
    if scheme_match:
        scheme = scheme_match.group(1).lower()
        if scheme not in ("http", "https"):
            raise FetchError(f"Yalnızca http/https okunabilir (verilen: {scheme}).")
    else:
        raw = f"https://{raw}"

    parsed = urlparse(raw)
    if parsed.scheme not in ("http", "https"):
        raise FetchError(f"Yalnızca http/https okunabilir (verilen: {parsed.scheme or '?'}).")
    if not parsed.hostname:
        raise FetchError(f"Adreste alan adı yok: {raw[:120]}")

    ok, reason = _is_public_address(parsed.hostname)
    if not ok:
        raise FetchError(f"Bu adres getirilemez — {reason}")
    return raw


def html_to_text(html: str) -> str:
    """Kaba ama bağımlılıksız HTML → okunur metin."""
    import html as html_module

    text = _DROP.sub(" ", html)
    text = _BLOCK_BREAK.sub("\n", text)
    text = _TAG.sub(" ", text)
    text = html_module.unescape(text)
    text = text.replace("\xa0", " ").replace("​", "")
    text = _SPACES.sub(" ", text)
    text = "\n".join(line.strip() for line in text.split("\n"))
    return _BLANKS.sub("\n\n", text).strip()


def fetch(url: str, *, max_chars: int = 6000) -> Page:
    """Sayfayı getir ve metnini döndür. **Bloklayıcı** (httpx senkron)."""
    target = guard_url(url)

    try:
        with httpx.Client(
            timeout=TIMEOUT,
            follow_redirects=False,
            headers={"User-Agent": USER_AGENT, "Accept-Language": "tr,en;q=0.8"},
        ) as client:
            for _ in range(MAX_REDIRECTS):
                response = client.get(target)
                if response.status_code in (301, 302, 303, 307, 308):
                    location = response.headers.get("location", "")
                    if not location:
                        raise FetchError(f"Yönlendirme adresi yok (HTTP {response.status_code}).")
                    # HER yönlendirme adımı yeniden denetleniyor: açık bir
                    # yönlendirme (open redirect) SSRF kapısını atlamasın.
                    target = guard_url(httpx.URL(target).join(location).__str__())
                    continue
                break
            else:
                raise FetchError("Çok fazla yönlendirme.")
    except httpx.HTTPError as exc:
        raise FetchError(f"Sayfa getirilemedi: {exc}") from exc

    if response.status_code >= 400:
        raise FetchError(f"Sunucu {response.status_code} döndürdü: {target}")

    content_type = response.headers.get("content-type", "")
    if not any(kind in content_type for kind in ("text/html", "text/plain", "application/xhtml")):
        raise FetchError(
            f"Bu içerik türü metne çevrilemiyor: {content_type or 'bilinmiyor'} ({target})"
        )

    raw = response.content[:MAX_BYTES]
    body = raw.decode(response.encoding or "utf-8", errors="replace")

    title_match = _TITLE.search(body)
    title = html_to_text(title_match.group(1)) if title_match else ""
    text = html_to_text(body)

    truncated = len(text) > max_chars
    return Page(url=target, title=title[:200], text=text[:max_chars], truncated=truncated)
