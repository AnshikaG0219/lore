"""Classification + structured extraction from caption and media text (Phase 2).

Input sent to the provider is limited to post content: caption, hashtags, creator, content type,
the user's own collection names, and text derived from attached media (OCR of images/frames and
the spoken transcript). Chat names, senders and message text are never sent.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import uuid
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from typing import Any

from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import AuditLog, ContentItem, ContentSource
from app.processing.ai.provider import Completion, LLMProvider, ProviderError
from app.processing.classification.schema import (
    CUISINES,
    GENRES,
    GOOD_FOR,
    HOWTO_DOMAINS,
    HUMOUR,
    PLACE_KINDS,
    PRICE_LEVELS,
    PROMPT_VERSION,
    REC_KINDS,
    STAYS,
    TERRAIN,
    THEMES,
    TOPICS,
    TRIP_TYPES,
    VENUE_TYPES,
    VIBES,
    Classification,
)

log = logging.getLogger(__name__)

JOB_TYPE = "caption_extraction"
MAX_CAPTION_CHARS = 6000

SYSTEM_PROMPT = f"""You classify ONE Instagram post for a personal knowledge base and \
return ONE JSON object.

Hard rules:
- Use ONLY the information provided. Never invent names, prices, addresses, locations, authors,
  dates or recommendations. Unknown → null or []. Prefer leaving a field empty to guessing.
- Every venue/place/book/product has "evidence": "explicit" (written or said in the post) or
  "inferred" (derived, e.g. a city from hashtags).
- Inputs are labelled: "caption" (by the creator), "on_screen_text" (OCR; may be noisy; never
  "correct" a name into a different one) and "spoken_transcript" (speech-to-text; may mis-hear).
  Prefer the on-screen text for names and spellings.
- If the post's actual content can't be determined, set "information_sufficient": false, say so
  honestly in the summary and lower "confidence" (one decimal).
- "tips"/"key_takeaways" only from the post itself; never add your own advice.
- Ignore places/products that only appear in an advertiser's package list or call to action.
- Locations: copy what the post says into place/area/city/state/country. Use the locality as
  written (e.g. "Hauz Khas", "Sector 18", "Indirapuram"); do NOT convert it to a zone like
  "South Delhi" yourself. Fill city/state only if stated or unambiguous from a well-known place.
- "gist": ONE short factual sentence with only the useful information (what/where/how much),
  e.g. "Kunzum Travel Café, Hauz Khas Village: pay-what-you-like coffee, quiet for work".
  Never describe the post itself ("this reel shows…", "the creator says…") and never mention
  missing information. null if the post has no useful information (memes, vague posts).
- English for title/summary/takeaways; keep proper nouns as written; "language" = ISO 639-1.
- JSON only. No markdown.

Choose ONE "post_type" (what the post is FOR):
- eat_drink: café, restaurant, bar, street food, bakery… to visit
- place: a non-food place to visit in a city: shop, market, bookstore, museum, gallery, park,
  monument, activity, studio/workshop, salon, coworking
- trip: destination, trek, itinerary, stay, travel tips
- property: flat/house/plot for sale or rent
- job: job opening, internship, hiring post
- recommendations: a list of books/films/series/music/podcasts/products/apps/courses
- how_to: tutorial, workout, recipe, money/tech/beauty/fashion/study tips
- shopping: products, brands, gifts, deals (not a venue)
- event: a dated event, workshop, concert, festival activity
- thought: quote, reflection, opinion, news, social or political commentary
- fun: meme, comedy skit, relatable/funny post, cute pets
- other: none of the above
A café inside a travel destination: "trip" if it's part of a trip guide, else "eat_drink".

"topic" (one of): {", ".join(TOPICS)}. "subtopic": short free text (e.g. "rooftop cafés").

Fill ONLY the section matching post_type (others null). For "fun" fill only fun.humour_type and
keep summary to one sentence (no takeaways/tips).

