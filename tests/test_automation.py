"""Otomasyon: eylem kümesi, beyaz liste, adım kayıtları (Faz 11).

Tarayıcı açılmıyor — buradaki testler KARARLARI sınıyor: hangi adres izinli,
hangi eylem onay ister, adım düzenlenince ne düşer. Tarayıcının kendisi
(CDP akışı, tıklama, yazma) canlı doğrulandı; onu teste bağlamak her koşuda
Chromium başlatmak demek olurdu.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.browser.actions import Action, host_allowed
from app.browser.agent import _json_from, action_still_valid
from app.browser.session import BrowserError, Element, PageState


# --- beyaz liste ------------------------------------------------------------


def test_izinli_alan_adlari():
    izin = ["mail.google.com", "docs.google.com"]
    assert host_allowed("https://mail.google.com/mail/u/0", izin)
    assert host_allowed("https://docs.google.com/spreadsheets/d/x", izin)
    assert not host_allowed("https://kotu-site.example/", izin)


def test_alt_alan_adi_kabul_ama_benzer_isim_degil():
    """`google.com` izinliyse `mail.google.com` da izinli; `googlecom.evil` DEĞİL."""
    assert host_allowed("https://mail.google.com/x", ["google.com"])
    assert not host_allowed("https://googlecom.evil/x", ["google.com"])
    assert not host_allowed("https://evil.com/?x=google.com", ["google.com"])


def test_bos_liste_her_yeri_acmaz():
    """Boş liste "her yer serbest" DEĞİL, "hiçbir yer" demek.

    Sessiz bir varsayılan, ajanın bir gün bambaşka bir sitede tıklaması
    demektir; otomasyon tanımlanırken siteler açıkça yazılmalı.
    """
    assert not host_allowed("https://example.com", [])


# --- eylem kümesi -----------------------------------------------------------


def test_bilinmeyen_eylem_reddediliyor():
    """Model uydurursa erken patlasın; sayfada rastgele bir şey denemesin."""
    with pytest.raises(BrowserError, match="Bilinmeyen eylem"):
        Action.parse({"type": "javascript_calistir", "code": "fetch('/sil')"})


def test_eylem_kumesi_js_calistirmayi_icermiyor():
    """En kritik sınır: modelin sayfada rastgele JS çalıştırma yolu YOK.

    Olsaydı, sayfaya enjekte edilmiş tek bir cümle ("şu JS'i çalıştır")
    ajanın oturum açmış hesabında her şeyi yapabilirdi.
    """
    from app.browser.actions import ACTIONS

    assert set(ACTIONS) == {"git", "tikla", "yaz", "tus", "kaydir", "oku", "bekle"}


def test_geri_alinamaz_eylem_onay_istiyor():
    state = PageState(elements=[
        Element(0, "button", "", "Gönder", 10, 10),
        Element(1, "a", "", "Gelen Kutusu", 20, 20),
    ])
    assert Action.parse({"type": "tikla", "index": 0}).is_irreversible(state)
    assert not Action.parse({"type": "tikla", "index": 1}).is_irreversible(state)
    assert not Action.parse({"type": "oku"}).is_irreversible(state)


def test_bekleme_suresi_sinirli():
    """Model "3600 saniye bekle" derse otomasyon donardı."""
    assert Action.parse({"type": "bekle", "seconds": 9999}).seconds == 10.0


# --- model cevabının ayrıştırılması -----------------------------------------


def test_json_kod_blogundan_cikariliyor():
    """Küçük modeller JSON'u ``` içine sarıyor ya da başına cümle ekliyor."""
    assert _json_from('```json\n{"type":"tikla","index":2}\n```') == {"type": "tikla", "index": 2}
    assert _json_from('Tabii: {"type":"oku"} — umarım olur') == {"type": "oku"}
    with pytest.raises(BrowserError):
        _json_from("hiç JSON yok")


# --- kayıtlı eylemin yeniden kullanımı --------------------------------------


def test_ayni_sayfada_model_cagrilmiyor():
    """Kotayı koruyan asıl mekanizma: eylem hâlâ geçerliyse model çağrılmaz."""
    state = PageState(elements=[Element(0, "a", "", "Gelen Kutusu", 10, 10)])
    assert action_still_valid({"type": "tikla", "index": 0, "label": "Gelen Kutusu"}, state)


