"""Web araştırma katmanı: arama, sayfa okuma, SSRF savunması (Faz 9).

Ağa çıkmıyoruz — `httpx` sahtelendi. Test edilen şey bizim tarafımız:
DDG cevabının ayrıştırılması, yönlendirme sarmalayıcısının açılması ve
**en önemlisi** SSRF kapısı.

SSRF neden bu kadar önemli: getirilecek adresi MODEL seçiyor ve model,
az önce okuduğu bir sayfanın metninden etkilenmiş olabiliyor. Yani karar
dolaylı olarak bir yabancının etkisi altında. Kapı olmasaydı bir sayfa
modele `http://127.0.0.1:8080/api/mail/messages` okutup kullanıcının
maillerini kendi çıktısına taşıyabilirdi.
"""

from __future__ import annotations

import pytest

from app.web import fetch as fetch_module
from app.web import search as search_module
from app.web.fetch import FetchError, guard_url, html_to_text
from app.web.search import SearchError


@pytest.fixture(autouse=True)
def _temiz_arama_durumu(monkeypatch):
    """Arama modülü süreç ömrü boyunca durum tutuyor (önbellek + son istek
    zamanı). Testler arasında taşınırsa biri diğerinin sonucunu görür ve
    hatalar rastgele görünür. Her testte sıfırlanıyor; bekleme de kapalı
    çünkü testler ağa çıkmıyor."""
    search_module._cache.clear()
    monkeypatch.setattr(search_module, "MIN_INTERVAL", 0)
    monkeypatch.setattr(search_module, "BACKOFF", 0)
    monkeypatch.setattr(search_module, "_last_request", 0.0)
    # Sağlayıcı ayarları geliştiricinin `.env`'inde dolu olabilir; testler
    # ona göre farklı davranmamalı (bkz. conftest'teki aynı ilke).
    from app.settings import settings

    monkeypatch.setattr(settings, "searxng_url", "")
    monkeypatch.setattr(settings, "tavily_api_key", "")
    monkeypatch.setattr(settings, "brave_api_key", "")
    yield
    search_module._cache.clear()


# --- SSRF kapısı ------------------------------------------------------------


@pytest.mark.parametrize(
    "url",
    [
        "http://localhost:8080/api/config",
        "http://127.0.0.1/",
        "http://127.0.0.1:8080/api/mail/messages",
        "http://0.0.0.0/",
        "http://169.254.169.254/latest/meta-data/",   # bulut metadata
        "http://192.168.1.1/",
        "http://10.0.0.5/",
        "http://172.16.0.1/",
        "http://[::1]/",
    ],
)
def test_yerel_ve_ozel_adresler_engelleniyor(url):
    with pytest.raises(FetchError):
        guard_url(url)


@pytest.mark.parametrize(
    "url",
    ["file:///etc/passwd", "ftp://example.com/x", "gopher://x", "javascript:alert(1)"],
)
def test_http_disi_semalar_engelleniyor(url):
    with pytest.raises(FetchError, match="http"):
        guard_url(url)


def test_bos_adres_reddedilir():
    with pytest.raises(FetchError):
        guard_url("")


def test_genel_adres_gecer(monkeypatch):
    monkeypatch.setattr(
        fetch_module.socket, "getaddrinfo",
        lambda host, port: [(2, 1, 6, "", ("93.184.216.34", 0))],
    )
    assert guard_url("example.com") == "https://example.com"
    assert guard_url("http://example.com/a?b=1") == "http://example.com/a?b=1"


def test_cozulemeyen_alan_adi_reddedilir(monkeypatch):
    import socket as socket_module

    def boom(*args, **kwargs):
        raise socket_module.gaierror("bilinmeyen ana bilgisayar")

    monkeypatch.setattr(fetch_module.socket, "getaddrinfo", boom)
    with pytest.raises(FetchError, match="çözülemedi"):
        guard_url("https://yok.invalid")


