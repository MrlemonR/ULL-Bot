/* Mail hesabı ekleme akışı — IMAP + uygulama parolası.
 *
 * Google OAuth bir süre burada duruyordu ve KALDIRILDI: `mail.google.com`
 * kısıtlı bir kapsam olduğu için Google, doğrulanmamış uygulamaların
 * yetkilendirmelerini 7 günde bir iptal ediyor (bkz. DECISIONS.md →
 * "Google OAuth, kişisel Gmail için pratik değil"). Kişisel bir araç için
 * doğru mekanizma uygulama parolası: doğrulama yok, süre sınırı yok.
 *
 * Diyaloğun asıl işi bu yüzden kullanıcıyı doğru parolaya götürmek. Üç
 * tuzağı da o kapatıyor, çünkü üçü de sahada yaşandı:
 *
 *   1. Kullanıcı adına görünen adını yazmak ("Mrlemon") → Gmail reddediyor.
 *      Alan artık gelişmiş bölümde ve Google'da e-postaya kilitli.
 *   2. Workspace adresinde sunucuyu `imap.<alanadı>` sanmak → doğrusu
 *      `imap.gmail.com`. Tek tıkla düzelten bir düğme var.
 *   3. Parolayı Google'ın gösterdiği gibi boşluklarla yapıştırmak →
 *      boşluklar otomatik temizleniyor.
 */

import { api } from "./api.js";
import { closeModal, escapeHtml, openExternal, openModal, toast } from "./util.js";

/* Alan adından sunucu tahmini. Kullanıcı elle de değiştirebilir. */
const PROVIDERS = {
  "gmail.com": { host: "imap.gmail.com", port: 993, kind: "google", label: "Gmail" },
  "googlemail.com": { host: "imap.gmail.com", port: 993, kind: "google", label: "Gmail" },
  "outlook.com": { host: "outlook.office365.com", port: 993, kind: "microsoft", label: "Outlook" },
  "hotmail.com": { host: "outlook.office365.com", port: 993, kind: "microsoft", label: "Outlook" },
  "live.com": { host: "outlook.office365.com", port: 993, kind: "microsoft", label: "Outlook" },
  "yandex.com": { host: "imap.yandex.com", port: 993, kind: "yandex", label: "Yandex" },
  "yandex.com.tr": { host: "imap.yandex.com.tr", port: 993, kind: "yandex", label: "Yandex" },
  "yahoo.com": { host: "imap.mail.yahoo.com", port: 993, kind: "yahoo", label: "Yahoo" },
  "icloud.com": { host: "imap.mail.me.com", port: 993, kind: "apple", label: "iCloud" },
};

const APP_PASSWORD_URL = "https://myaccount.google.com/apppasswords";
const TWO_STEP_URL = "https://myaccount.google.com/signinoptions/twosv";
const WORKSPACE_IMAP_URL = "https://admin.google.com/ac/apps/gmail/enduseraccess";

const GOOGLE_HOST = /(^|\.)gmail\.com$/i;

export function detectProvider(email) {
  const domain = String(email || "").split("@")[1]?.toLowerCase().trim();
  if (!domain) return null;
  if (PROVIDERS[domain]) return { ...PROVIDERS[domain], domain };
  // Tanımadığımız alan adı: sunucuyu tahmin ediyoruz ama Google Workspace
  // olma ihtimali yüksek, o yüzden `unknown` işaretliyoruz.
  return { host: `imap.${domain}`, port: 993, kind: "unknown", label: domain, domain };
}

export class AccountDialog {
  constructor(onSaved) {
    this.onSaved = onSaved;
  }

