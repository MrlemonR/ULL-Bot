"""Arayüz varlıklarının statik kontrolleri (Faz 8).

Buradaki testler tarayıcı çalıştırmıyor — kaynak dosyaları okuyup bilinen
tuzakları arıyorlar. Neden gerekli: Faz 8'in ilk teslimi, arayüzün tamamını
kaplayan görünmez bir örtüyle çıktı ve **UI sürüş testlerinin hepsi geçti**,
çünkü onlar `element.click()` ile JS'ten tetikliyordu — bu hit-testing'i
atlar. Kullanıcı ekranda "her şey sönük ve hiçbir şeye tıklanmıyor" gördü.

Ders: bir `display` bildirimi `hidden` niteliğini ezebilir ve bu sessizdir.
Aşağıdaki testler o sınıfı kapatıyor.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

WEB = Path(__file__).resolve().parent.parent / "web"
CSS = WEB / "style.css"
HTML = WEB / "index.html"
JS_DIR = WEB / "js"


def test_hidden_niteligi_display_bildirimleriyle_ezilmiyor():
    """`[hidden] { display: none !important }` kuralı BULUNMALI.

    HTML'in `hidden` niteliği UA stylesheet'ten gelir; author stylesheet'teki
    herhangi bir `display` bildirimi onu ezer. `.modal-backdrop
    { display: grid }` yazmak, `<div hidden>` olmasına rağmen onay
    diyaloğunun kaplamasını kalıcı olarak görünür bırakmıştı: tüm arayüz
    %80 opak siyahın altında kaldı ve z-index:90 yüzünden hiçbir tıklama
    altına geçmedi.
    """
    css = CSS.read_text(encoding="utf-8")
    assert re.search(r"\[hidden\]\s*\{[^}]*display:\s*none\s*!important", css), (
        "style.css'te `[hidden] { display: none !important; }` kuralı yok — "
        "`hidden` nitelikli öğeler `display` bildirimi olan seçicilerde görünür kalır."
    )


def _rules_with_display(css: str) -> dict[str, str]:
    """Seçici → `display` değeri (kaba ama bu iş için yeterli bir ayrıştırma)."""
    rules: dict[str, str] = {}
    for match in re.finditer(r"([^{}]+)\{([^{}]*)\}", css):
        selector = match.group(1).strip().splitlines()[-1].strip()
        body = match.group(2)
        display = re.search(r"(?:^|;)\s*display:\s*([^;!]+)", body)
        if display:
            rules[selector] = display.group(1).strip()
    return rules


def test_hidden_kullanan_ogelerin_hepsi_korunuyor():
    """`hidden` ile gizlenen her öğe, kuralın kapsamına giriyor mu?

    `[hidden]` kuralı hepsini kapsıyor; bu test o kuralın gerçekten
    `display` bildirimi olan sınıfları da yendiğini (yani `!important`
    olduğunu) ve gizlenen öğelerin listesinin bilindiğini belgeliyor.
    """
    html = HTML.read_text(encoding="utf-8")
    js = "\n".join(path.read_text(encoding="utf-8") for path in JS_DIR.glob("*.js"))
    css = CSS.read_text(encoding="utf-8")

    # HTML'de `hidden` yazılmış öğeler + JS'te `.hidden = true` yapılanlar.
    html_hidden = re.findall(r'class="([^"]*)"[^>]*\shidden[\s>]', html)
    assert html_hidden, "HTML'de `hidden` nitelikli öğe bulunamadı — test eskimiş olabilir."

    display_rules = _rules_with_display(css)
    riskli = []
    for class_attr in html_hidden:
        for cls in class_attr.split():
            for selector, value in display_rules.items():
                if f".{cls}" in selector and value != "none":
                    riskli.append(f".{cls} (display: {value})")

    # Riskli olmaları sorun değil — `[hidden]` kuralı onları yeniyor.
    # Sorun, o kural olmadan riskli olmaları. Kural varlığı yukarıda test
    # edildi; burada sadece listenin görünür kalmasını sağlıyoruz.
    assert re.search(r"\[hidden\]\s*\{[^}]*display:\s*none\s*!important", css), (
        f"Bu sınıflar `display` bildirimi taşıyor ve `hidden` ile gizleniyor: {riskli}"
    )
    # `dock-context` JS'ten gizleniyor; onun da `display: flex`i var.
    assert ".dock-context" in " ".join(display_rules), "dock-context kuralı kayboldu mu?"


def test_kaplama_ogesi_hidden_ile_baslar():
    """Modal kaplaması sayfa açılışında gizli olmalı."""
    html = HTML.read_text(encoding="utf-8")
    match = re.search(r'<div[^>]*id="modal-backdrop"[^>]*>', html)
    assert match, "modal-backdrop öğesi yok"
    assert " hidden" in match.group(0), (
        "modal-backdrop `hidden` olmadan başlıyor — açılışta tüm arayüzü kaplar."
    )


def test_tum_js_modulleri_html_den_ulasilabilir():
    """`web/js/` altındaki her modül import zincirinde olmalı (ölü dosya yok)."""
    html = HTML.read_text(encoding="utf-8")
    entry = re.search(r'src="/static/js/(\w+\.js)"', html)
    assert entry, "index.html bir JS giriş noktası yüklemiyor"

    reachable = {entry.group(1)}
    frontier = [entry.group(1)]
    while frontier:
        name = frontier.pop()
        path = JS_DIR / name
        if not path.is_file():
            pytest.fail(f"{name} import ediliyor ama dosya yok")
        for imported in re.findall(r'from\s+"\./([\w.]+\.js)"', path.read_text(encoding="utf-8")):
            if imported not in reachable:
                reachable.add(imported)
                frontier.append(imported)

    on_disk = {path.name for path in JS_DIR.glob("*.js")}
    assert on_disk == reachable, f"ulaşılamayan modül(ler): {on_disk - reachable}"


def test_css_degiskenleri_tanimli():
    """Kullanılan her `var(--x)` `:root`ta tanımlı olmalı."""
    css = CSS.read_text(encoding="utf-8")
    root = re.search(r":root\s*\{(.*?)\n\}", css, re.S)
    assert root, ":root bloğu bulunamadı"
    defined = set(re.findall(r"(--[\w-]+)\s*:", root.group(1)))
    used = set(re.findall(r"var\((--[\w-]+)", css))
    assert not (used - defined), f"tanımsız CSS değişkeni: {sorted(used - defined)}"


def test_bos_durum_dikeyde_dagilmiyor():
    """`.empty` grid'i satırları içeriğe göre paketlemeli.

    `height: 100%` + `place-items: center` üç çocuğu kabın yüksekliğini
    eşit bölen satırlara koyuyor ve her birini kendi satırında ortalıyordu:
    ikon, başlık ve metin ~150px arayla dağılıyor, sayfa yarım yüklenmiş
    gibi görünüyordu. `align-content: center` bunu düzeltiyor.
    """
    css = CSS.read_text(encoding="utf-8")
    match = re.search(r"\.empty\s*\{([^}]*)\}", css)
    assert match, ".empty kuralı yok"
    body = match.group(1)
    assert "align-content: center" in body, (
        ".empty `align-content: center` içermeli, yoksa öğeler dikeyde dağılır."
    )


def test_kaydirma_zincirindeki_her_halka_min_height_sifir():
    """Yükseklik zincirindeki HER kap `min-height: 0` taşımalı.

    Grid ve flex öğelerinin varsayılan `min-height`ı `auto`dur: içeriklerinin
    altına küçülmeyi REDDEDERLER. Zincirde tek bir halka bunu kaçırırsa uzun
    içerik (örn. 2000px'lik bir HTML mail) o halkayı şişirir, altındaki
    `height: 100%` zinciri şişmiş boyu miras alır ve HİÇBİR panel kaymaz —
    içerik ekranın altından taşıp kaybolur.

    Sahada tam olarak bu oldu: `.stage`de bu satır yoktu, mail paneli
    25.000px'e büyüdü ve kullanıcı "kaydıramıyorum" dedi. Diğer sekiz
    halkada kural zaten vardı, o yüzden hata görünmezdi.
    """
    css = CSS.read_text(encoding="utf-8")
    # Yalnızca grid/flex ÖĞESİ olanlar; sıradan blok öğelerin `min-height`ı
    # zaten 0'a çözülüyor, onlara bu satır gerekmiyor.
    zincir = [".stage", ".views", ".view", ".split", ".split-main", ".split-dock",
              ".mail-layout", ".mail-detail", ".mail-list", ".cal-layout", ".cal-main"]
    eksik = []
    for selector in zincir:
        match = re.search(rf"^{re.escape(selector)}\s*\{{(.*?)\}}", css, re.M | re.S)
        if not match:
            eksik.append(f"{selector} (kural yok)")
            continue
        if not re.search(r"min-height:\s*0", match.group(1)):
            eksik.append(selector)
    assert not eksik, f"`min-height: 0` eksik: {eksik}"


def test_mail_govdesi_kum_havuzunda_ve_betiksiz():
    """HTML mail iframe'i `allow-scripts` ALMAMALI.

    `allow-same-origin` var (ana sayfa bağlantı tıklamalarını yakalasın diye);
    ikisi BİRLİKTE verilseydi mail içindeki bir betik ana sayfaya erişebilirdi.
    """
    source = (JS_DIR / "mailbody.js").read_text(encoding="utf-8")
    assert 'setAttribute("sandbox", "allow-same-origin")' in source

    # Yorum satırlarını at: docstring'de "allow-scripts VERİLMİYOR" yazıyor,
    # onu kod sanmayalım. Sadece gerçek kodda arıyoruz.
    kod = "\n".join(
        line for line in source.splitlines()
        if not line.lstrip().startswith(("*", "/*", "//"))
    )
    assert "allow-scripts" not in kod, (
        "mail gövdesi iframe'ine ASLA allow-scripts verilmemeli"
    )


def test_mail_govdesi_uzak_resimleri_varsayilan_engelliyor():
    """Uzak resimler takip pikselidir; varsayılan engelli olmalı."""
    source = (JS_DIR / "mailbody.js").read_text(encoding="utf-8")
    assert "allowRemoteImages = false" in source
    # CSP'de varsayılan `img-src` uzak kaynak içermemeli.
    assert 'allowRemoteImages ? "https: data: cid:" : "data: cid:"' in source


def test_mail_govdesi_tehlikeli_etiketleri_siliyor():
    source = (JS_DIR / "mailbody.js").read_text(encoding="utf-8")
    for tag in ("SCRIPT", "IFRAME", "OBJECT", "EMBED", "FORM"):
        assert tag in source, f"{tag} engellenen etiketler listesinde yok"
    # Regex ile değil, tarayıcının ayrıştırıcısıyla temizlenmeli.
    assert "DOMParser" in source


def test_markdown_davranisi(tmp_path):
    """`markdown()` davranış testini Node ile çalıştır.

    Bu fonksiyon tarayıcıda çalışıyor, o yüzden Python'dan test edilemiyor;
    iddialar `tests/markdown_check.mjs` içinde. İçinde iki gerçek hata
    çıkmıştı (yer tutucu sızıntısı ve tablonun `<p>` içine sarılması),
    ikisi de sessizdi — o yüzden kalıcı teste bağlandı.
    """
    import shutil
    import subprocess

    node = shutil.which("node")
    if node is None:
        pytest.skip("node yok — markdown davranış testi atlandı")

    script = Path(__file__).parent / "markdown_check.mjs"
    result = subprocess.run(
        [node, str(script)], capture_output=True, text=True, timeout=60, check=False
    )
    assert result.returncode == 0, (
        "markdown davranış testi başarısız:\n" + result.stdout + result.stderr
    )


def test_her_js_modulu_ayristirilabiliyor(tmp_path):
    """Her modül ESM olarak ayrıştırılabilmeli.

    `mailbody.js`teki CSS şablon dizesi, bir YORUM içindeki ters tırnak
    yüzünden erken kapanmıştı (`` `color-scheme: dark` ``). Sonrası JS
    sanılıp `SyntaxError` attı; modül grafiği düştüğü için `app.start()`
    hiç çalışmadı ve uygulama "bağlanıyor" rozetinde asılı kaldı —
    WebSocket açılmadığı için sunucu logunda hiçbir iz de yoktu.

    `node --check dosya.js` bunu YAKALAMIYOR: uzantı `.js` olunca Node
    betiği CommonJS sanıp sessizce geçiyor. Kopyayı `.mjs` yapmak ESM
    ayrıştırıcısını zorluyor — hatayı ancak böyle görüyoruz.
    """
    import shutil
    import subprocess

    node = shutil.which("node")
    if node is None:
        pytest.skip("node yok — modül ayrıştırma testi atlandı")

    modules = sorted(JS_DIR.glob("*.js"))
    assert modules, "web/js altında modül yok"

    for module in modules:
        copy = tmp_path / f"{module.stem}.mjs"
        copy.write_text(module.read_text(encoding="utf-8"), encoding="utf-8")
        result = subprocess.run(
            [node, "--check", str(copy)], capture_output=True, text=True, timeout=60, check=False
        )
        assert result.returncode == 0, (
            f"{module.name} ayrıştırılamıyor — tarayıcıda modül grafiği komple düşer:\n"
            + result.stdout
            + result.stderr
        )


def test_daraltilmis_dock_tek_dugme_birakiyor():
    """Daraltılmış dock 46px — içine tek düğme sığar.

    Kullanıcı canlı bildirdi: daraltınca "kalem butonu hâlâ kalıyor ve
    kapatma butonu sağa kayıyor". Ölçüldü: başlık satırının `scrollWidth`i
    52px'e çıkıyordu (client 45px) ve daraltma düğmesinin sağ kenarı
    pencerenin 7px DIŞINDA kalıyordu.
    """
    css = CSS.read_text(encoding="utf-8")
    collapsed_hidden = re.search(
        r"((?:^\.dock-collapsed[^,{]*,\s*)+\.dock-collapsed[^,{]*)\{[^}]*display:\s*none",
        css,
        re.MULTILINE,
    )
    assert collapsed_hidden, "`.dock-collapsed ... { display: none }` bloğu bulunamadı"
    assert "[data-new]" in collapsed_hidden.group(1), (
        "daraltılmış dock'ta `[data-new]` (yeni konuşma) düğmesi gizlenmeli — "
        "iki düğme 46px'lik kolona sığmıyor ve ikincisi dışarı taşıyor"
    )


def test_arac_kartlari_cevabin_ustune_ekleniyor():
    """Araç kartları `streamBody`den ÖNCE eklenmeli.

    Kullanıcı bildirdi: "yaptıkları en altta kalıyor, verdiği cevap en
    üstte". Sebep `insertBefore(node, this.streamBody.nextSibling)` idi:
    her yeni kart gövdenin hemen arkasına giriyor, öncekileri aşağı itiyordu
    — yani cevap üstte, işler altında ve TERS sırada. Doğal okuma sırası
    önce ne yapıldığı, sonra sonuç.
    """
    chat = (JS_DIR / "chat.js").read_text(encoding="utf-8")
    # Yorumlarda geçmesi serbest (hatanın hikâyesi orada yazılı); yasak olan
    # ÇAĞRININ kendisi.
    assert "insertBefore(node, this.streamBody.nextSibling)" not in chat, (
        "`streamBody.nextSibling`e ekleme geri gelmiş — araç kartları cevabın "
        "altında ve ters sırada görünür."
    )
    assert chat.count("insertBefore(node, this.streamBody)") >= 2, (
        "araç kartı ve bildirim, `streamBody`den önce eklenmeli"
    )


def test_tablo_hucreleri_satir_ici_bicimlendirmeden_geciyor():
    """Tablo hücrelerinde `**kalın**` ve `[link](url)` işlenmeli.

    Kullanıcı ekran görüntüsüyle bildirdi: karşılaştırma tablosunda ürün
    adları `**Razer BlackShark V2 Pro**` diye ham görünüyordu ve YouTube
    bağlantıları `[İnceleme Videosu](https://…)` metni olarak kalıp
    TIKLANAMIYORDU. Sebep: tablolar satır içi kurallardan ÖNCE yer tutucuya
    alınıyor ve yer tutucu içeriği bir daha işlenmiyordu.
    """
    util = (JS_DIR / "util.js").read_text(encoding="utf-8")
    assert re.search(r"function inline\(", util), "`inline()` fonksiyonu yok"
    assert re.search(r"<\$\{tag\}\$\{style\}>\$\{inline\(", util), (
        "tablo hücreleri `inline()`dan geçirilmeli — yoksa kalın ve bağlantı "
        "ham metin olarak görünür"
    )


def test_tablo_sutunlari_ezilmiyor():
    """`width: 100%` sabit tablo, ilk sütunu ezip kelimeyi ortadan bölüyordu."""
    css = CSS.read_text(encoding="utf-8")
    match = re.search(r"\.md-table\s*\{([^}]*)\}", css)
    assert match, ".md-table kuralı yok"
    assert "min-width: 100%" in match.group(1) and "width: auto" in match.group(1), (
        ".md-table içeriğe göre genişlemeli (`width: auto; min-width: 100%`); "
        "sabit %100'de sütunlar eziliyor"
    )
    cells = re.search(r"\.md-table th, \.md-table td\s*\{([^}]*)\}", css)
    assert cells and "word-break: normal" in cells.group(1), (
        "`.msg-bot .body`den miras gelen `word-break: break-word` ürün adını "
        "ortadan bölüyor — hücrede `word-break: normal` olmalı"
    )
