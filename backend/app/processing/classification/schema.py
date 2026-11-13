"""Classification schema v4: post type + type-specific facets (ADR-025).

The model reports *facts from the post* (venue, neighbourhood, cuisine, state…). Groupings that
must be consistent — e.g. neighbourhood → Delhi zone → city — are derived in code by the
gazetteer, never by the model.

Choice fields are forgiving: values are lower-cased/normalised and anything outside the allowed
list maps to "other" (lists) or None (single values) instead of failing validation.
"""

from __future__ import annotations

import re
import unicodedata
from typing import Annotated, Any

from pydantic import BaseModel, BeforeValidator, ConfigDict, Field, field_validator

PROMPT_VERSION = "classification.v5"

POST_TYPES = ("eat_drink", "place", "trip", "property", "job", "recommendations", "how_to",
              "shopping", "event", "thought", "fun", "other")
PLACE_KINDS = ("shop", "market", "bookstore", "art_supplies", "museum", "art_gallery", "park",
               "monument", "temple_religious", "activity", "workshop_studio", "salon_spa",
               "gym_studio", "library", "coworking", "other")
GENRES = (
    # books
    "literary_fiction", "fiction", "mystery_thriller", "romance", "fantasy_scifi",
    "historical_fiction", "memoir_biography", "self_help", "poetry", "philosophy", "history",
    "politics_society", "feminism", "business_finance", "science", "psychology", "classics",
    # films / series
    "drama", "comedy", "thriller", "horror", "action", "documentary", "animation", "crime",
    "romcom", "short_film",
    # music
    "bollywood", "indie", "pop", "rock", "classical", "hiphop", "sufi_ghazal", "lofi",
    "devotional", "folk",
    # apps / websites / products
    "job_search", "productivity", "learning", "finance_app", "design", "ai_tool", "other",
)
TOPICS = ("food_drinks", "travel", "books", "films_music_shows", "real_estate", "jobs_career",
          "tech_ai", "money", "health_fitness", "fashion_beauty", "shopping_gifts",
          "art_craft_diy", "relationships", "self_mind", "pets_animals", "news_society",
          "humour", "other")

VENUE_TYPES = ("cafe", "restaurant", "bar", "brewery", "street_food", "bakery_dessert",
               "cloud_kitchen", "food_market", "other")
CUISINES = ("north_indian", "south_indian", "mughlai", "street_food", "chinese", "asian",
            "japanese", "korean", "thai", "italian", "continental", "mediterranean", "mexican",
            "middle_eastern", "tibetan", "himachali", "desserts", "bakery", "coffee", "healthy",
            "multi_cuisine", "other")
GOOD_FOR = ("date", "friends", "work_study", "family", "solo", "other")
VIBES = ("rooftop", "aesthetic", "live_music", "pet_friendly", "hidden_gem", "budget",
         "outdoor", "luxury", "late_night", "other")
PRICE_LEVELS = ("budget", "mid", "premium")
TRIP_SCOPE = ("india", "international")
TRIP_TYPES = ("trek", "road_trip", "mountains", "beach", "camping", "wildlife_nature",
              "heritage_culture", "spiritual", "city_break", "backpacking_solo", "luxury",
              "offbeat", "adventure_sports", "other")
TERRAIN = ("mountains", "beach", "desert", "forest", "lake", "snow", "river", "city", "other")
DIFFICULTY = ("easy", "moderate", "hard")
STAYS = ("homestay", "camp", "hostel", "hotel", "resort", "other")
DEALS = ("buy", "rent")
JOB_MODES = ("remote", "onsite", "hybrid")
REC_KINDS = ("books", "films", "series", "music", "podcasts", "products", "apps", "courses",
             "other")
HOWTO_DOMAINS = ("workout", "recipe", "tech", "money", "beauty", "fashion", "home", "study",
                 "career", "health", "other")
DIETS = ("veg", "non_veg", "vegan", "eggetarian")
THEMES = ("relationships", "self_growth", "mental_health", "motivation", "society", "politics",
          "spirituality", "humour", "other")
HUMOUR = ("meme", "skit", "relatable", "pets", "prank", "satire", "other")


def _norm(value: Any) -> str:
    """"Café / Bakery" → "cafe_bakery" (accents stripped, punctuation → underscores)."""
    text = unicodedata.normalize("NFKD", str(value)).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", "_", text.strip().lower()).strip("_")


def _one(allowed: tuple[str, ...]):
    def check(v: Any) -> str | None:
        if v is None or v == "":
            return None
        n = _norm(v)
        return n if n in allowed else ("other" if "other" in allowed else None)
    return BeforeValidator(check)


def _many(allowed: tuple[str, ...]):
    def check(v: Any) -> list[str]:
        if v is None:
            return []
        items = v if isinstance(v, list) else [v]
        out: list[str] = []
        for item in items:
            n = _norm(item)
            n = n if n in allowed else ("other" if "other" in allowed else None)
            if n and n not in out:
                out.append(n)
        return out
    return BeforeValidator(check)


def _int(v: Any) -> int | None:
    if v is None or v == "":
        return None
    m = re.search(r"\d[\d,]*", str(v))
    return int(m.group().replace(",", "")) if m else None