  open() {
    openModal(`
      <div class="modal-head">
        <h3>Mail hesabı ekle</h3>
        <p>Gmail, Outlook, Yandex ve diğer IMAP sunucuları.</p>
      </div>
      <div class="modal-body">
        <label class="f">
          <span>E-posta adresi</span>
          <input type="email" id="a-email" placeholder="ad@sirket.com" autocomplete="off">
        </label>
        <div id="a-provider"></div>
        <div id="a-google" hidden>
          <div class="notice notice-info" id="a-google-note"></div>
        </div>

        <div style="display:grid;grid-template-columns:2fr 1fr;gap:12px">
          <label class="f"><span>IMAP sunucusu</span><input type="text" id="a-host" placeholder="imap.…"></label>
          <label class="f"><span>Port</span><input type="number" id="a-port" value="993"></label>
        </div>

        <label class="f">
          <span class="label-row">
            <span id="a-pass-label">Parola</span>
            <button type="button" class="inline-link" id="a-getpass" hidden>
              Parolayı Google'dan al ↗
            </button>
          </span>
          <input type="password" id="a-pass" autocomplete="off"
                 placeholder="Google'dan aldığın 16 haneli parola">
        </label>
        <p class="muted" id="a-pass-hint" style="margin:-6px 0 12px"></p>

        <button class="btn btn-ghost btn-sm" id="a-adv-toggle" type="button"
                style="padding:4px 0;background:none;border:none;color:var(--text-3)">
          Gelişmiş ▾
        </button>
        <div id="a-adv" hidden style="margin-top:10px">
          <label class="f">
            <span>Kullanıcı adı</span>
            <input type="text" id="a-user" autocomplete="off">
          </label>
          <p class="muted" id="a-user-hint" style="margin-top:-8px"></p>
        </div>

        <div id="a-result"></div>
      </div>
      <div class="modal-foot">
        <button class="btn btn-ghost" data-close>Vazgeç</button>
        <button class="btn btn-ghost" id="a-test">Bağlantıyı sına</button>
        <button class="btn btn-primary" id="a-save">Ekle</button>
      </div>`);

    const email = document.getElementById("a-email");
    email.addEventListener("input", () => this.onEmailChanged());
    email.focus();

    const host = document.getElementById("a-host");
    host.addEventListener("input", () => { host.dataset.touched = "1"; });

    const adv = document.getElementById("a-adv");
    document.getElementById("a-adv-toggle").addEventListener("click", (event) => {
      adv.hidden = !adv.hidden;
      event.currentTarget.textContent = adv.hidden ? "Gelişmiş ▾" : "Gelişmiş ▴";
    });

    document.querySelector("[data-close]").addEventListener("click", closeModal);
    document.getElementById("a-test").addEventListener("click", () => this.test());
    document.getElementById("a-save").addEventListener("click", () => this.save());

    this.onEmailChanged();
  }

  /* --- e-posta yazıldıkça sunucuyu ve ipuçlarını güncelle --- */

  onEmailChanged() {
    const email = document.getElementById("a-email").value.trim();
    const provider = detectProvider(email);
    const box = document.getElementById("a-provider");
    const google = document.getElementById("a-google");
    const hostInput = document.getElementById("a-host");
    const portInput = document.getElementById("a-port");

    if (!provider) {
      box.innerHTML = "";
      google.hidden = true;
      document.getElementById("a-getpass").hidden = true;
      document.getElementById("a-pass-label").textContent = "Parola";
      document.getElementById("a-pass-hint").textContent = "";
      return;
    }

    // Kullanıcı sunucuyu elle değiştirdiyse üstüne yazma.
    if (!hostInput.dataset.touched) {
      hostInput.value = provider.host;
      portInput.value = provider.port;
    }

    const isGoogle = provider.kind === "google";
    const maybeWorkspace = provider.kind === "unknown";
    const googleish = isGoogle || maybeWorkspace;

    box.innerHTML = isGoogle
      ? `<p class="muted">${escapeHtml(provider.label)} algılandı.</p>`
      : maybeWorkspace
        ? `<p class="muted">Özel alan adı (<code>${escapeHtml(provider.domain)}</code>).
           Google Workspace ise aşağıdaki nota bak.</p>`
        : `<p class="muted">${escapeHtml(provider.label)} algılandı.</p>`;

    google.hidden = !googleish;
    document.getElementById("a-pass-label").textContent =
      googleish ? "Uygulama parolası" : "Parola";
    document.getElementById("a-pass-hint").textContent = googleish
      ? "Google parolayı 4'erli gruplar hâlinde gösterir (abcd efgh ijkl mnop) — boşluklarla yapıştırabilirsin, otomatik temizlenir."
      : "";

    // Parolayı alacağı link, tam parolayı gireceği yerin yanında dursun.
    const getPass = document.getElementById("a-getpass");
    getPass.hidden = !googleish;
    getPass.onclick = () => this.openAppPasswordPage(email);

    this.syncUsername(email, hostInput.value.trim());
    if (googleish) this.renderGoogleHelp(email, provider);
  }

