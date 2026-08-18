"""Web araması — sırayla denenen dört yol, hepsi ücretsiz.

1. **SearXNG** — `SEARXNG_URL` doluysa ÖNCE bu. Kendi makinende çalışan
   meta-arama: kota yok, anahtar yok, sorgu üçüncü tarafa gitmiyor.
2. **Tavily** — `TAVILY_API_KEY` doluysa ikinci. Ayda 1000 kredi, her ay
   yenileniyor, kayıtta kart istemiyor.
3. **Brave Search API** — `BRAVE_API_KEY` doluysa üçüncü. Ücretsiz katmanı
   ayda 2000 sorgu ama kayıtta kredi kartı isteyebiliyor (kullanıcının
   kartı reddedildi, o yüzden artık ilk sırada değil).
4. **DuckDuckGo kazıma** — anahtarsız son çare. Hiçbir kurulum istemiyor,
   projenin "ücretsiz katmanla çalış" ilkesine (spec §1) uyuyor.

Sıra bilinçli: önce hiçbir kotaya dokunmayan yerel yol, sonra kotalı ama
güvenilir API'ler, en son engellenmeye açık kazıma. Bir yol sonuç
vermezse (hata ya da boş liste) sessizce bir sonrakine geçiliyor —
sağlayıcı seçimi kullanıcının işi değil, aramanın çalışması onun işi.

**Kazımanın bedeli canlı ölçüldü ve küçük değil:** DDG iki hızlı sorgudan
sonra HTTP **202** + "anomaly" sayfası döndürüyor; Mojeek doğrudan Captcha
veriyor. 202 bir hata kodu olmadığı için ilk sürüm bunu "sonuç yok"
sanıyordu ve model aramayı tekrarlıyordu — her tekrar engeli sıkıştırıyor.

Bu yüzden şu savunmalar var: kendi kendimize 2.5 sn aralık, 5 dakikalık
sorgu önbelleği, hız sınırı tespiti (202/429/captcha/anomaly), bir kez
otomatik bekle-tekrar dene, ve iki farklı DDG uç noktası. Hiçbiri
tutmazsa **hata fırlatılıyor** — sessizce boş liste dönmek modelin
uydurmaya başlaması demek.
"""

from __future__ import annotations

import re
import threading
import time
from dataclasses import dataclass
from html import unescape
from urllib.parse import parse_qs, unquote, urlparse

import httpx

from app.settings import settings
from app.web.fetch import USER_AGENT, html_to_text

TIMEOUT = 20.0
# DDG'nin hız sınırı canlı ölçüldü: iki hızlı sorgudan sonra sonuçsuz bir
# "anomaly" sayfası dönüyor. Kendimizi sınırlamak, engellenip sonra hiç
# arama yapamamaktan iyi.
MIN_INTERVAL = 2.5
BACKOFF = 20
CACHE_TTL = 300

_lock = threading.Lock()
_last_request = 0.0
# (sorgu, bölge, limit) -> (zaman, sonuçlar)
_cache: dict[tuple[str, str, int], tuple[float, list["Result"]]] = {}

HTML_ENDPOINT = "https://html.duckduckgo.com/html/"
LITE_ENDPOINT = "https://lite.duckduckgo.com/lite/"

# html.duckduckgo.com biçimi: <a class="result__a" href="...">başlık</a>
_HTML_RESULT = re.compile(
    r'<a[^>]+class="[^"]*result__a[^"]*"[^>]+href="(?P<url>[^"]+)"[^>]*>(?P<title>.*?)</a>'
    # Bitiş koşuluna `$` de dahil: sayfa `</body>` ile bitmiyorsa (ya da
    # kırpılmışsa) SON sonuç hiç eşleşmiyordu.
    r'(?P<rest>.*?)(?=<a[^>]+class="[^"]*result__a|</body>|$)',
    re.IGNORECASE | re.DOTALL,
)
_SNIPPET = re.compile(
    r'class="[^"]*result__snippet[^"]*"[^>]*>(?P<snippet>.*?)</a>',
    re.IGNORECASE | re.DOTALL,
)
# lite.duckduckgo.com biçimi: düz tablo, bağlantılar `result-link` sınıfında
_LITE_RESULT = re.compile(
    r'<a[^>]+class="[^"]*result-link[^"]*"[^>]+href="(?P<url>[^"]+)"[^>]*>(?P<title>.*?)</a>',
    re.IGNORECASE | re.DOTALL,
)


class SearchError(RuntimeError):
    """Kullanıcıya/modele gösterilebilir arama hatası."""


@dataclass
class Result:
    title: str
    url: str
    snippet: str = ""

    def as_dict(self) -> dict[str, str]:
        return {"title": self.title, "url": self.url, "snippet": self.snippet}


def _clean_url(raw: str) -> str:
    """DDG bağlantıları `/l/?uddg=<gerçek adres>` şeklinde sarmalıyor."""
    value = unescape(raw).strip()
    if value.startswith("//"):
        value = f"https:{value}"
    parsed = urlparse(value)
    if "duckduckgo.com" in (parsed.netloc or "") and parsed.path.startswith("/l/"):
        target = parse_qs(parsed.query).get("uddg", [""])[0]
        if target:
            return unquote(target)
    return value


