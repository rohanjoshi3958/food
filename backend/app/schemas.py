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
    servings_per_container: float | None = None
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
    servings_per_container: float | None = None
    calories: float | None = None
    protein_g: float | None = None
    carbs_g: float | None = None
    fat_g: float | None = None
    fiber_g: float | None = None
    sodium_mg: float | None = None
    nutrition_notes: str | None = None
    is_manual: bool = False
    is_food: bool = True


class CreateManualIngredientRequest(BaseModel):
    ingredient_name: str = Field(min_length=1)
    quantity: str | None = None
    unit: str | None = None


class ConfirmReceiptRequest(BaseModel):
    items: list[DraftIngredientItem]


class PreviousMealContext(BaseModel):
    name: str
    description: str | None = None
    ingredients_used: str | None = None
    instructions: str | None = None


class GenerateMealRequest(BaseModel):
    previous_meal: PreviousMealContext | None = None


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
    ingredients_used: str | None = None
    instructions: str | None = None
    photo_url: str | None = None
    calories: float | None = None
    protein_g: float | None = None
    carbs_g: float | None = None
    fat_g: float | None = None
    fiber_g: float | None = None
    sodium_mg: float | None = None
    created_at: datetime

    model_config = {"from_attributes": True}


def meal_response(meal) -> MealResponse:
    return MealResponse(
        id=meal.id,
        name=meal.name,
        description=meal.description,
        ingredients_used=meal.ingredients_used,
        instructions=meal.instructions,
        photo_url=f"/api/meals/{meal.id}/photo" if meal.photo_filename else None,
        calories=meal.calories,
        protein_g=meal.protein_g,
        carbs_g=meal.carbs_g,
        fat_g=meal.fat_g,
        fiber_g=meal.fiber_g,
        sodium_mg=meal.sodium_mg,
        created_at=meal.created_at,
    )


class CookbookEntryResponse(BaseModel):
    id: str
    title: str
    description: str | None = None
    ingredients: str | None
    instructions: str | None
    photo_url: str | None = None
    calories: float | None = None
    protein_g: float | None = None
    carbs_g: float | None = None
    fat_g: float | None = None
    fiber_g: float | None = None
    sodium_mg: float | None = None
    created_at: datetime

    model_config = {"from_attributes": True}


def cookbook_entry_response(entry) -> CookbookEntryResponse:
    return CookbookEntryResponse(
        id=entry.id,
        title=entry.title,
        description=entry.description,
        ingredients=entry.ingredients,
        instructions=entry.instructions,
        photo_url=f"/api/cookbook/{entry.id}/photo" if entry.photo_filename else None,
        calories=entry.calories,
        protein_g=entry.protein_g,
        carbs_g=entry.carbs_g,
        fat_g=entry.fat_g,
        fiber_g=entry.fiber_g,
        sodium_mg=entry.sodium_mg,
        created_at=entry.created_at,
    )
