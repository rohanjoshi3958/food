import uuid
from datetime import UTC, datetime

from sqlalchemy import JSON, Boolean, DateTime, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


def utcnow() -> datetime:
    return datetime.now(UTC)


def new_id() -> str:
    return str(uuid.uuid4())


class User(Base):
    __tablename__ = "User"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=new_id)
    name: Mapped[str | None] = mapped_column(String, nullable=True)
    email: Mapped[str] = mapped_column(String, unique=True, index=True)
    emailVerified: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    image: Mapped[str | None] = mapped_column(String, nullable=True)
    password: Mapped[str | None] = mapped_column(String, nullable=True)
    createdAt: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )
    updatedAt: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False
    )

    receipts: Mapped[list["Receipt"]] = relationship(back_populates="user")
    ingredients: Mapped[list["Ingredient"]] = relationship(back_populates="user")
    meals: Mapped[list["Meal"]] = relationship(back_populates="user")
    cookbook_entries: Mapped[list["CookbookEntry"]] = relationship(back_populates="user")
    sessions: Mapped[list["AuthSession"]] = relationship(back_populates="user")
    reset_tokens: Mapped[list["PasswordResetToken"]] = relationship(back_populates="user")


class AuthSession(Base):
    __tablename__ = "auth_sessions"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(String, ForeignKey("User.id", ondelete="CASCADE"))
    token_hash: Mapped[str] = mapped_column(String, unique=True, index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    user: Mapped["User"] = relationship(back_populates="sessions")


class PasswordResetToken(Base):
    __tablename__ = "password_reset_tokens"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(String, ForeignKey("User.id", ondelete="CASCADE"))
    token_hash: Mapped[str] = mapped_column(String, unique=True, index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    user: Mapped["User"] = relationship(back_populates="reset_tokens")


class Receipt(Base):
    __tablename__ = "receipts"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(String, ForeignKey("User.id", ondelete="CASCADE"))
    filename: Mapped[str] = mapped_column(String, nullable=False)
    original_name: Mapped[str] = mapped_column(String, nullable=False)
    store_name: Mapped[str | None] = mapped_column(String, nullable=True)
    analysis_status: Mapped[str] = mapped_column(String, default="pending", nullable=False)
    analysis_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    draft_items: Mapped[list | None] = mapped_column(JSON, nullable=True)
    uploaded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )

    user: Mapped["User"] = relationship(back_populates="receipts")
    ingredients: Mapped[list["Ingredient"]] = relationship(back_populates="receipt")


class Ingredient(Base):
    __tablename__ = "ingredients"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(String, ForeignKey("User.id", ondelete="CASCADE"))
    receipt_id: Mapped[str | None] = mapped_column(
        String, ForeignKey("receipts.id", ondelete="SET NULL"), nullable=True
    )
    name: Mapped[str] = mapped_column(String, nullable=False)
    store_item_name: Mapped[str | None] = mapped_column(String, nullable=True)
    quantity: Mapped[str | None] = mapped_column(String, nullable=True)
    original_quantity: Mapped[str | None] = mapped_column(String, nullable=True)
    unit: Mapped[str | None] = mapped_column(String, nullable=True)
    serving_size: Mapped[str | None] = mapped_column(String, nullable=True)
    servings_per_container: Mapped[float | None] = mapped_column(Float, nullable=True)
    calories: Mapped[float | None] = mapped_column(Float, nullable=True)
    protein_g: Mapped[float | None] = mapped_column(Float, nullable=True)
    carbs_g: Mapped[float | None] = mapped_column(Float, nullable=True)
    fat_g: Mapped[float | None] = mapped_column(Float, nullable=True)
    fiber_g: Mapped[float | None] = mapped_column(Float, nullable=True)
    sodium_mg: Mapped[float | None] = mapped_column(Float, nullable=True)
    nutrition_notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )

    user: Mapped["User"] = relationship(back_populates="ingredients")
    receipt: Mapped["Receipt | None"] = relationship(back_populates="ingredients")


class Meal(Base):
    __tablename__ = "meals"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=new_id)
    name: Mapped[str] = mapped_column(String, nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    ingredients_used: Mapped[str | None] = mapped_column(Text, nullable=True)
    ingredients_used_data: Mapped[list | None] = mapped_column(JSON, nullable=True)
    instructions: Mapped[str | None] = mapped_column(Text, nullable=True)
    photo_filename: Mapped[str | None] = mapped_column(String, nullable=True)
    calories: Mapped[float | None] = mapped_column(Float, nullable=True)
    protein_g: Mapped[float | None] = mapped_column(Float, nullable=True)
    carbs_g: Mapped[float | None] = mapped_column(Float, nullable=True)
    fat_g: Mapped[float | None] = mapped_column(Float, nullable=True)
    fiber_g: Mapped[float | None] = mapped_column(Float, nullable=True)
    sodium_mg: Mapped[float | None] = mapped_column(Float, nullable=True)
    user_id: Mapped[str] = mapped_column(String, ForeignKey("User.id", ondelete="CASCADE"))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )

    user: Mapped["User"] = relationship(back_populates="meals")