def test_etiket_degistiyse_yeniden_cozuluyor():
    """Liste kaydığında aynı numara BAŞKA bir şeye denk gelir.

    Numaranın var olması yetmez; üzerindeki metin de tutmalı, yoksa ajan
    "Gelen Kutusu" sanıp "Kalıcı olarak sil"e tıklayabilir.
    """
    state = PageState(elements=[Element(0, "button", "", "Kalıcı olarak sil", 10, 10)])
    assert not action_still_valid({"type": "tikla", "index": 0, "label": "Gelen Kutusu"}, state)


def test_kayitli_eylem_yoksa_model_cagrilir():
    assert not action_still_valid(None, PageState())


# --- sayfa modele nasıl gidiyor ---------------------------------------------


def test_sayfa_numarali_liste_olarak_veriliyor():
    state = PageState(url="https://x", title="Kutu", elements=[
        Element(0, "a", "", "Gelen Kutusu", 1, 1),
        Element(1, "input", "text", "", 2, 2),
    ])
    rendered = state.render()
    assert "[0] <a> 'Gelen Kutusu'" in rendered
    assert "[1] <input type=text>" in rendered


# --- kayıtlar ---------------------------------------------------------------


def test_adim_duzenlenince_eylem_dusuyor(workspace: Path) -> None:
    """Cümle değişti: eski somut eylem artık o adıma ait değil.

    Düşmezse kullanıcı adımı düzeltir ama ajan eskisini yapmaya devam eder.
    """
    from fastapi.testclient import TestClient

    from app.browser import store
    from app.main import app

    automation = store.create_automation("t", allowlist=["example.com"])
    steps = store.replace_steps(automation["id"], [
        {"intent": "ilk maili aç", "action": {"type": "tikla", "index": 3}},
    ])
    assert steps[0]["action"] == {"type": "tikla", "index": 3}

    client = TestClient(app)
    client.patch(f"/api/automations/steps/{steps[0]['id']}", json={"intent": "ikinci maili aç"})

    updated = store.list_steps(automation["id"])[0]
    assert updated["intent"] == "ikinci maili aç"
    assert updated["action"] is None, "cümle değişince eski eylem düşmeliydi"
    assert updated["status"] == "bekliyor"


def test_adim_silinince_siralama_sikisiyor(workspace: Path) -> None:
    from app.browser import store

    automation = store.create_automation("t", allowlist=["example.com"])
    steps = store.replace_steps(automation["id"], [
        {"intent": "bir"}, {"intent": "iki"}, {"intent": "üç"},
    ])
    store.delete_step(steps[1]["id"])
    kalan = store.list_steps(automation["id"])
    assert [s["intent"] for s in kalan] == ["bir", "üç"]
    assert [s["position"] for s in kalan] == [0, 1]


def test_otomasyon_silinince_adimlari_da_gidiyor(workspace: Path) -> None:
    from app.browser import store

    automation = store.create_automation("t", allowlist=["example.com"])
    store.replace_steps(automation["id"], [{"intent": "bir"}])
    store.delete_automation(automation["id"])
    assert store.list_steps(automation["id"]) == []
    assert store.get_automation(automation["id"]) is None


# --- 2026-08-19 düzeltmeleri ------------------------------------------------


def test_beyaz_liste_istemciden_okunmuyor():
    """Güvenlik kararı istemciye sorulmamalı.

    Canlı yaşandı: istemci boş liste gönderdi ve kayıtta izinli olan
    `mail.google.com` "izinli değil" diye reddedildi. Tersi çok daha kötü:
    bozuk ya da kötü niyetli bir istemci sonsuz geniş liste gönderip sınırı
    tamamen kaldırabilirdi.
    """
    source = (Path(__file__).resolve().parent.parent / "app" / "main.py").read_text(encoding="utf-8")
    start_handler = source[source.index('if kind == "start":'):source.index('if kind == "stop":')]
    assert 'data.get("allowlist")' not in start_handler, "beyaz liste hâlâ istemciden geliyor"
    assert "automation_store.get_automation" in start_handler, "kayıttan okunmuyor"