def test_alan_adi_yerel_ipye_cozulurse_engellenir(monkeypatch):
    """DNS rebinding: alan adı genel görünür ama 127.0.0.1'e çözülür."""
    monkeypatch.setattr(
        fetch_module.socket, "getaddrinfo",
        lambda host, port: [(2, 1, 6, "", ("127.0.0.1", 0))],
    )
    with pytest.raises(FetchError, match="özel/yerel"):
        guard_url("https://kotu-site.example")


# --- HTML → metin -----------------------------------------------------------


def test_script_ve_style_atiliyor():
    text = html_to_text(
        "<html><head><style>p{color:red}</style></head>"
        "<body><script>alert(1)</script><p>Görünen metin</p></body></html>"
    )
    assert "Görünen metin" in text
    assert "alert" not in text
    assert "color:red" not in text


def test_blok_etiketleri_satir_sonuna_cevriliyor():
    text = html_to_text("<p>Bir</p><p>İki</p>")
    assert "Bir" in text and "İki" in text
    assert text.index("Bir") < text.index("İki")


def test_html_varliklari_cozuluyor():
    assert "Ürün & Fiyat" in html_to_text("<p>&Uuml;r&uuml;n &amp; Fiyat</p>")


# --- arama ayrıştırma -------------------------------------------------------


class FakeResponse:
    def __init__(self, text: str, status_code: int = 200) -> None:
        self.text = text
        self.status_code = status_code


DDG_HTML = """
<div class="result">
  <a class="result__a" href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fwww.epey.com%2Fkulaklik%2F">
    Kulakl&#x131;k Fiyatlar&#x131;
  </a>
  <a class="result__snippet">En iyi kulakl&#x131;k modelleri ve fiyatlar&#x131;.</a>
</div>
<div class="result">
  <a class="result__a" href="https://versus.com/tr/headphone">Kar&#x15f;&#x131;la&#x15f;t&#x131;rma</a>
  <a class="result__snippet">Kulakl&#x131;klar&#x131; kar&#x15f;&#x131;la&#x15f;t&#x131;r.</a>
</div>
"""


def test_ddg_sonuclari_ayristiriliyor(monkeypatch):
    monkeypatch.setattr(search_module.httpx, "post", lambda *a, **k: FakeResponse(DDG_HTML))
    results = search_module.search("kulaklık", limit=5)
    assert len(results) == 2
    assert results[0].title.startswith("Kulaklık")
    assert results[0].snippet


def test_ddg_yonlendirme_sarmalayicisi_aciliyor(monkeypatch):
    """DDG bağlantıları `/l/?uddg=<gerçek adres>` ile sarılı gelir."""
    monkeypatch.setattr(search_module.httpx, "post", lambda *a, **k: FakeResponse(DDG_HTML))
    results = search_module.search("kulaklık")
    assert results[0].url == "https://www.epey.com/kulaklik/"
    assert "duckduckgo.com" not in results[0].url


def test_limit_uygulaniyor(monkeypatch):
    monkeypatch.setattr(search_module.httpx, "post", lambda *a, **k: FakeResponse(DDG_HTML))
    assert len(search_module.search("x", limit=1)) == 1


def test_bos_sorgu_reddedilir():
    with pytest.raises(SearchError):
        search_module.search("   ")


def test_ilk_uc_nokta_bosalirsa_ikincisi_deneniyor(monkeypatch):
    """`html` uç noktası boş dönerse `lite` denenmeli — biçim değişikliğine dayanıklılık."""
    calls = []
    lite = '<a class="result-link" href="https://ornek.com/a">Başlık</a>'

    def fake_post(url, **kwargs):
        calls.append(url)
        return FakeResponse("" if "html.duckduckgo" in url else lite)

    monkeypatch.setattr(search_module.httpx, "post", fake_post)
    results = search_module.search("x")
    assert len(calls) == 2
    assert results[0].url == "https://ornek.com/a"


