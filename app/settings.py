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

    @property
    def resolved_db_path(self) -> Path:
        if self.db_path:
            return Path(self.db_path)
        data_dir = Path(user_data_dir("ai-orchestrator", roaming=False))
        data_dir.mkdir(parents=True, exist_ok=True)
        return data_dir / "orchestrator.db"


settings = Settings()
