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

from app.db.models import ContentItem, ContentSource, ProcessingJob
from app.processing.ai.provider import Completion, LLMProvider, ProviderError
from app.processing.extraction.schema import PROMPT_VERSION, CaptionExtraction, Category

log = logging.getLogger(__name__)

JOB_TYPE = "caption_extraction"
MAX_CAPTION_CHARS = 6000

SYSTEM_PROMPT = f"""You are the extraction engine of a personal knowledge base built from saved \
Instagram posts. You receive ONE post's caption and metadata and return ONE JSON object.

Hard rules:
- Use ONLY the information provided. Never invent names, prices, addresses, locations, authors or
  recommendations. If something is not stated, use null or an empty list.
- Mark every entity/place/book/product with "evidence": "explicit" if it is written in the input,
  or "inferred" if you derived it (e.g. a city implied by hashtags). Prefer explicit.
- Sources are labelled: "caption" (written by the creator), "on_screen_text" (OCR of images or
  video frames; may contain OCR noise or misspellings, so ignore garbled fragments and never
  "correct" a name into a different one), and "spoken_transcript" (speech-to-text, which may
  contain mis-heard words). Prefer facts confirmed by several sources. If they conflict, prefer
  the on-screen text for names and spellings and mention the conflict in the summary.
- The caption may not describe the video. If the caption alone does not reveal the actual content
  (e.g. "wait for it", only hashtags, emojis), set "information_sufficient": false, keep the
  summary honest about that, and lower "confidence".
- "tips" and "key_takeaways" must come from the post itself. Do NOT add your own general advice
  (e.g. "check opening hours", "watch the video"); if the post gives no tips, return [].
- Do not list places/products that only appear in an advertiser's package list or call-to-action
  unless the post actually recommends them.
- Extract reusable knowledge, not just a summary: a post listing six cafés yields six places; a
  book list yields each book.
- "confidence" is your 0-1 confidence in the classification and extraction. Avoid false precision
  (use one decimal).
- Write title/summary/takeaways in English, even if the caption is in another language; keep
  proper nouns as written. Set "language" to the caption's main language (ISO 639-1).
- Respond with the JSON object only. No markdown, no commentary.

Allowed categories: {", ".join(c.value for c in Category)}.

JSON shape:
{{"title": str, "summary": str, "primary_category": category, "secondary_category": category|null,
 "subcategory": str|null, "key_takeaways": [str], "entities": [{{"name", "entity_type", "location",
 "description", "evidence"}}], "extracted_information": {{"places": [{{"name", "place_type",
 "location", "notes", "evidence"}}],
 "books": [{{"title", "author", "genre", "reason", "evidence"}}],
 "films_and_shows": [entity], "products": [{{"name", "brand", "price", "evidence"}}],
 "tools": [entity], "recipe": {{"name", "ingredients": [str], "steps": [str]}}|null,
 "tips": [str]}}, "tags": [str], "language": str|null, "information_sufficient": bool,
 "confidence": float}}"""


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


def parse_response(text: str) -> CaptionExtraction:
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
        return CaptionExtraction.model_validate(data)
    except ValidationError as exc:
        raise ValueError(f"Schema validation failed: {exc}") from exc


@dataclass(slots=True)
class ExtractionResult:
    extraction: CaptionExtraction
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
    item.primary_category = ex.primary_category.value
    item.secondary_category = ex.secondary_category.value if ex.secondary_category else None
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
    session.add(ProcessingJob(content_id=item.id, job_type=JOB_TYPE, stage="STRUCTURING",
                              status="completed", retry_count=result.attempts - 1,
                              started_at=started_at, completed_at=now))


def persist_failure(session: Session, item_id: uuid.UUID, error: str,
                    started_at: datetime) -> None:
    session.add(ProcessingJob(content_id=item_id, job_type=JOB_TYPE, stage="STRUCTURING",
                              status="failed", error_message=error[:2000],
                              started_at=started_at, completed_at=datetime.now(UTC)))


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
            "attempts": result.attempts, "category": result.extraction.primary_category.value}