def test_hepsi_basarisizsa_hata_firlatiyor(monkeypatch):
    """Sessizce boş liste DÖNMEMELİ — model 'sonuç yok' sanır ve uydurmaya başlar."""
    monkeypatch.setattr(search_module.httpx, "post", lambda *a, **k: FakeResponse("", 503))
    with pytest.raises(SearchError, match="Denenenler"):
        search_module.search("x")


# --- araçların sözleşmesi ---------------------------------------------------


def test_web_araclari_kayitli():
    from app.agent.tools import get_tool

    assert get_tool("web_search") is not None
    assert get_tool("fetch_url") is not None


def test_web_ciktisi_untrusted_isaretli(monkeypatch):
    """Web içeriği düşman girdidir — mailde olduğu gibi işaretlenmeli."""
    from pathlib import Path

    from app.agent.tools import get_tool
    from app.agent.tools.base import ToolContext
    from app.web.search import Result

    monkeypatch.setattr(
        search_module, "search",
        lambda *a, **k: [Result(title="T", url="https://x.example", snippet="S")],
    )
    monkeypatch.setattr(
        "app.agent.tools.web.search",
        lambda *a, **k: [Result(title="T", url="https://x.example", snippet="S")],
    )
    ctx = ToolContext(cwd=Path("/tmp"), session_id="t")
    result = get_tool("web_search").run(ctx, query="test")
    assert result.ok
    assert result.untrusted is True, "web araması çıktısı untrusted olmalı"


def test_bos_sorgu_arac_seviyesinde_blocked():
    from pathlib import Path

    from app.agent.tools import get_tool
    from app.agent.tools.base import ToolContext

    ctx = ToolContext(cwd=Path("/tmp"), session_id="t")
    assert get_tool("web_search").assess(ctx, query="").risk == "blocked"
    assert get_tool("fetch_url").assess(ctx, url="").risk == "blocked"


def test_ayni_arama_turda_tekrarlanmiyor(monkeypatch):
    """Aynı sorgu ikinci kez gelirse arama yapılmamalı, model uyarılmalı.

    Canlı yakalandı: zincirin sonundaki küçük yerel model (qwen2.5:3b)
    "4000 TL kulaklık önerisi" / "4000TL kulaklık önerisi" / "... öneri"
    diye 15 adım boyunca aynı şeyi aradı. Sorgular birebir aynı olmadığı
    için `AgentLoop._stuck_on` devreye girmiyordu ve kullanıcı hiç cevap
    alamadı.
    """
    from pathlib import Path as P

    from app.agent.tools import get_tool
    from app.agent.tools.base import ToolContext
    from app.web.search import Result

    calls: list[str] = []

    def fake_search(query, **kwargs):
        calls.append(query)
        return [Result(title="T", url="https://x.example", snippet="S")]

    monkeypatch.setattr("app.agent.tools.web.search", fake_search)
    tool = get_tool("web_search")
    ctx = ToolContext(cwd=P("/tmp"), session_id="t")

    assert tool.run(ctx, query="4000 TL kulaklık önerisi").ok

    for tekrar in ("4000TL kulaklık önerisi", "4000 TL kulaklık öneri"):
        result = tool.run(ctx, query=tekrar)
        assert not result.ok, f"{tekrar!r} tekrar sayılmalıydı"
        assert "zaten yaptın" in result.output
        assert "fetch_url" in result.output, "modele sıradaki adım söylenmeli"

    assert calls == ["4000 TL kulaklık önerisi"], "tekrarlar arama motoruna gitmemeli"


def test_gercekten_farkli_sorgu_engellenmiyor(monkeypatch):
    """Koruma araştırmayı öldürmemeli — yeni bir sorgu geçebilmeli."""
    from pathlib import Path as P

    from app.agent.tools import get_tool
    from app.agent.tools.base import ToolContext
    from app.web.search import Result

    monkeypatch.setattr(
        "app.agent.tools.web.search",
        lambda *a, **k: [Result(title="T", url="https://x.example", snippet="S")],
    )
    tool = get_tool("web_search")
    ctx = ToolContext(cwd=P("/tmp"), session_id="t")

    assert tool.run(ctx, query="4000 TL kulaklık önerisi").ok
    assert tool.run(ctx, query="Sony WH-1000XM5 özellikleri").ok
    assert tool.run(ctx, query="kafa üstü kulaklık karşılaştırma 2026").ok


