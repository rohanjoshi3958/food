from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT_DIR = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    database_url: str = "postgresql://postgres:postgres@localhost:5433/food"
    auth_secret: str = "change-me-in-production"
    upload_dir: str = "uploads/receipts"
    cors_origins: str = "http://localhost:3000,http://localhost:3001"
    anthropic_api_key: str = ""
    anthropic_model: str = "claude-sonnet-4-20250514"

    model_config = SettingsConfigDict(
        env_file=str(ROOT_DIR / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    @property
    def cors_origin_list(self) -> list[str]:
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]


settings = Settings()
