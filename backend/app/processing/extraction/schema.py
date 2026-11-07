"""Validated output schema for caption-based classification and extraction.

Rules from the PRD: never invent facts; unknowns are null; every entity says whether it was
explicitly stated or inferred.
"""

from __future__ import annotations

import enum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

PROMPT_VERSION = "content-extraction.v3"


class Category(enum.StrEnum):
    TRAVEL = "travel"
    FOOD_AND_RECIPES = "food_and_recipes"
    BOOKS_AND_READING = "books_and_reading"
    TECHNOLOGY_AND_AI = "technology_and_ai"
    CAREER_AND_LEARNING = "career_and_learning"
    BUSINESS_AND_ENTREPRENEURSHIP = "business_and_entrepreneurship"
    FINANCE_AND_INVESTING = "finance_and_investing"
    SHOPPING_AND_PRODUCTS = "shopping_and_products"
    FASHION_AND_BEAUTY = "fashion_and_beauty"
    HEALTH_AND_FITNESS = "health_and_fitness"
    PRODUCTIVITY = "productivity"
    LIFESTYLE = "lifestyle"
    HOME_AND_INTERIOR = "home_and_interior"
    ENTERTAINMENT = "entertainment"
    EDUCATION = "education"
    MISCELLANEOUS = "miscellaneous"


Evidence = Literal["explicit", "inferred"]


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Entity(_Strict):
    name: str
    entity_type: str = Field(description="e.g. cafe, restaurant, place, book, film, product, tool")
    location: str | None = None
    description: str | None = None
    evidence: Evidence


class Place(_Strict):
    name: str
    place_type: str | None = None
    location: str | None = None
    notes: str | None = None
    evidence: Evidence


class Book(_Strict):
    title: str
    author: str | None = None
    genre: str | None = None
    reason: str | None = None
    evidence: Evidence


class Product(_Strict):
    name: str
    brand: str | None = None
    price: str | None = None
    evidence: Evidence


class Recipe(_Strict):
    name: str | None = None
    ingredients: list[str] = Field(default_factory=list)
    steps: list[str] = Field(default_factory=list)


class ExtractedInformation(_Strict):
    places: list[Place] = Field(default_factory=list)
    books: list[Book] = Field(default_factory=list)
    films_and_shows: list[Entity] = Field(default_factory=list)
    products: list[Product] = Field(default_factory=list)
    tools: list[Entity] = Field(default_factory=list)
    recipe: Recipe | None = None
    tips: list[str] = Field(default_factory=list)


class CaptionExtraction(_Strict):
    title: str = Field(max_length=200)
    summary: str
    primary_category: Category
    secondary_category: Category | None = None
    subcategory: str | None = None
    key_takeaways: list[str] = Field(default_factory=list)
    entities: list[Entity] = Field(default_factory=list)
    extracted_information: ExtractedInformation = Field(default_factory=ExtractedInformation)
    tags: list[str] = Field(default_factory=list, max_length=15)
    language: str | None = None
    information_sufficient: bool = Field(
        description="False when the caption alone is too thin to know what the post contains"
    )
    confidence: float = Field(ge=0, le=1)

    @field_validator("tags")
    @classmethod
    def _dedupe_tags(cls, tags: list[str]) -> list[str]:
        seen, out = set(), []
        for t in (t.strip() for t in tags):
            if t and t.lower() not in seen:
                seen.add(t.lower())
                out.append(t)
        return out