def test_tekrar_hafizasi_tura_ozel():
    """Yeni tur yeni `ToolContext` demek — kullanıcı aynı şeyi sonra tekrar sorabilir."""
    from pathlib import Path as P

    from app.agent.tools.base import ToolContext

    birinci = ToolContext(cwd=P("/tmp"), session_id="t")
    birinci.searched.append("kulaklık")
    ikinci = ToolContext(cwd=P("/tmp"), session_id="t")
    assert ikinci.searched == [], "tekrar hafızası turlar arasında taşınmamalı"


# --- hız sınırı ve önbellek -------------------------------------------------


def test_ddg_hiz_siniri_sessizce_bos_donmuyor(monkeypatch):
    """DDG sınırı HTTP **202** ile bildiriyor — bir hata kodu değil.

    Canlı ölçüldü: iki hızlı sorgudan sonra 202 + "anomaly" sayfası
    geliyor. Bu "sonuç yok" gibi görünürse model aramayı tekrar tekrar
    dener ve her tekrar sınırı daha da sıkar. Hata mesajı bunu açıkça
    söylemeli.
    """
    monkeypatch.setattr(search_module, "MIN_INTERVAL", 0)
    monkeypatch.setattr(
        search_module.httpx, "post",
        lambda *a, **k: FakeResponse("<html>anomaly detected</html>", 202),
    )
    with pytest.raises(SearchError, match="engelledi"):
        search_module.search("kulaklık", limit=3)


def test_429_da_hiz_siniri_sayiliyor(monkeypatch):
    monkeypatch.setattr(search_module, "MIN_INTERVAL", 0)
    monkeypatch.setattr(
        search_module.httpx, "post", lambda *a, **k: FakeResponse("", 429)
    )
    with pytest.raises(SearchError, match="engelledi"):
        search_module.search("x")


def test_ayni_sorgu_onbellekten_doner(monkeypatch):
    """Model bir turda benzer sorguları tekrarlıyor; her tekrar sınıra bir adım."""
    monkeypatch.setattr(search_module, "MIN_INTERVAL", 0)
    calls = []

    def fake_post(*args, **kwargs):
        calls.append(1)
        return FakeResponse(DDG_HTML)

    monkeypatch.setattr(search_module.httpx, "post", fake_post)
    first = search_module.search("kulaklık", limit=5)
    second = search_module.search("Kulaklık", limit=5)   # yalnızca ilk harf farklı
    assert first == second
    assert len(calls) == 1, "ikinci çağrı önbellekten gelmeliydi"


def test_farkli_sorgu_onbelleğe_takilmaz(monkeypatch):
    monkeypatch.setattr(search_module, "MIN_INTERVAL", 0)
    calls = []
    monkeypatch.setattr(
        search_module.httpx, "post",
        lambda *a, **k: (calls.append(1), FakeResponse(DDG_HTML))[1],
    )
    search_module.search("kulaklık")
    search_module.search("mikrofon")
    assert len(calls) == 2


def test_aramalar_arasinda_bekleme_var(monkeypatch):
    """Kendimizi sınırlamak, engellenip hiç arama yapamamaktan iyi."""
    monkeypatch.setattr(search_module, "MIN_INTERVAL", 0.3)
    monkeypatch.setattr(search_module, "_last_request", 0.0)
    monkeypatch.setattr(search_module.httpx, "post", lambda *a, **k: FakeResponse(DDG_HTML))

    import time as time_module

    start = time_module.monotonic()
    search_module.search("bir")
    search_module.search("iki")
    assert time_module.monotonic() - start >= 0.3


# --- Brave API (isteğe bağlı yol) -------------------------------------------