JSON shape:
{{"title": str, "gist": str|null, "summary": str, "post_type": str, "topic": str,
 "subtopic": str|null, "key_takeaways": [str], "tips": [str], "tags": [str], "language": str|null,
 "information_sufficient": bool, "confidence": float,
 "eat_drink": {{"venues": [{{"name": str, "venue_type": {"|".join(VENUE_TYPES)},
   "cuisines": [{"|".join(CUISINES)}], "dishes": [str],
   "location": LOC, "good_for": [{"|".join(GOOD_FOR)}], "vibe": [{"|".join(VIBES)}],
   "price_level": {"|".join(PRICE_LEVELS)}|null, "price_note": str|null, "notes": str|null,
   "evidence": "explicit|inferred"}}]}} | null,
 "place": {{"places": [{{"name": str, "kind": {"|".join(PLACE_KINDS)}, "location": LOC,
   "good_for": [...], "vibe": [...], "price_note": str|null, "notes": str|null,
   "evidence": "explicit|inferred"}}]}} | null,
 "trip": {{"scope": "india|international", "destinations": [LOC],
   "trip_types": [{"|".join(TRIP_TYPES)}], "terrain": [{"|".join(TERRAIN)}],
   "trek": {{"name", "difficulty": "easy|moderate|hard", "days": int, "max_altitude_m": int,
   "base": str}} | null, "places_to_visit": [{{"name", "kind", "location": LOC, "notes",
   "evidence"}}], "duration", "budget", "best_season", "how_to_reach",
   "stays": [{"|".join(STAYS)}], "permits"}} | null,
 "property": {{"deal": "buy|rent", "bhk": int, "size_sqft": int, "price": str, "project": str,
   "location": LOC}} | null,
 "job": {{"role", "company", "mode": "remote|onsite|hybrid", "location": LOC, "experience",
   "deadline", "how_to_apply"}} | null,
 "recommendations": {{"kind": {"|".join(REC_KINDS)}, "items": [{{"title", "creator",
   "genre": {"|".join(GENRES)}, "why", "evidence"}}]}} | null,
 "how_to": {{"domain": {"|".join(HOWTO_DOMAINS)}, "steps": [str], "tools": [str],
   "workout": {{"body_parts": [str], "equipment": [str]}}|null,
   "recipe": {{"name", "cuisine", "diet": "veg|non_veg|vegan|eggetarian", "time",
   "ingredients": [str]}}|null}} | null,
 "shopping": {{"products": [{{"name", "brand", "price", "where_to_buy", "evidence"}}]}} | null,
 "event": {{"what", "date", "price", "location": LOC}} | null,
 "thought": {{"theme": {"|".join(THEMES)}}} | null,
 "fun": {{"humour_type": {"|".join(HUMOUR)}}} | null}}