def _clean_text(raw: str) -> str:
    return html_to_text(raw)[:300]


def _parse_html(body: str, limit: int) -> list[Result]:
    results: list[Result] = []
    for match in _HTML_RESULT.finditer(body):
        url = _clean_url(match.group("url"))
        if not url.startswith("http"):
            continue
        snippet_match = _SNIPPET.search(match.group("rest") or "")
        results.append(
            Result(
                title=_clean_text(match.group("title")),
                url=url,
                snippet=_clean_text(snippet_match.group("snippet")) if snippet_match else "",
            )
        )
        if len(results) >= limit:
            break
    return results


def _parse_lite(body: str, limit: int) -> list[Result]:
    results: list[Result] = []
    for match in _LITE_RESULT.finditer(body):
        url = _clean_url(match.group("url"))
        if not url.startswith("http"):
            continue
        results.append(Result(title=_clean_text(match.group("title")), url=url))
        if len(results) >= limit:
            break
    return results


def _looks_throttled(response: httpx.Response) -> bool:
    """DDG hız sınırı sayfası mı?

    Canlı ölçüldü: iki hızlı sorgudan sonra DDG **HTTP 202** ve içinde
    "anomaly" geçen, sonuçsuz bir sayfa döndürüyor. 202 bir hata kodu
    olmadığı için bu sessizce "sonuç yok" gibi görünüyordu ve model
    "demek ki sonuç yok" deyip aramayı tekrar tekrar deniyordu.
    """
    if response.status_code in (202, 429):
        return True
    lowered = response.text[:4000].lower()
    return any(mark in lowered for mark in ("anomaly", "captcha", "unusual traffic", "<title>captcha"))


def _throttle() -> None:
    """Ardışık aramalar arasında en az `MIN_INTERVAL` saniye bırak."""
    global _last_request
    with _lock:
        wait = _last_request + MIN_INTERVAL - time.monotonic()
        if wait > 0:
            time.sleep(wait)
        _last_request = time.monotonic()


TAVILY_ENDPOINT = "https://api.tavily.com/search"


def _searxng(query: str, limit: int, region: str) -> list[Result]:
    """Kendi makinende calisan SearXNG — kota yok, anahtar yok.

    SearXNG bir meta-arama: sorguyu Google/Bing/Startpage/Mojeek gibi bircok
    motora paralel soruyor. Bu makinenin IP'si DDG tarafindan engellendi ama
    SearXNG tek motora bagli olmadigi icin bir motorun dusmesi aramayi
    bitirmiyor — asil kazanci bu.

    JSON bicimi SearXNG'de VARSAYILAN OLARAK KAPALI; `settings.yml` icinde
    `search.formats` listesine `json` eklenmemisse HTML donuyor ve burasi
    `ValueError` alip bir sonraki saglayiciya gecer.
    """
    base = settings.searxng_url.strip().rstrip("/")
    if not base:
        return []
    response = httpx.get(
        f"{base}/search",
        params={
            "q": query,
            "format": "json",
            # SearXNG dil kodu bekliyor ("tr-TR" degil "tr"); bolge kodunun
            # ilk parcasi bunu veriyor.
            "language": region.split("-")[0] or "tr",
        },
        headers={"Accept": "application/json"},
        timeout=TIMEOUT,
    )
    if response.status_code >= 400:
        raise SearchError(f"SearXNG {response.status_code}: {response.text[:150]}")
    payload = response.json().get("results", []) or []
    return [
        Result(
            title=_clean_text(item.get("title", "")),
            url=item.get("url", ""),
            snippet=_clean_text(item.get("content", "")),
        )
        for item in payload[:limit]
        if item.get("url", "").startswith("http")
    ]


def _tavily(query: str, limit: int, region: str) -> list[Result]:
    """Tavily — ayda 1000 kredi, kart istemiyor.

    Ajanlar icin tasarlanmis: snippet'leri (`content`) DDG'ninkinden cok daha
    uzun geliyor, yani model her sonuc icin ayrica `fetch_url` cagirmak
    zorunda kalmiyor. Bu hem tur sayisini hem de kisir donguye girme riskini
    dusuruyor.
    """
    key = settings.tavily_api_key.strip()
    if not key:
        return []
    response = httpx.post(
        TAVILY_ENDPOINT,
        json={
            "query": query,
            "max_results": min(limit, 20),
            "search_depth": "basic",  # 1 kredi; "advanced" 2 kredi eder
        },
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
        timeout=TIMEOUT,
    )
    if response.status_code >= 400:
        raise SearchError(f"Tavily {response.status_code}: {response.text[:150]}")
    payload = response.json().get("results", []) or []
    return [
        Result(
            title=_clean_text(item.get("title", "")),
            url=item.get("url", ""),
            snippet=_clean_text(item.get("content", "")),
        )
        for item in payload[:limit]
        if item.get("url", "").startswith("http")
    ]