def test_brave_anahtari_yoksa_kazimaya_dusuyor(monkeypatch):
    """Anahtar yoksa hiçbir şey değişmemeli — kurulumsuz yol çalışmaya devam."""
    from app.settings import settings

    monkeypatch.setattr(settings, "brave_api_key", "")
    monkeypatch.setattr(search_module.httpx, "post", lambda *a, **k: FakeResponse(DDG_HTML))
    monkeypatch.setattr(
        search_module.httpx, "get",
        lambda *a, **k: pytest.fail("anahtar yokken Brave çağrılmamalı"),
    )
    assert len(search_module.search("kulaklık")) == 2


def test_brave_anahtari_varsa_once_o_deneniyor(monkeypatch):
    from app.settings import settings

    monkeypatch.setattr(settings, "brave_api_key", "test-key")

    class BraveResponse:
        status_code = 200

        def json(self):
            return {"web": {"results": [
                {"title": "Epey", "url": "https://epey.com/x", "description": "özet"},
            ]}}

    monkeypatch.setattr(search_module.httpx, "get", lambda *a, **k: BraveResponse())
    monkeypatch.setattr(
        search_module.httpx, "post",
        lambda *a, **k: pytest.fail("Brave çalışırken kazımaya düşülmemeli"),
    )
    results = search_module.search("kulaklık")
    assert results[0].url == "https://epey.com/x"
    assert results[0].snippet == "özet"


def test_brave_duserse_kazima_devrede(monkeypatch):
    """Brave kotası dolarsa arama tamamen ölmemeli."""
    from app.settings import settings

    monkeypatch.setattr(settings, "brave_api_key", "test-key")
    monkeypatch.setattr(search_module.httpx, "get", lambda *a, **k: FakeResponse("kota doldu", 429))
    monkeypatch.setattr(search_module.httpx, "post", lambda *a, **k: FakeResponse(DDG_HTML))
    assert len(search_module.search("kulaklık")) == 2


# --- SearXNG + Tavily (isteğe bağlı yollar) ---------------------------------


class JsonResponse:
    """`json()` döndüren sahte cevap. `text` de var — hata mesajları onu kırpıyor."""

    def __init__(self, payload, status_code: int = 200) -> None:
        self._payload = payload
        self.status_code = status_code
        self.text = str(payload)

    def json(self):
        return self._payload


SEARXNG_JSON = {"results": [
    {"title": "Epey", "url": "https://epey.com/x", "content": "yerel özet"},
]}
TAVILY_JSON = {"results": [
    {"title": "Versus", "url": "https://versus.com/y", "content": "tavily özet"},
]}


def test_searxng_varsa_ilk_o_deneniyor(monkeypatch):
    """Yerel SearXNG hiçbir kotaya dokunmuyor — sıranın başında olmalı."""
    from app.settings import settings

    monkeypatch.setattr(settings, "searxng_url", "http://127.0.0.1:8888")
    monkeypatch.setattr(settings, "tavily_api_key", "tvly-test")
    monkeypatch.setattr(search_module.httpx, "get", lambda *a, **k: JsonResponse(SEARXNG_JSON))
    monkeypatch.setattr(
        search_module.httpx, "post",
        lambda *a, **k: pytest.fail("SearXNG çalışırken Tavily/kazıma denenmemeli"),
    )
    results = search_module.search("kulaklık")
    assert results[0].url == "https://epey.com/x"
    assert results[0].snippet == "yerel özet"


def test_searxng_sondaki_egik_cizgi_adresi_bozmuyor(monkeypatch):
    """`.env`e `http://127.0.0.1:8888/` yazmak sık oluyor; `//search` olmamalı."""
    from app.settings import settings

    monkeypatch.setattr(settings, "searxng_url", "http://127.0.0.1:8888/")
    seen: list[str] = []

    def fake_get(url, **kwargs):
        seen.append(url)
        return JsonResponse(SEARXNG_JSON)

    monkeypatch.setattr(search_module.httpx, "get", fake_get)
    search_module.search("kulaklık")
    assert seen == ["http://127.0.0.1:8888/search"]