def test_ekran_goruntusu_pencere_yuzeyinden_alinmiyor():
    """`fromSurface: False` olmazsa görünmeyen pencerede ayna donuyor.

    Ölçüldü: pencere ekranda görünmüyorken (başka masaüstü, küçültülmüş,
    üstü kapalı) `Page.captureScreenshot` yeni bir kare bekleyip 30 saniye
    takılıyor ve panel boş kalıyor.
    """
    source = (Path(__file__).resolve().parent.parent / "app" / "browser" / "session.py").read_text(
        encoding="utf-8"
    )
    assert '"fromSurface": False' in source


def test_gorunur_pencere_varsayilan():
    """Kullanıcı "tam çalışan bir tarayıcı" istedi: varsayılan mod görünür."""
    source = (Path(__file__).resolve().parent.parent / "app" / "main.py").read_text(encoding="utf-8")
    assert 'headless=bool(data.get("headless", False))' in source


def test_mod_degisince_tarayici_yeniden_baslatiliyor():
    """Chromium çalışırken headless↔görünür geçemiyor.

    İzlenmezse "Tarayıcıyı aç" arka planda duran eski headless süreci
    benimsiyor ve pencere hiç açılmıyordu.
    """
    source = (Path(__file__).resolve().parent.parent / "app" / "browser" / "session.py").read_text(
        encoding="utf-8"
    )
    assert "if self.headless == headless:" in source


# --- 2026-08-19, ilk gerçek koşudan çıkanlar --------------------------------


def test_degistirici_tuslar():
    """Google Sheets'te gezinmenin TEK yolu klavye.

    Hücre ızgarası canvas: `kaydir` çalışmıyor (canlı görüldü — model
    "10000px aşağı in" dedi, hiçbir şey olmadı) ve tıklanacak DOM öğesi yok.
    """
    from app.browser.actions import parse_key

    assert parse_key("ctrl+end") == ("End", 35, 2)
    assert parse_key("ctrl+arrowdown") == ("ArrowDown", 40, 2)
    assert parse_key("shift+tab") == ("Tab", 9, 8)
    assert parse_key("enter") == ("Enter", 13, 0)
    with pytest.raises(BrowserError):
        parse_key("ctrl+f13")


def test_prompt_tablolarda_klavye_diyor():
    """Modelin kendi akıl etmesini beklemek yerine söylüyoruz."""
    source = (Path(__file__).resolve().parent.parent / "app" / "browser" / "agent.py").read_text(
        encoding="utf-8"
    )
    assert "ctrl+end" in source
    assert "canvas" in source.casefold()


def test_surec_tutamaci_tarayicinin_ayakta_olmasina_kanit_degil():
    """Chromium aynı profille açık bir örneğe devredip HEMEN çıkıyor.

    `poll()` bir çıkış kodu döndürüyor ve kod "tarayıcı kapandı" sanıyordu;
    ajan her adımda "Tarayıcı kapalı" hatası veriyordu. Otorite CDP
    bağlantısı olmalı.
    """
    source = (Path(__file__).resolve().parent.parent / "app" / "browser" / "session.py").read_text(
        encoding="utf-8"
    )
    running = source[source.index("def running(self)"):source.index("def _binary(self)")]
    assert "if self.socket is not None:" in running, "süreç tutamacına güveniliyor"


def test_yeni_sekmeye_geciliyor():
    """Yeni sekme açılınca ajan ve ayna oraya geçmeli.

    Eski davranışta bağlantı `pages[0]`da kalıyordu: ekranda yeni sayfa
    görünüyor, ajan görünmeyen eski sayfada çalışıyordu.
    """
    source = (Path(__file__).resolve().parent.parent / "app" / "browser" / "session.py").read_text(
        encoding="utf-8"
    )
    assert "_known_targets" in source and "ensure_page" in source


def test_tek_adim_ve_devam_calistirma():
    """Kullanıcı: "bir adım geri gidip o adımı revize edip devam ettirebilelim"."""
    source = (Path(__file__).resolve().parent.parent / "app" / "main.py").read_text(encoding="utf-8")
    assert "only: int | None = None, start_at: int = 0" in source
    assert 'data.get("only_step")' in source and 'data.get("from_step")' in source

    js = (Path(__file__).resolve().parent.parent / "web" / "js" / "automation.js").read_text(
        encoding="utf-8"
    )
    assert "data-only" in js and "data-from" in js


