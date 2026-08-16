from pathlib import Path

from platformdirs import user_data_dir
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    profile: str = "desktop"

    litellm_base_url: str = "http://localhost:4000"
    litellm_master_key: str = ""
    litellm_model: str = "chat-default"

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
