from pydantic import BaseModel, Field, HttpUrl


class Ingredient(BaseModel):
    name: str
    quantity: str


class Menu(BaseModel):
    id: str
    name: str
    cook_with: str
    main_ingredient: str
    category: str
    is_high_effort: bool
    uses_perishable_ingredients: bool
    seasonality: str
    ingredients: list[Ingredient]
    reference_url: HttpUrl | None = None
    image_filename: str | None = None
    cooking_note: str | None = None


class MenuSearchCriteria(BaseModel):
    main_ingredient: str | None = None
    category: str | None = None
    cook_with: str | None = None
    max_items: int = Field(default=10, ge=1, le=20)


class MenuSummary(BaseModel):
    id: str
    name: str
    cook_with: str
    main_ingredient: str
    category: str


class ChatRequest(BaseModel):
    message: str = Field(min_length=1)
    thread_id: str | None = None


class ChatResponse(BaseModel):
    message: str
    thread_id: str | None = None
    source: str
