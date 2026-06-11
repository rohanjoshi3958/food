from datetime import datetime

from pydantic import BaseModel, EmailStr, Field


class RegisterRequest(BaseModel):
    name: str = ""
    email: EmailStr
    password: str = Field(min_length=8)


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class UserResponse(BaseModel):
    id: str
    name: str | None
    email: EmailStr

    model_config = {"from_attributes": True}


class AuthResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: UserResponse


class IngredientResponse(BaseModel):
    id: str
    name: str
    store_item_name: str | None = None
    quantity: str | None = None
    unit: str | None = None
    serving_size: str | None = None
    calories: float | None = None
    protein_g: float | None = None
    carbs_g: float | None = None
    fat_g: float | None = None
    fiber_g: float | None = None
    sodium_mg: float | None = None
    nutrition_notes: str | None = None
    receipt_id: str | None = None
    created_at: datetime | None = None

    model_config = {"from_attributes": True}


class DraftIngredientItem(BaseModel):
    store_item_name: str = ""
    ingredient_name: str
    quantity: str | None = None
    unit: str | None = None
    serving_size: str | None = None
    calories: float | None = None
    protein_g: float | None = None
    carbs_g: float | None = None
    fat_g: float | None = None
    fiber_g: float | None = None
    sodium_mg: float | None = None
    nutrition_notes: str | None = None
    is_manual: bool = False


class CreateManualIngredientRequest(BaseModel):
    ingredient_name: str = Field(min_length=1)
    quantity: str | None = None
    unit: str | None = None


class ConfirmReceiptRequest(BaseModel):
    items: list[DraftIngredientItem]


class ReceiptResponse(BaseModel):
    id: str
    original_name: str
    filename: str
    store_name: str | None = None
    analysis_status: str
    analysis_error: str | None = None
    uploaded_at: datetime
    ingredients: list[IngredientResponse] = Field(default_factory=list)
    draft_items: list[DraftIngredientItem] = Field(default_factory=list)

    model_config = {"from_attributes": True}


class MealResponse(BaseModel):
    id: str
    name: str
    description: str | None
    created_at: datetime

    model_config = {"from_attributes": True}


class CookbookEntryResponse(BaseModel):
    id: str
    title: str
    ingredients: str | None
    instructions: str | None
    created_at: datetime

    model_config = {"from_attributes": True}
