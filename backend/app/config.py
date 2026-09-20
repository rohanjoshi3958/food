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
# Cheap tier for the receipt pipeline's text-only steps (soft OCR cleanup,
# classify/extract over OCR text).
RECEIPT_HAIKU_MODEL = HAIKU_ANTHROPIC_MODEL
OPENAI_IMAGE_MODEL = "gpt-image-1"
# FOOD-55 OCR-first escalation ladder (only used when RECEIPT_OCR_FIRST is on).
# Soft fail: cheap text model cleans up the Tesseract output.
RECEIPT_OCR_CLEANUP_MODEL = RECEIPT_HAIKU_MODEL
# Hard fail: Sonnet vision on the downsampled image instead of the Opus extract.
RECEIPT_OCR_VISION_FALLBACK_MODEL = SONNET_ANTHROPIC_MODEL


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

    # FOOD-55 receipt pipeline flags. Both default OFF so production keeps the
    # vision-Opus baseline until we deliberately flip them.
    receipt_analysis_cache: bool = False
    receipt_ocr_first: bool = False
    # OCR-first tuning (only used when receipt_ocr_first is on).
    receipt_ocr_min_confidence: float = 60.0  # Tesseract mean word confidence, 0-100
    receipt_ocr_max_missing_qty_unit_ratio: float = 0.5
    receipt_ocr_totals_tolerance: float = 0.02  # relative; absolute floor is $0.05
    receipt_ocr_tesseract_config: str = "--psm 4"
    receipt_ocr_tesseract_cmd: str = ""  # optional path override for the binary
    receipt_vision_long_edge: int = 1600

    # LLM usage instrumentation / cost dashboard (FOOD-54).
    # Emit one JSON line per Claude call to stdout in addition to the DB store.
    llm_usage_log_enabled: bool = True
    # Optional JSON override of the USD/MTok pricing table (see app/llm_usage/pricing.py).
    llm_pricing_json: str = ""
    # Who may read /api/metrics/llm/*: comma-separated emails of signed-in
    # users, and/or a static bearer token for ops tooling. When neither is set
    # outside production, any signed-in user may view the dashboard locally.
    admin_emails: str = ""
    metrics_api_token: str = ""
    # Anthropic Admin API key (sk-ant-admin...) for billing reconciliation.
    anthropic_admin_api_key: str = ""

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