class CookbookEntry(Base):
    __tablename__ = "cookbook_entries"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(String, ForeignKey("User.id", ondelete="CASCADE"))
    meal_id: Mapped[str | None] = mapped_column(
        String, ForeignKey("meals.id", ondelete="SET NULL"), nullable=True
    )
    title: Mapped[str] = mapped_column(String, nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    ingredients: Mapped[str | None] = mapped_column(Text, nullable=True)
    instructions: Mapped[str | None] = mapped_column(Text, nullable=True)
    photo_filename: Mapped[str | None] = mapped_column(String, nullable=True)
    calories: Mapped[float | None] = mapped_column(Float, nullable=True)
    protein_g: Mapped[float | None] = mapped_column(Float, nullable=True)
    carbs_g: Mapped[float | None] = mapped_column(Float, nullable=True)
    fat_g: Mapped[float | None] = mapped_column(Float, nullable=True)
    fiber_g: Mapped[float | None] = mapped_column(Float, nullable=True)
    sodium_mg: Mapped[float | None] = mapped_column(Float, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )

    user: Mapped["User"] = relationship(back_populates="cookbook_entries")


class LlmWorkflowRun(Base):
    """One product-level LLM workflow invocation (e.g. a receipt parse).

    A run groups every Claude call made while fulfilling it, including retries,
    so we can report $ per *successful* receipt / normalize / meal and how
    often a workflow needed more than one call.
    """

    __tablename__ = "llm_workflow_runs"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=new_id)
    workflow: Mapped[str] = mapped_column(String, nullable=False, index=True)
    status: Mapped[str] = mapped_column(String, nullable=False, default="running")
    # Existing app identifiers only; no new PII.
    user_id: Mapped[str | None] = mapped_column(String, nullable=True, index=True)
    receipt_id: Mapped[str | None] = mapped_column(String, nullable=True)
    meal_id: Mapped[str | None] = mapped_column(String, nullable=True)
    error_type: Mapped[str | None] = mapped_column(String, nullable=True)
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False, index=True
    )
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)


class LlmUsageEvent(Base):
    """One Claude Messages API call with its token usage and estimated cost."""

    __tablename__ = "llm_usage_events"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=new_id)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False, index=True
    )
    workflow: Mapped[str] = mapped_column(String, nullable=False, index=True)
    step: Mapped[str] = mapped_column(String, nullable=False)
    run_id: Mapped[str | None] = mapped_column(String, nullable=True, index=True)
    attempt: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    provider: Mapped[str] = mapped_column(String, nullable=False, default="anthropic")
    model: Mapped[str] = mapped_column(String, nullable=False, index=True)
    # Pipeline route that produced this step: cache | ocr | haiku | sonnet | opus.
    # Claude calls infer it from the model; OCR-first paths (FOOD-55) set it.
    route: Mapped[str | None] = mapped_column(String, nullable=True)
    # Optional 0..1 confidence reported by the step (OCR / matcher), if any.
    confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    service_tier: Mapped[str | None] = mapped_column(String, nullable=True)
    status: Mapped[str] = mapped_column(String, nullable=False, default="ok")
    error_type: Mapped[str | None] = mapped_column(String, nullable=True)
    stop_reason: Mapped[str | None] = mapped_column(String, nullable=True)
    latency_ms: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    max_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)

    uncached_input_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    cache_write_5m_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    cache_write_1h_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    cache_read_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    image_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    document_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    approx_visual_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    input_text_chars: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    estimated_cost_usd: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    input_cost_usd: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    cache_write_cost_usd: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    cache_read_cost_usd: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    output_cost_usd: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    pricing_known: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    pricing_version: Mapped[str | None] = mapped_column(String, nullable=True)

    user_id: Mapped[str | None] = mapped_column(String, nullable=True, index=True)
    receipt_id: Mapped[str | None] = mapped_column(String, nullable=True)
    meal_id: Mapped[str | None] = mapped_column(String, nullable=True)
    anthropic_request_id: Mapped[str | None] = mapped_column(String, nullable=True)