where LOC = {{"place": str|null, "area": str|null, "city": str|null, "state": str|null,
 "country": str|null}}"""


@dataclass(slots=True)
class ExtractionInput:
    content_id: str
    content_type: str
    creator: str | None
    caption: str
    hashtags: list[str] = field(default_factory=list)
    collections: list[str] = field(default_factory=list)
    on_screen_text: str = ""
    spoken_transcript: str = ""
    transcript_language: str | None = None

    @property
    def sources(self) -> list[str]:
        return [name for name, value in (("caption", self.caption),
                                         ("on_screen_text", self.on_screen_text),
                                         ("spoken_transcript", self.spoken_transcript)) if value]

    def prompt(self) -> str:
        payload = {
            "content_type": self.content_type,
            "creator_username": self.creator,
            "user_collections": self.collections,
            "hashtags": self.hashtags,
            "caption": self.caption[:MAX_CAPTION_CHARS] or None,
        }
        if self.on_screen_text:
            payload["on_screen_text"] = self.on_screen_text
        if self.spoken_transcript:
            payload["spoken_transcript"] = self.spoken_transcript
            payload["transcript_language"] = self.transcript_language
        return ("Extract knowledge from this Instagram post.\n"
                + json.dumps(payload, ensure_ascii=False, indent=1))

    @property
    def input_hash(self) -> str:
        data = json.dumps([PROMPT_VERSION, asdict(self)], ensure_ascii=False, sort_keys=True)
        return hashlib.sha256(data.encode()).hexdigest()


def build_input(session: Session, item: ContentItem) -> ExtractionInput | None:
    from app.processing.media.pipeline import media_text_for

    media = media_text_for(session, item.id)
    caption = (item.caption or "").strip()
    if not caption and not media.on_screen_text and not media.transcript:
        return None
    sources = session.scalars(
        select(ContentSource).where(ContentSource.content_id == item.id)
    ).all()
    collections = sorted({
        s.source_name.strip() for s in sources
        if s.source_type == "saved_collection" and s.source_name
    })
    hashtags: list[str] = []
    for s in sources:
        for tag in (s.meta or {}).get("hashtags", []):
            if tag not in hashtags:
                hashtags.append(tag)
    return ExtractionInput(
        content_id=str(item.id),
        content_type=item.content_type,
        creator=item.creator_username,
        caption=caption,
        hashtags=hashtags[:30],
        collections=collections,
        on_screen_text=media.on_screen_text,
        spoken_transcript=media.transcript,
        transcript_language=media.transcript_language,
    )


_FENCE = re.compile(r"^```(?:json)?\s*|\s*```$", re.IGNORECASE)


def parse_response(text: str) -> Classification:
    """Parse model output into the schema. Raises ValueError with a useful message."""
    cleaned = _FENCE.sub("", text.strip())
    start, end = cleaned.find("{"), cleaned.rfind("}")
    if start == -1 or end <= start:
        raise ValueError("Response did not contain a JSON object")
    try:
        data = json.loads(cleaned[start:end + 1])
    except json.JSONDecodeError as exc:
        raise ValueError(f"Invalid JSON: {exc}") from exc
    try:
        return Classification.model_validate(data)
    except ValidationError as exc:
        raise ValueError(f"Schema validation failed: {exc}") from exc


@dataclass(slots=True)
class ExtractionResult:
    extraction: Classification
    completion: Completion
    attempts: int


async def extract(provider: LLMProvider, inp: ExtractionInput,
                  max_attempts: int = 2) -> ExtractionResult:
    prompt = inp.prompt()
    last_error = ""
    for attempt in range(1, max_attempts + 1):
        completion = await provider.complete(SYSTEM_PROMPT, prompt)
        try:
            return ExtractionResult(parse_response(completion.text), completion, attempt)
        except ValueError as exc:
            last_error = str(exc)[:1500]
            log.warning("extraction_invalid_output",
                        extra={"content_id": inp.content_id, "attempt": attempt})
            prompt = (inp.prompt() + "\n\nYour previous answer was rejected: " + last_error
                      + "\nReturn ONLY a corrected JSON object matching the shape.")
    raise ValueError(f"No valid extraction after {max_attempts} attempts: {last_error}")


def is_up_to_date(item: ContentItem, inp: ExtractionInput) -> bool:
    meta = ((item.extracted_data or {}).get(JOB_TYPE) or {}).get("_meta") or {}
    return meta.get("input_hash") == inp.input_hash


def persist_success(session: Session, item: ContentItem, inp: ExtractionInput,
                    result: ExtractionResult, started_at: datetime) -> None:
    ex = result.extraction
    now = datetime.now(UTC)
    item.title = ex.title
    item.summary = ex.summary
    item.primary_category = ex.post_type
    item.secondary_category = ex.topic
    item.tags = ex.tags
    item.extraction_confidence = ex.confidence
    item.last_processed_at = now
    item.extracted_data = {
        **(item.extracted_data or {}),
        JOB_TYPE: {
            **ex.model_dump(mode="json"),
            "_meta": {
                "source": "+".join(inp.sources),
                "provider": result.completion.provider,
                "model": result.completion.model,
                "prompt_version": PROMPT_VERSION,
                "input_hash": inp.input_hash,
                "attempts": result.attempts,
                "extracted_at": now.isoformat(),
            },
        },
    }
    from app.processing.classification.archive import apply_visibility
    from app.processing.classification.facets import replace_facets

    data = ex.model_dump(mode="json")
    replace_facets(session, item.id, data)
    apply_visibility(item, data)
    session.add(AuditLog(entity_type="content_item", entity_id=item.id,
                         action=f"{JOB_TYPE}.completed",
                         details={"attempts": result.attempts, "model": result.completion.model,
                                  "sources": inp.sources, "started_at": started_at.isoformat()}))


def persist_failure(session: Session, item_id: uuid.UUID, error: str,
                    started_at: datetime) -> None:
    session.add(AuditLog(entity_type="content_item", entity_id=item_id,
                         action=f"{JOB_TYPE}.failed",
                         details={"error": error[:2000], "started_at": started_at.isoformat()}))


async def process_item(session: Session, item: ContentItem, provider: LLMProvider,
                       *, force: bool = False) -> dict[str, Any]:
    """Extract one item and commit. Never raises for provider/validation failures."""
    inp = build_input(session, item)
    if inp is None:
        return {"content_id": str(item.id), "status": "skipped", "reason": "no caption or media"}
    if not force and is_up_to_date(item, inp):
        return {"content_id": str(item.id), "status": "skipped", "reason": "up to date"}
    started = datetime.now(UTC)
    try:
        result = await extract(provider, inp)
    except (ProviderError, ValueError) as exc:
        persist_failure(session, item.id, str(exc), started)
        session.commit()
        return {"content_id": str(item.id), "status": "failed", "error": str(exc)[:300]}
    persist_success(session, item, inp, result, started)
    session.commit()
    return {"content_id": str(item.id), "status": "completed",
            "attempts": result.attempts, "category": result.extraction.post_type}
