"""Classify export members by path, falling back to JSON shape.

Path rules use suffix matching so the export's top-level folder name (which differs per
account/export) and layout changes (e.g. the newer ``your_instagram_activity/`` prefix)
do not matter.
"""

from __future__ import annotations

import re
from typing import Any

from app.ingestion.instagram.models import FileCategory

_MEDIA_EXT = (
    ".jpg", ".jpeg", ".png", ".webp", ".gif", ".heic", ".mp4", ".mov", ".m4a", ".mp3",
    ".aac", ".wav", ".srt", ".vtt",
)
_MESSAGE_FILE = re.compile(r"(^|/)messages/[^/]+/[^/]+/message_\d+\.json$", re.IGNORECASE)


def classify_path(path: str) -> FileCategory | None:
    """Classify from the path alone. None means "JSON, inspect content to decide"."""
    lower = path.lower()
    name = lower.rsplit("/", 1)[-1]
    if lower.endswith((".html", ".htm")):
        return FileCategory.HTML
    if lower.endswith(_MEDIA_EXT):
        return FileCategory.MEDIA
    if not lower.endswith(".json"):
        return FileCategory.OTHER
    if name == "saved_posts.json":
        return FileCategory.SAVED_POSTS
    if name == "saved_collections.json":
        return FileCategory.SAVED_COLLECTIONS
    if _MESSAGE_FILE.search(lower):
        return FileCategory.MESSAGES
    return None


def classify_json_shape(data: Any) -> FileCategory:
    if isinstance(data, dict):
        if isinstance(data.get("messages"), list) and isinstance(data.get("participants"), list):
            return FileCategory.MESSAGES
        keys = {k.lower() for k in data if isinstance(k, str)}
        if any("saved_collection" in k for k in keys):
            return FileCategory.SAVED_COLLECTIONS
        if any(k.startswith("saved_saved_media") or k == "saved_media" for k in keys):
            return FileCategory.SAVED_POSTS
    return FileCategory.OTHER_JSON


def is_relevant_json_candidate(path: str) -> bool:
    """Unknown JSON files worth shape-sniffing (keeps us from loading every export file)."""
    lower = path.lower()
    return "saved" in lower or "message" in lower or "inbox" in lower