BRAVE_ENDPOINT = "https://api.search.brave.com/res/v1/web/search"


def _brave(query: str, limit: int, region: str) -> list[Result]:
    """Brave Search API — anahtar varsa denenir.

    Neden: kazima (DDG/Mojeek) ucretsiz ama motorlar sik istekte captcha
    ya da "anomaly" sayfasi donduruyor ve arastirma tam ortasinda kesiliyor.
    Brave'in ucretsiz katmani ayda 2000 sorgu; ancak kayit sirasinda kredi
    karti isteyebiliyor. Anahtar yoksa hicbir sey degismiyor, sonraki yol
    denenmeye devam ediyor.
    """
    key = settings.brave_api_key.strip()
    if not key:
        return []
    response = httpx.get(
        BRAVE_ENDPOINT,
        params={"q": query, "count": min(limit, 20),
                "country": region.split("-")[-1].upper() or "TR"},
        headers={"Accept": "application/json", "X-Subscription-Token": key},
        timeout=TIMEOUT,
    )
    if response.status_code >= 400:
        raise SearchError(f"Brave API {response.status_code}: {response.text[:150]}")
    payload = response.json().get("web", {}).get("results", []) or []
    return [
        Result(
            title=_clean_text(item.get("title", "")),
            url=item.get("url", ""),
            snippet=_clean_text(item.get("description", "")),
        )
        for item in payload[:limit]
        if item.get("url", "").startswith("http")
    ]


def search(query: str, *, limit: int = 8, region: str = "tr-tr", _retry: bool = True) -> list[Result]:
    """Web'de ara. **Bloklayıcı** (httpx senkron) — thread'de çağrılmalı.

    `region` varsayılanı Türkçe: kullanıcı Türkçe soruyor ve "4000 TL"
    gibi sorgular yerel sonuç istiyor.

    Aynı sorgu kısa sürede tekrar sorulursa önbellekten dönüyor: model bir
    araştırma turunda benzer sorguları tekrarlama eğiliminde ve her tekrar
    bizi hız sınırına bir adım yaklaştırıyor.
    """
    text = (query or "").strip()
    if not text:
        raise SearchError("Boş arama sorgusu.")

    key = (text.lower(), region, limit)
    cached = _cache.get(key)
    if cached and time.monotonic() - cached[0] < CACHE_TTL:
        return cached[1]

    # Yapilandirilmis saglayicilar, sirayla. Her biri anahtari/adresi yoksa
    # bos liste donuyor, o yuzden ayrica kontrol etmiyoruz. Biri patlarsa
    # (kota, ag, JSON yerine HTML) sessizce sonrakine geciliyor — arama
    # tamamen olmesin diye.
    for provider in (_searxng, _tavily, _brave):
        try:
            results = provider(text, limit, region)
        except (httpx.HTTPError, SearchError, ValueError):
            continue
        if results:
            _cache[key] = (time.monotonic(), results)
            return results

    attempts = [
        (HTML_ENDPOINT, _parse_html),
        (LITE_ENDPOINT, _parse_lite),
    ]
    errors: list[str] = []
    throttled = False

    for endpoint, parser in attempts:
        _throttle()
        try:
            response = httpx.post(
                endpoint,
                data={"q": text, "kl": region},
                headers={"User-Agent": USER_AGENT, "Accept-Language": "tr,en;q=0.8"},
                timeout=TIMEOUT,
                follow_redirects=True,
            )
        except httpx.HTTPError as exc:
            errors.append(f"{endpoint}: {exc}")
            continue

        if _looks_throttled(response):
            throttled = True
            errors.append(f"{endpoint}: hız sınırı (HTTP {response.status_code})")
            continue
        if response.status_code >= 400:
            errors.append(f"{endpoint}: HTTP {response.status_code}")
            continue

        results = parser(response.text, limit)
        if results:
            _cache[key] = (time.monotonic(), results)
            return results
        errors.append(f"{endpoint}: sonuç ayrıştırılamadı")

    if throttled and _retry:
        # Gecici bir engel; beklemek genellikle cozuyor. Modelden beklemesini
        # istemek yerine burada bekliyoruz — model bekleyemez, tekrar dener
        # ve her tekrar engeli sikilastirir.
        time.sleep(BACKOFF)
        return search(text, limit=limit, region=region, _retry=False)

    if throttled:
        # Modelin bunu "sonuç yok" sanıp aramayı tekrarlaması en kötü
        # sonuç — tekrar her denemede sınırı daha da sıkıyor. Ne olduğunu
        # ve ne YAPMAMASI gerektiğini açıkça söylüyoruz.
        raise SearchError(
            "Arama motoru çok sık istek nedeniyle geçici olarak engelledi. "
            f"{int(BACKOFF)} saniye bekleyip TEK bir kez daha dene; aynı sorguyu "
            "art arda tekrarlama. Elindeki mevcut sonuçlarla devam edebilirsin."
        )
    raise SearchError("Arama sonuç vermedi. Denenenler — " + "; ".join(errors))