  /**
   * Kullanıcı adı neredeyse hep tam e-posta adresidir ve Gmail'de BAŞKA bir
   * şey olması mümkün değil. Kullanıcı buraya görünen adını ("Mrlemon")
   * yazıp `AUTHENTICATIONFAILED` aldı — alan artık Google'da kilitli.
   */
  syncUsername(email, host) {
    const input = document.getElementById("a-user");
    const hint = document.getElementById("a-user-hint");
    const googleHost = GOOGLE_HOST.test(host);

    if (!input.dataset.touched || googleHost) input.value = email;
    input.disabled = googleHost;
    hint.textContent = googleHost
      ? "Gmail ve Google Workspace'te kullanıcı adı DAİMA tam e-posta adresidir; görünen ad (örn. 'Mrlemon') çalışmaz."
      : "Boş bırakırsan e-posta adresi kullanılır. Sadece sunucun farklı bir kullanıcı adı istiyorsa değiştir.";
  }

  renderGoogleHelp(email, provider) {
    const note = document.getElementById("a-google-note");

    // Workspace hesapları (şirket alan adı) Gmail'in IMAP sunucusunu
    // kullanır, `imap.<alanadı>` DEĞİL. Alan adından tahmin ettiğimiz sunucu
    // bu durumda yanlış olur ve kullanıcı anlamsız bir hata alır.
    const workspaceBlock = provider.kind !== "google"
      ? `<br><br><b>Google Workspace hesabıysa:</b> sunucu
         <code>${escapeHtml(provider.host)}</code> değil,
         <code>imap.gmail.com</code> olmalı.
         <div style="margin-top:8px">
           <button class="btn btn-ghost btn-sm" id="a-usegmail">
             Sunucuyu imap.gmail.com yap
           </button>
         </div>
         <br>Şirket hesaplarında uygulama parolası ve IMAP yöneticiler
         tarafından kapatılmış olabilir. Google "Aradığınız ayar hesabınızda
         kullanılamıyor" diyorsa yapabileceğin bir şey yok —
         <a href="${WORKSPACE_IMAP_URL}">yöneticinin</a> IMAP erişimine ve
         uygulama parolalarına izin vermesi gerekiyor.`
      : "";

    note.innerHTML = `
      Google normal hesap parolasını kabul etmez; 16 haneli bir
      <b>uygulama parolası</b> gerekiyor.
      Sayfa açılmıyorsa önce <a href="${TWO_STEP_URL}">2 adımlı doğrulamayı</a>
      açman gerekir — Google bu seçeneği ancak o zaman gösteriyor.
      <div style="margin-top:10px">
        <button class="btn btn-ghost btn-sm" id="a-apppass">
          Google'da uygulama parolası oluştur ↗
        </button>
      </div>${workspaceBlock}`;

    document.getElementById("a-apppass").addEventListener("click", () =>
      this.openAppPasswordPage(email)
    );

    const useGmail = document.getElementById("a-usegmail");
    if (useGmail) {
      useGmail.addEventListener("click", () => {
        const host = document.getElementById("a-host");
        host.value = "imap.gmail.com";
        host.dataset.touched = "1";   // artık alan adından tahmin etme
        document.getElementById("a-port").value = 993;
        this.syncUsername(document.getElementById("a-email").value.trim(), "imap.gmail.com");
        toast("Sunucu ayarlandı", "imap.gmail.com:993", "ok");
      });
    }
  }

