"""Common internal representation produced by every ingestion adapter."""

from __future__ import annotations

import enum
import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from app.ingestion.url_parser import ParsedInstagramUrl


class SourceType(enum.StrEnum):
    SAVED_POST = "saved_post"
    SAVED_COLLECTION = "saved_collection"
    DIRECT_MESSAGE = "direct_message"
    GROUP_MESSAGE = "group_message"
    MANUAL_CAPTURE = "manual_capture"


class FileCategory(enum.StrEnum):
    SAVED_POSTS = "saved_posts"
    SAVED_COLLECTIONS = "saved_collections"
    MESSAGES = "messages"
    HTML = "html"
    MEDIA = "media"
    OTHER_JSON = "other_json"
    OTHER = "other"


@dataclass(slots=True)
class ContentReference:
    """One occurrence of an Instagram URL in one source (a save, a collection, a message)."""

    url: ParsedInstagramUrl
    source_type: SourceType
    source_name: str | None
    source_reference: str  # stable locator inside the export, e.g. "path#index"
    captured_at: datetime | None = None
    sender: str | None = None
    creator_username: str | None = None
    caption: str | None = None
    conversation_id: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    raw: Any = None

    @property
    def source_key(self) -> str:
        """Idempotency key for one source association (same input → same key)."""
        payload = json.dumps(
            [self.url.identity_key, self.source_type.value, self.source_name,
             self.source_reference],
            ensure_ascii=False,
        )
        return hashlib.sha256(payload.encode()).hexdigest()


@dataclass(slots=True)
class ParseIssue:
    path: str
    message: str
    severity: str = "error"  # "error" | "warning"
    locator: str | None = None


@dataclass(slots=True)
class ConversationInfo:
    conversation_id: str
    title: str | None
    participant_count: int
    is_group: bool
    message_count: int = 0
    messages_with_links: int = 0


@dataclass(slots=True)
class FileParseResult:
    path: str
    category: FileCategory
    parser: str | None = None
    records_seen: int = 0
    references: list[ContentReference] = field(default_factory=list)
    issues: list[ParseIssue] = field(default_factory=list)
    collections: dict[str, int] = field(default_factory=dict)
    conversation: ConversationInfo | None = None
    # Records with no usable URL (e.g. deleted content); kept for the report, never dropped.
    unresolved_records: list[Any] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not any(i.severity == "error" for i in self.issues)