def test_calistirma_hatasi_ui_yi_asili_birakmiyor():
    """Tur bir istisnayla düşerse UI sonsuza kadar "çalışıyor" görünüyordu."""
    source = (Path(__file__).resolve().parent.parent / "app" / "main.py").read_text(encoding="utf-8")
    guarded = source[source.index("async def guarded("):source.index("task = asyncio.create_task(guarded())")]
    assert '"type": "run_done"' in guarded, "hata yolunda bitiş bildirilmiyor"


def test_engellenen_adres_ayri_istisna_tipi():
    """Hata METNİNİ eşleştirmek kırılgandı.

    İki ayrı yerde iki farklı cümle vardı ("… izinli siteleri arasında
    değil" ve "Tarayıcı izinli sitelerin dışında: …"); UI yalnızca birini
    tanıyordu ve diğerinde "izin ver" düğmesi hiç çıkmıyordu.
    """
    from app.browser.session import BlockedHost, BrowserError

    exc = BlockedHost("docs.google.com", "https://docs.google.com/x", ["mail.google.com"])
    assert isinstance(exc, BrowserError)
    assert exc.host == "docs.google.com"
    assert exc.allowlist == ["mail.google.com"]

    for module in ("actions", "agent"):
        source = (
            Path(__file__).resolve().parent.parent / "app" / "browser" / f"{module}.py"
        ).read_text(encoding="utf-8")
        assert "BlockedHost(" in source, f"{module}.py hâlâ düz BrowserError fırlatıyor"


def test_otomasyon_tarayiciyi_kendi_aciyor():
    """Kullanıcı: "sayfa açık değilse bile sayfayı açsın".

    Eskiden planlama tarayıcının önceden açılmış olmasını bekliyordu.
    """
    source = (Path(__file__).resolve().parent.parent / "app" / "main.py").read_text(encoding="utf-8")
    assert "async def prepare(" in source
    plan_handler = source[source.index('if kind == "plan":'):source.index('if kind == "run":')]
    assert "await prepare(automation)" in plan_handler


def test_dogru_sekme_secimi():
    """Birden fazla sekme açık olabiliyor; aynı siteden olan seçilmeli."""
    source = (Path(__file__).resolve().parent.parent / "app" / "browser" / "session.py").read_text(
        encoding="utf-8"
    )
    assert "async def open_for(" in source and "async def focus_tab(" in source
    open_for = source[source.index("async def open_for("):source.index("async def _attach(")]
    assert "hostname" in open_for, "aynı siteden sekme aranmıyor"
    assert "Target.createTarget" in open_for, "sekme yoksa açılmıyor"


# --- adım yönetimi (kullanıcı istekleri) ------------------------------------


def test_adim_elle_eklenip_araya_giriyor(workspace: Path) -> None:
    from app.browser import store

    automation = store.create_automation("t", allowlist=["example.com"])
    store.replace_steps(automation["id"], [{"intent": "bir"}, {"intent": "iki"}])
    store.add_step(automation["id"], "araya", kind="kontrol", position=1)

    steps = store.list_steps(automation["id"])
    assert [s["intent"] for s in steps] == ["bir", "araya", "iki"]
    assert [s["position"] for s in steps] == [0, 1, 2]
    assert steps[1]["kind"] == "kontrol"


def test_adim_sirasi_degistiriliyor(workspace: Path) -> None:
    """Kullanıcı: "adım ekleyip sıralarını değiştirebilelim"."""
    from app.browser import store

    automation = store.create_automation("t", allowlist=["example.com"])
    steps = store.replace_steps(
        automation["id"], [{"intent": "bir"}, {"intent": "iki"}, {"intent": "üç"}]
    )
    store.move_step(steps[2]["id"], -1)
    assert [s["intent"] for s in store.list_steps(automation["id"])] == ["bir", "üç", "iki"]

    # Sınırın dışına taşımak sessizce yoksayılmalı, sıra bozulmamalı.
    ilk = store.list_steps(automation["id"])[0]
    store.move_step(ilk["id"], -1)
    assert [s["position"] for s in store.list_steps(automation["id"])] == [0, 1, 2]


def test_planlayici_adim_turu_veriyor():
    """Tür listede rozet olarak görünüyor; model uydurursa `islem`e düşer."""
    source = (Path(__file__).resolve().parent.parent / "app" / "browser" / "agent.py").read_text(
        encoding="utf-8"
    )
    assert '"kind": "sayfa"' in source
    assert 'gecerli = {"sayfa", "oku", "yaz", "tikla", "bekle", "kontrol", "islem"}' in source