def test_searxng_json_kapaliysa_tavily_devrede(monkeypatch):
    """JSON biçimi açılmamışsa SearXNG HTML döner ve `json()` patlar.

    Bu, kurulumdaki EN OLASI hata (SearXNG'de `search.formats` varsayılanı
    sadece `html`). Aramanın tamamen ölmemesi gerekiyor.
    """
    from app.settings import settings

    monkeypatch.setattr(settings, "searxng_url", "http://127.0.0.1:8888")
    monkeypatch.setattr(settings, "tavily_api_key", "tvly-test")

    def html_instead_of_json(*a, **k):
        response = JsonResponse(None)
        response.json = lambda: (_ for _ in ()).throw(ValueError("JSON değil"))
        return response

    monkeypatch.setattr(search_module.httpx, "get", html_instead_of_json)
    monkeypatch.setattr(search_module.httpx, "post", lambda *a, **k: JsonResponse(TAVILY_JSON))
    results = search_module.search("kulaklık")
    assert results[0].url == "https://versus.com/y"


def test_tavily_anahtari_yoksa_cagrilmiyor(monkeypatch):
    """Anahtarsızken Tavily uç noktasına istek gitmemeli — kazımaya düşmeli."""
    from app.settings import settings

    monkeypatch.setattr(settings, "tavily_api_key", "")
    posted: list[str] = []

    def fake_post(url, **kwargs):
        posted.append(url)
        return FakeResponse(DDG_HTML)

    monkeypatch.setattr(search_module.httpx, "post", fake_post)
    assert len(search_module.search("kulaklık")) == 2
    assert search_module.TAVILY_ENDPOINT not in posted


def test_tavily_duserse_kazima_devrede(monkeypatch):
    """Aylık kredi biterse arama tamamen ölmemeli."""
    from app.settings import settings

    monkeypatch.setattr(settings, "tavily_api_key", "tvly-test")

    def fake_post(url, **kwargs):
        if url == search_module.TAVILY_ENDPOINT:
            return FakeResponse("kredi bitti", 432)
        return FakeResponse(DDG_HTML)

    monkeypatch.setattr(search_module.httpx, "post", fake_post)
    assert len(search_module.search("kulaklık")) == 2


def test_captcha_sayfasi_hiz_siniri_sayiliyor(monkeypatch):
    """Mojeek captcha ile, DDG 202 ile engelliyor — ikisi de aynı sınıf."""
    monkeypatch.setattr(search_module, "BACKOFF", 0)
    monkeypatch.setattr(
        search_module.httpx, "post",
        lambda *a, **k: FakeResponse("<html><head><title>Captcha</title></head></html>", 200),
    )
    with pytest.raises(SearchError, match="engelledi"):
        search_module.search("x")


def test_hiz_siniri_sonrasi_bir_kez_otomatik_tekrar_deneniyor(monkeypatch):
    """Model bekleyemez; beklemeyi biz yapıyoruz.

    İlk tur engellenirse `BACKOFF` kadar beklenip TEK bir kez daha
    deneniyor. Modelden "20 sn bekle" diye istemek işe yaramıyordu —
    model bekleyemiyor, hemen tekrar deniyor ve engeli sıkıştırıyor.
    """
    monkeypatch.setattr(search_module, "BACKOFF", 0)
    calls = {"n": 0}

    def fake_post(*args, **kwargs):
        calls["n"] += 1
        # İlk iki uç nokta engellensin, tekrar denemede başarılı olsun.
        return FakeResponse("anomaly", 202) if calls["n"] <= 2 else FakeResponse(DDG_HTML)

    monkeypatch.setattr(search_module.httpx, "post", fake_post)
    results = search_module.search("kulaklık")
    assert len(results) == 2, "otomatik tekrar denemesi başarılı olmalıydı"
    assert calls["n"] == 3


# --- youtube_search ---------------------------------------------------------


