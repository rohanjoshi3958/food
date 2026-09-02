from datetime import timedelta
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT_DIR = Path(__file__).resolve().parents[2]

# Fixed model choices — not user-configurable.
RECEIPT_ANTHROPIC_MODEL = "claude-opus-5"
MEAL_ANTHROPIC_MODEL = "claude-sonnet-5"
OPENAI_IMAGE_MODEL = "gpt-image-1"


class Settings(BaseSettings):
    database_url: str = "postgresql://postgres:postgres@localhost:5433/food"
    auth_secret: str = "change-me-in-production"
    environment: str = "development"
    cookie_secure: bool = False
    session_ttl_days: int = 7
    password_reset_ttl_minutes: int = 30
    upload_dir: str = "uploads/receipts"
    meal_upload_dir: str = "uploads/meals"
    cookbook_upload_dir: str = "uploads/cookbook"
    cors_origins: str = "http://localhost:3000,http://localhost:3001"
    anthropic_api_key: str = ""
    openai_api_key: str = ""

    model_config = SettingsConfigDict(
        env_file=str(ROOT_DIR / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    @property
    def cors_origin_list(self) -> list[str]:
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]

    @property
    def session_cookie_secure(self) -> bool:
        return self.cookie_secure or self.environment.lower() == "production"

    @property
    def session_ttl(self) -> timedelta:
        return timedelta(days=self.session_ttl_days)

    @property
    def password_reset_ttl(self) -> timedelta:
        return timedelta(minutes=self.password_reset_ttl_minutes)


def get_settings() -> Settings:
    """Always reload from env / .env so key changes apply without a full restart."""
    return Settings()


class _SettingsProxy:
    def __getattr__(self, name: str):
        return getattr(get_settings(), name)


settings = _SettingsProxy()
