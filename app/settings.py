from pathlib import Path
from typing import ClassVar

from platformdirs import user_data_dir
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    profile: str = "desktop"

    litellm_base_url: str = "http://localhost:4000"
    litellm_master_key: str = ""
    litellm_model: str = "chat-default"

    # Sağlayıcı anahtarları. Orchestrator bunlarla istek atmaz (istekler
    # LiteLLM'den geçer) — sadece "bu sağlayıcı yapılandırılmış mı?" sorusuna
    # cevap vermek ve OpenRouter probe'unu çalıştırmak için okunur.
    openrouter_api_key: str = ""
    groq_api_key: str = ""
    gemini_api_key: str = ""
    cerebras_api_key: str = ""
    mistral_api_key: str = ""

    # Faz 4: `gemini_lite` gerçek bir sağlayıcı değil, `gemini`nin ikinci
    # modeli (flash-lite) için ayrı kota muhasebesi yapabilmek adına router'ın
    # kullandığı sanal isim (bkz. DECISIONS.md "Gemini'nin iki modeli").
    # Anahtarı gerçek `gemini` sağlayıcısıyla aynı; burada eşleniyor.
    PROVIDER_KEY_ALIASES: ClassVar[dict[str, str]] = {"gemini_lite": "gemini"}

    def api_key_for(self, provider: str) -> str:
        """Sağlayıcının anahtarı (yoksa boş). Anahtarsız sağlayıcılar için 'local'."""
        if provider in {"ollama", "local"}:
            return "local"
        provider = self.PROVIDER_KEY_ALIASES.get(provider, provider)
        return str(getattr(self, f"{provider}_api_key", "") or "").strip()

    db_path: str = ""

    # --- Faz 2: ajan ve güvenlik ---
    workspace_root: str = ""
    dry_run: bool = True
    max_agent_steps: int = 15
    # Onay diyaloğu cevapsız kalırsa istek reddedilir (saniye).
    approval_timeout_seconds: int = 300
    # Araç çıktısı bu uzunluğu aşarsa ortadan kırpılır (spec §6.1/4c).
    tool_output_limit: int = 4000
    shell_timeout_seconds: int = 30

    # --- Faz 3: çoklu sağlayıcı ve kota ---
    # Kotanın bu oranı acil işler için saklanır; 0 ise quotas.yaml'daki değer
    # kullanılır (spec §5.3).
    reserve_ratio: float = 0.0
    # Bir tur içinde en fazla kaç farklı sağlayıcı denenir (429 sonrası devir).
    max_provider_attempts: int = 3

    # --- Faz 5: local model (Ollama) ---
    # LiteLLM'in `ollama_chat/` provider'ı bu adresi kullanıyor
    # (config/litellm.desktop.yaml → `chat-local`). Laptop profilinde bu,
    # masaüstündeki Ollama'ya LAN üzerinden bağlanmak için değiştirilebilir
    # (spec §5.1) — ama o opsiyon varsayılan kapalı, Faz 6'yı bekliyor.
    ollama_host: str = "http://localhost:11434"
    # Elle kapatma anahtarı: VRAM'i oyun/Blender'a bırakmak için, ya da
    # laptop profilinde local'i baştan devre dışı bırakmak için (spec §5.1
    # "laptop profilinde local tamamen çıkarılır" — Faz 6'da profil bazlı
    # otomatikleşecek, o güne kadar bu manuel anahtar).
    enable_local: bool = True

    # --- Faz 9: web aramasi (ISTEGE BAGLI) ---
    # Hicbiri doldurulmazsa arama DuckDuckGo kazimasiyla yapilir: ucretsiz ama
    # motorlar sik istekte captcha/"anomaly" sayfasi donduruyor ve arastirma
    # yarida kesiliyor. Asagidakiler sirayla denenir, ilk sonuc veren kazanir.
    #
    # 1) Kendi makinende calisan SearXNG (meta-arama). Kota yok, anahtar yok,
    #    sorgu ucuncu tarafa gitmiyor. `settings.yml`de `search.formats`
    #    icine `json` eklenmis olmali. Ornek: http://127.0.0.1:8888
    searxng_url: str = ""
    # 2) Tavily: ayda 1000 kredi, her ay yenileniyor, kart istemiyor.
    #    https://app.tavily.com/  — anahtar `tvly-` ile baslar.
    tavily_api_key: str = ""
    # 3) Brave: ayda 2000 sorgu ama kayitta kredi karti isteyebiliyor.
    #    https://api-dashboard.search.brave.com/
    brave_api_key: str = ""

    # --- Faz 8: mail (IMAP) ---
    # Arka plan senkronu; 0 ise otomatik senkron kapalı (sadece elle "yenile").
    mail_sync_interval_seconds: int = 300
    # Bir senkronda klasör başına en fazla kaç YENİ mesaj çekilir.
    mail_sync_limit: int = 200
    # Gövde bu boyutu aşarsa kırpılarak saklanır (önbellek şişmesin).
    mail_body_limit: int = 200_000
    # Yeni mail geldiğinde kural tabanlı sınıflandırıcı otomatik çalışsın mı?
    mail_auto_categorize: bool = True

    # --- Faz 8: takvim ve bildirim ---
    # Hatırlatıcı döngüsünün kontrol aralığı. Bildirimin gecikme payı bu kadar.
    calendar_poll_seconds: int = 30
    # Masaüstü bildirimleri (dunst/libnotify) açık mı?
    notifications_enabled: bool = True
    # Varsayılan hatırlatma: etkinlikten kaç dakika önce.
    default_reminder_minutes: int = 10

    # --- Faz 8: masaüstü uygulaması ---
    # Süpervizörün başlatacağı portlar. `litellm_base_url` ile tutarlı olmalı.
    api_port: int = 8080
    litellm_port: int = 4000
    # Servisler bu süre içinde ayağa kalkmazsa uygulama hata gösterir.
    service_startup_timeout: int = 90
    window_width: int = 1360
    window_height: int = 880

    @property
    def data_dir(self) -> Path:
        data_dir = Path(user_data_dir("ai-orchestrator", roaming=False))
        data_dir.mkdir(parents=True, exist_ok=True)
        return data_dir

    @property
    def resolved_db_path(self) -> Path:
        if self.db_path:
            return Path(self.db_path)
        return self.data_dir / "orchestrator.db"

    @property
    def resolved_workspace_root(self) -> Path:
        """Ajanın varsayılan çalışma dizini.

        Boş bırakılırsa `~/Projects` (yoksa ev dizini) kullanılır. Bu dizin
        `config/workspace.yaml`'daki `allowed_paths`'e her zaman eklenir.
        """
        if self.workspace_root:
            return Path(self.workspace_root).expanduser()
        projects = Path.home() / "Projects"
        return projects if projects.is_dir() else Path.home()

    @property
    def audit_log_path(self) -> Path:
        return self.data_dir / "audit.log"

    @property
    def trash_dir(self) -> Path:
        return self.data_dir / "trash"


settings = Settings()