One = lambda allowed: Annotated[str | None, _one(allowed)]  # noqa: E731
Many = lambda allowed: Annotated[list[str], _many(allowed)]  # noqa: E731
Int = Annotated[int | None, BeforeValidator(_int)]
Evidence = Annotated[str, BeforeValidator(lambda v: "explicit" if _norm(v) != "inferred"
                                          else "inferred")]


class _M(BaseModel):
    model_config = ConfigDict(extra="ignore")


class Location(_M):
    """As written in the post. Normalised to city/zone/state/country by the gazetteer."""
    place: str | None = None   # e.g. "Hauz Khas Village", "Pangong Lake"
    area: str | None = None    # neighbourhood / sector / locality
    city: str | None = None
    state: str | None = None
    country: str | None = None


class Venue(_M):
    name: str
    venue_type: One(VENUE_TYPES) = None
    cuisines: Many(CUISINES) = []
    dishes: list[str] = []
    location: Location = Field(default_factory=Location)
    good_for: Many(GOOD_FOR) = []
    vibe: Many(VIBES) = []
    price_level: One(PRICE_LEVELS) = None
    price_note: str | None = None
    notes: str | None = None
    evidence: Evidence = "explicit"


class EatDrink(_M):
    venues: list[Venue] = []


class PlaceVisit(_M):
    """Non-food place to visit: shop, market, museum, park, activity, studio…"""
    name: str
    kind: One(PLACE_KINDS) = None
    location: Location = Field(default_factory=Location)
    good_for: Many(GOOD_FOR) = []
    vibe: Many(VIBES) = []
    price_note: str | None = None
    notes: str | None = None
    evidence: Evidence = "explicit"


class Places(_M):
    places: list[PlaceVisit] = []


class Trek(_M):
    name: str | None = None
    difficulty: One(DIFFICULTY) = None
    days: Int = None
    max_altitude_m: Int = None
    base: str | None = None


class TripPlace(_M):
    name: str
    kind: str | None = None  # lake, pass, village, monastery, beach…
    location: Location = Field(default_factory=Location)
    notes: str | None = None
    evidence: Evidence = "explicit"


class Trip(_M):
    scope: One(TRIP_SCOPE) = None
    destinations: list[Location] = []
    trip_types: Many(TRIP_TYPES) = []
    terrain: Many(TERRAIN) = []
    trek: Trek | None = None
    places_to_visit: list[TripPlace] = []
    duration: str | None = None
    budget: str | None = None
    best_season: str | None = None
    how_to_reach: str | None = None
    stays: Many(STAYS) = []
    permits: str | None = None


class Property(_M):
    deal: One(DEALS) = None
    bhk: Int = None
    size_sqft: Int = None
    price: str | None = None
    project: str | None = None
    location: Location = Field(default_factory=Location)


class Job(_M):
    role: str | None = None
    company: str | None = None
    mode: One(JOB_MODES) = None
    location: Location = Field(default_factory=Location)
    experience: str | None = None
    deadline: str | None = None
    how_to_apply: str | None = None


class RecItem(_M):
    title: str
    creator: str | None = None
    genre: One(GENRES) = None
    why: str | None = None
    evidence: Evidence = "explicit"


class Recommendations(_M):
    kind: One(REC_KINDS) = None
    items: list[RecItem] = []


class Workout(_M):
    body_parts: list[str] = []
    equipment: list[str] = []


class Recipe(_M):
    name: str | None = None
    cuisine: One(CUISINES) = None
    diet: One(DIETS) = None
    time: str | None = None
    ingredients: list[str] = []


class HowTo(_M):
    domain: One(HOWTO_DOMAINS) = None
    steps: list[str] = []
    tools: list[str] = []
    workout: Workout | None = None
    recipe: Recipe | None = None


class Product(_M):
    name: str
    brand: str | None = None
    price: str | None = None
    where_to_buy: str | None = None
    evidence: Evidence = "explicit"


class Shopping(_M):
    products: list[Product] = []


class Event(_M):
    what: str | None = None
    date: str | None = None
    price: str | None = None
    location: Location = Field(default_factory=Location)


class Thought(_M):
    theme: One(THEMES) = None


class Fun(_M):
    humour_type: One(HUMOUR) = None


class Classification(_M):
    title: str = Field(max_length=200)
    # One factual sentence of what's useful, no meta ("this reel shows…", "the caption
    # doesn't say…"). Empty when there's nothing useful.
    gist: str | None = None
    summary: str
    post_type: Annotated[str, _one(POST_TYPES)]
    topic: Annotated[str, _one(TOPICS)]
    subtopic: str | None = None
    key_takeaways: list[str] = []
    tips: list[str] = []
    tags: list[str] = Field(default_factory=list, max_length=15)
    language: str | None = None
    information_sufficient: bool
    confidence: float = Field(ge=0, le=1)

    eat_drink: EatDrink | None = None
    place: Places | None = None
    trip: Trip | None = None
    property: Property | None = None
    job: Job | None = None
    recommendations: Recommendations | None = None
    how_to: HowTo | None = None
    shopping: Shopping | None = None
    event: Event | None = None
    thought: Thought | None = None
    fun: Fun | None = None

    @field_validator("tags")
    @classmethod
    def _dedupe_tags(cls, tags: list[str]) -> list[str]:
        seen, out = set(), []
        for t in (t.strip() for t in tags if isinstance(t, str)):
            if t and t.lower() not in seen:
                seen.add(t.lower())
                out.append(t)
        return out