def test_izin_listesi_url_yapistirmayi_kaldiriyor():
    """Kullanıcı kutuya tam URL yapıştırıyor; ham hâlde asla eşleşmiyor.

    Canlı yaşandı: listede
    `https://mail.google.com/mail/u/1/#inbox` duruyordu ve otomasyon
    "izinli değil" deyip duruyordu.
    """
    from app.browser.actions import host_allowed, normalize_host

    assert normalize_host("https://mail.google.com/mail/u/1/#inbox") == "mail.google.com"
    assert normalize_host("mail.google.com/mail/u/0") == "mail.google.com"
    assert normalize_host("*.google.com") == "google.com"
    assert host_allowed("https://mail.google.com/x", ["https://mail.google.com/mail/u/1/#inbox"])


def test_ilerleme_gunlugu_yayinlaniyor():
    """Kullanıcı: "neler yaptığını, nelerde uğraştığını görebilelim".

    Model çağrısı kota doluyken saniyeler sürüyor; ekranda hiçbir şey
    görünmeyince kullanıcı "takıldı" sanıyordu.
    """
    main = (Path(__file__).resolve().parent.parent / "app" / "main.py").read_text(encoding="utf-8")
    agent = (Path(__file__).resolve().parent.parent / "app" / "browser" / "agent.py").read_text(
        encoding="utf-8"
    )
    assert '"type": "log"' in main
    assert "on_progress" in agent and "Modele soruluyor" in agent

    js = (Path(__file__).resolve().parent.parent / "web" / "js" / "automation.js").read_text(
        encoding="utf-8"
    )
    assert "addLog" in js and 'case "log"' in js


def test_bos_izin_listesi_kaydedilemiyor(workspace: Path) -> None:
    """Boş liste "her yer serbest" değil, "hiçbir yer" demek.

    Kullanıcı satırları silip yeniden yazarken farkında olmadan boş
    kaydedebiliyor; sonra otomasyon sessizce çalışamaz hâle geliyor
    ("izinler silindi"). Sessizce kabul etmek yerine reddediyoruz ve
    eski liste yerinde kalıyor.
    """
    from fastapi.testclient import TestClient

    from app.browser import store
    from app.main import app

    automation = store.create_automation("t", allowlist=["example.com"])
    client = TestClient(app)

    response = client.patch(f"/api/automations/{automation['id']}", json={"allowlist": []})
    assert response.status_code == 400
    assert store.get_automation(automation["id"])["allowlist"] == ["example.com"]

    # Sadece boşluk/çöp girilmişse de aynı koruma geçerli.
    response = client.patch(f"/api/automations/{automation['id']}", json={"allowlist": ["  ", ""]})
    assert response.status_code == 400
    assert store.get_automation(automation["id"])["allowlist"] == ["example.com"]


def test_bitiste_adimlarin_son_hali_gonderiliyor():
    """Kullanıcı "bitirdiğini fark etmedim, sayfa yenilenmedi" dedi.

    Liste ekranda eski durumuyla kalıyordu; bitişte sunucudan son hâli
    gidiyor ve UI onu kullanıyor.
    """
    main = (Path(__file__).resolve().parent.parent / "app" / "main.py").read_text(encoding="utf-8")
    run_done = main[main.index('"type": "run_done",\n            "status": status,'):]
    assert '"steps": automation_store.list_steps(automation_id)' in run_done[:400]

    js = (Path(__file__).resolve().parent.parent / "web" / "js" / "automation.js").read_text(
        encoding="utf-8"
    )
    handler = js[js.index('case "run_done"'):js.index('case "error"')]
    assert "if (event.steps) this.steps = event.steps" in handler
    assert "TAMAMLANDI" in handler, "bitiş kullanıcıya belirgin gösterilmiyor"


def test_windows_tarayici_bulma():
    """Windows'ta Chrome/Edge `PATH`te olmuyor; bilinen yollar taranmalı."""
    source = (Path(__file__).resolve().parent.parent / "app" / "browser" / "session.py").read_text(
        encoding="utf-8"
    )
    finder = source[source.index("def _binary(self)"):source.index("async def start(")]
    assert "msedge" in finder and "PROGRAMFILES" in finder
    assert "CHROME_PATH" in finder, "elle yol verme yolu yok"
