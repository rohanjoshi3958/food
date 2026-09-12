from datetime import timedelta
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT_DIR = Path(__file__).resolve().parents[2]

# Claude tiers, strongest to cheapest. Anthropic API aliases (dateless ids);
# see backend/MODEL_ROUTING.md for which call site may use which tier.
OPUS_ANTHROPIC_MODEL = "claude-opus-5"
SONNET_ANTHROPIC_MODEL = "claude-sonnet-5"
HAIKU_ANTHROPIC_MODEL = "claude-haiku-4-5"

# Fixed defaults per pipeline — not user-configurable. These are what every
# call site uses while MODEL_ROUTING_ENABLED is off (the shipped state).
RECEIPT_ANTHROPIC_MODEL = OPUS_ANTHROPIC_MODEL
MEAL_ANTHROPIC_MODEL = SONNET_ANTHROPIC_MODEL
# Cheap tier reserved for the receipt pipeline's text-only steps (soft OCR
# cleanup, classify/extract over OCR text). FOOD-55 should import this rather
# than hard-code a model id.
RECEIPT_HAIKU_MODEL = HAIKU_ANTHROPIC_MODEL
OPENAI_IMAGE_MODEL = "gpt-image-1"


class Settings(BaseSettings):
    database_url: str = "postgresql://postgres:postgres@localhost:5433/food"
    auth_secret: str = "change-me-in-production"
    environment: str = "development"
    cookie_secure: bool = False
    session_ttl_days: int = 7
    password_reset_ttl_minutes: int = 10
    upload_dir: str = "uploads/receipts"
    meal_upload_dir: str = "uploads/meals"
    cookbook_upload_dir: str = "uploads/cookbook"
    cors_origins: str = "http://localhost:3000,http://localhost:3001"
    anthropic_api_key: str = ""
    openai_api_key: str = ""
    resend_api_key: str = ""
    email_from: str = "Food <onboarding@resend.dev>"
    frontend_url: str = "http://localhost:3000"
    # FOOD-58: when off (default) every call site uses its RECEIPT_/MEAL_
    # default above and the router only logs. When on, the routed tiers in
    # app/services/model_router.py apply. Flip only after the live evals in
    # backend/tests/evals are green for the cheaper tier.
    model_routing_enabled: bool = False

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
