from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

BACKEND_DIR = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=BACKEND_DIR / ".env", extra="ignore")

    database_url: str = f"sqlite:///{(BACKEND_DIR / 'inventory.db').as_posix()}"
    anthropic_api_key: str | None = None
    llm_model: str = "claude-opus-5"
    forecast_horizon_days: int = 28
    service_level_z: float = 1.645  # 95% cycle service level
    overstock_days_threshold: int = 90
    review_period_days: int = 14
    cors_origins: list[str] = ["http://localhost:5173"]
    seed_on_startup: bool = True
    retrain_interval_hours: float = 24.0  # 0 disables scheduled retraining
    alert_webhook_url: str | None = None  # Slack-compatible incoming webhook for stockout alerts
    chat_max_steps: int = 8
    max_upload_mb: int = 20


@lru_cache
def get_settings() -> Settings:
    return Settings()
