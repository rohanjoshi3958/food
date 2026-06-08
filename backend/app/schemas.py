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


class ReceiptResponse(BaseModel):
    id: str
    original_name: str
    filename: str
    uploaded_at: datetime

    model_config = {"from_attributes": True}


class IngredientResponse(BaseModel):
    id: str
    name: str
    quantity: str | None
    unit: str | None
    created_at: datetime

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