  /**
   * Google'ın uygulama parolası sayfasını aç.
   *
   * `authuser` hesabı ön seçer — iki Google hesabı olan kullanıcı yanlış
   * olanda parola üretip sonra "neden çalışmıyor" demesin.
   */
  openAppPasswordPage(email) {
    const url = email
      ? `${APP_PASSWORD_URL}?authuser=${encodeURIComponent(email)}`
      : APP_PASSWORD_URL;
    openExternal(url);
    toast(
      "Google açıldı",
      "Bir ad yaz (örn. ULL-Bot) → Oluştur → 16 haneli parolayı buraya yapıştır.",
      "ok"
    );
  }

  /* --- kaydetme --- */

  gather() {
    const email = document.getElementById("a-email").value.trim();
    const host = document.getElementById("a-host").value.trim();
    const typedUser = document.getElementById("a-user").value.trim();
    const googleHost = GOOGLE_HOST.test(host);

    return {
      email,
      host,
      port: Number(document.getElementById("a-port").value) || 993,
      // Gmail kullanıcı adını asla ezmesin (alan kilitli ama yine de savun).
      username: googleHost ? email : (typedUser || email),
      // Google uygulama parolasını "abcd efgh ijkl mnop" diye gösteriyor;
      // olduğu gibi yapıştıran kullanıcı boşluklar yüzünden reddedilirdi.
      password: document.getElementById("a-pass").value.replace(/\s+/g, ""),
      use_ssl: true,
    };
  }

  /** Sunucuya gitmeden önce yakalanabilecek hatalar. `null` = sorun yok. */
  validate(payload) {
    if (!payload.email || !payload.host || !payload.password) {
      return "E-posta, sunucu ve parola gerekli.";
    }
    if (!payload.email.includes("@")) {
      return "E-posta adresi geçerli görünmüyor.";
    }
    if (GOOGLE_HOST.test(payload.host) && payload.password.length !== 16) {
      // Google uygulama parolaları tam 16 harf. Farklıysa neredeyse her
      // zaman hesap parolası girilmiştir ve sunucu "Invalid credentials"
      // der — bunu ağa çıkmadan söylemek daha yardımcı.
      return (
        `Google uygulama parolaları tam 16 karakterdir; girdiğin ` +
        `${payload.password.length} karakter. Normal hesap parolanı değil, ` +
        `"Parolayı Google'dan al" ile aldığın parolayı yapıştır.`
      );
    }
    return null;
  }

  showProblem(message) {
    document.getElementById("a-result").innerHTML =
      `<div class="notice notice-danger" style="white-space:pre-wrap">${escapeHtml(message)}</div>`;
  }

  async test() {
    const problem = this.validate(this.gather());
    if (problem) return this.showProblem(problem);

    const result = document.getElementById("a-result");
    result.innerHTML = '<p class="muted"><span class="spin"></span> bağlanılıyor…</p>';
    try {
      const data = await api.mailAccountTest(this.gather());
      result.innerHTML =
        `<div class="notice notice-info">Bağlantı başarılı — ${data.folders.length} klasör bulundu.</div>`;
    } catch (error) {
      this.showProblem(error.message);
    }
  }

  async save() {
    const payload = this.gather();
    const problem = this.validate(payload);
    if (problem) return this.showProblem(problem);

    const result = document.getElementById("a-result");
    result.innerHTML = '<p class="muted"><span class="spin"></span> doğrulanıyor ve kaydediliyor…</p>';
    try {
      await api.mailAccountAdd(payload);
      closeModal();
      toast("Hesap eklendi", "İlk senkron için Mail → Senkronla.", "ok");
      this.onSaved?.();
    } catch (error) {
      this.showProblem(error.message);
    }
  }
}
