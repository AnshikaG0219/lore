"""Automatic hiding of posts that carry no reusable information (user decision, 2026-11-11).

Hidden, never deleted: `archived_at` + `archive_reason`. A user's explicit keep/hide
(`visibility_override`) always wins over the rule.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from app.db.models import ContentItem

HIDE_TYPES = {"fun": "fun", "thought": "thought"}


def has_useful_facts(c: dict[str, Any]) -> bool:
    """Concrete, reusable facts: a named venue/place/destination/property/job/item/product, or
    a real recipe/how-to (at least 2 steps or ingredients)."""
    def named(items):
        return any(isinstance(i, dict) and (i.get("name") or i.get("title")) for i in items or [])

    trip = c.get("trip") or {}
    dests = [d for d in trip.get("destinations") or [] if any(d.get(k) for k in
             ("place", "area", "city", "state", "country"))]
    prop = c.get("property") or {}
    job = c.get("job") or {}
    how = c.get("how_to") or {}
    recipe = how.get("recipe") or {}
    event = c.get("event") or {}
    return any([
        named((c.get("eat_drink") or {}).get("venues")),
        named((c.get("place") or {}).get("places")),
        bool(dests) or named(trip.get("places_to_visit")),
        any(prop.get(k) for k in ("bhk", "price", "project", "size_sqft")),
        bool(job.get("role") or job.get("company")),
        named((c.get("recommendations") or {}).get("items")),
        named((c.get("shopping") or {}).get("products")),
        len(how.get("steps") or []) >= 2 or len(recipe.get("ingredients") or []) >= 2,
        bool(event.get("what") and (event.get("date") or (event.get("location") or {}))),
    ])


def auto_reason(classification: dict[str, Any]) -> str | None:
    post_type = classification.get("post_type")
    if post_type in HIDE_TYPES:
        return HIDE_TYPES[post_type]
    # "Unclear" alone isn't enough to hide: keep low-confidence posts that still have facts.
    if classification.get("information_sufficient") is False and \
            not has_useful_facts(classification):
        return "unclear"
    return None


def apply_visibility(item: ContentItem, classification: dict[str, Any] | None) -> None:
    if item.visibility_override == "keep":
        item.archived_at = item.archive_reason = None
        return
    if item.visibility_override == "hide":
        item.archived_at = item.archived_at or datetime.now(UTC)
        item.archive_reason = "manual"
        return
    reason = auto_reason(classification or {})
    if reason:
        item.archived_at = item.archived_at or datetime.now(UTC)
        item.archive_reason = reason
    else:
        item.archived_at = item.archive_reason = None


def set_override(item: ContentItem, choice: str | None, classification: dict | None) -> None:
    """choice: "keep" | "hide" | None (back to automatic)."""
    item.visibility_override = choice
    apply_visibility(item, classification)