def _yt(monkeypatch, urls, titles=None):
    """Arama zincirini sahtele: verilen adresleri döndürsün."""
    from app.web.search import Result

    titles = titles or [f"video {i}" for i in range(len(urls))]
    monkeypatch.setattr(
        "app.agent.tools.web.search",
        lambda *a, **k: [Result(title=t, url=u, snippet="") for t, u in zip(titles, urls)],
    )


def _ctx():
    from pathlib import Path as P

    from app.agent.tools.base import ToolContext

    return ToolContext(cwd=P("/tmp"), session_id="t")


def test_youtube_sadece_gercek_video_adresi_donduruyor(monkeypatch):
    """Kanal, oynatma listesi ve shorts elenmeli — istenen inceleme videosu."""
    from app.agent.tools import get_tool

    _yt(monkeypatch, [
        "https://www.youtube.com/c/HawkGamingChair",
        "https://www.youtube.com/shorts/9ljxGM5YZc0",
        "https://www.youtube.com/watch?v=0kCOlDv9KbM",
        "https://youtu.be/H1O8fxyv7HE",
        "https://www.donanimhaber.com/inceleme",
    ])
    result = get_tool("youtube_search").run(_ctx(), query="Hawk Gaming HS420", limit=5)

    assert result.ok
    assert "watch?v=0kCOlDv9KbM" in result.output
    assert "watch?v=H1O8fxyv7HE" in result.output, "youtu.be kısa adresi de videodur"
    assert "shorts" not in result.output
    assert "HawkGamingChair" not in result.output
    assert "donanimhaber" not in result.output


def test_youtube_ayni_video_iki_kez_listelenmiyor(monkeypatch):
    """Aynı video hem `youtu.be` hem `watch?v=` biçiminde gelebilir."""
    from app.agent.tools import get_tool

    _yt(monkeypatch, [
        "https://www.youtube.com/watch?v=0kCOlDv9KbM",
        "https://youtu.be/0kCOlDv9KbM",
        "https://m.youtube.com/watch?app=desktop&v=0kCOlDv9KbM",
    ])
    result = get_tool("youtube_search").run(_ctx(), query="Hawk HS420", limit=5)
    assert result.output.count("0kCOlDv9KbM") == 1
    assert result.meta["count"] == 1


def test_youtube_video_yoksa_uydurmaya_izin_vermiyor(monkeypatch):
    """Sonuç yoksa `ok=False` + açık talimat — model bağlantı icat etmesin."""
    from app.agent.tools import get_tool

    _yt(monkeypatch, ["https://www.epey.com/kulaklik/"])
    result = get_tool("youtube_search").run(_ctx(), query="olmayan ürün")

    assert not result.ok
    assert "bulunamadı" in result.output
    assert "UYDURMA" in result.output


def test_youtube_ciktisi_untrusted(monkeypatch):
    """Video başlıkları da webden geliyor — kural 15."""
    from app.agent.tools import get_tool

    _yt(monkeypatch, ["https://www.youtube.com/watch?v=0kCOlDv9KbM"])
    assert get_tool("youtube_search").run(_ctx(), query="x").untrusted is True


def test_youtube_ve_web_aramasi_birbirini_engellemiyor(monkeypatch):
    """Aynı ürün için hem sayfa hem video araması meşru — tekrar sayılmamalı."""
    from app.agent.tools import get_tool

    _yt(monkeypatch, ["https://www.youtube.com/watch?v=0kCOlDv9KbM"])
    ctx = _ctx()
    assert get_tool("web_search").run(ctx, query="Logitech G435").ok
    assert get_tool("youtube_search").run(ctx, query="Logitech G435").ok
    # Ama video araması kendi içinde tekrarlanmamalı.
    assert not get_tool("youtube_search").run(ctx, query="Logitech G435").ok


def test_youtube_bos_sorgu_blocked():
    from app.agent.tools import get_tool

    assert get_tool("youtube_search").assess(_ctx(), query="").risk == "blocked"
