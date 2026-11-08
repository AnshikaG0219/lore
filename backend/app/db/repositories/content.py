"""Persistence for canonical content items and their source associations."""

from __future__ import annotations

import uuid
from datetime import UTC

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import ContentItem, ContentSource, User
from app.ingestion.deduplication import merge_content_type
from app.ingestion.instagram.models import ContentReference, SourceType
from app.ingestion.url_parser import UrlKind

DEFAULT_OWNER_EMAIL = "owner@lore.local"

# Saved records carry the post's full caption; message shares only carry a preview
# (share_text). Saved captions therefore replace message captions, never the reverse.
_AUTHORITATIVE_CAPTION = frozenset({SourceType.SAVED_POST, SourceType.SAVED_COLLECTION})


def get_or_create_user(session: Session, email: str = DEFAULT_OWNER_EMAIL) -> User:
    user = session.scalar(select(User).where(User.email == email))
    if user is None:
        user = User(email=email)
        session.add(user)
        session.flush()
    return user


def _content_type(kind: UrlKind) -> str:
    return kind.value if kind in (UrlKind.POST, UrlKind.REEL, UrlKind.TV) else "unknown"


class ContentRepository:
    def __init__(self, session: Session, user_id: uuid.UUID) -> None:
        self.session = session
        self.user_id = user_id

    def get_by_identity(self, identity_key: str) -> ContentItem | None:
        return self.session.scalar(
            select(ContentItem).where(
                ContentItem.user_id == self.user_id, ContentItem.identity_key == identity_key
            )
        )

    def upsert_from_reference(self, ref: ContentReference) -> tuple[ContentItem, bool]:
        """Return (item, created). Never overwrites user/AI-populated fields."""
        url = ref.url
        item = self.get_by_identity(url.identity_key)
        if item is None:
            item = ContentItem(
                user_id=self.user_id,
                identity_key=url.identity_key,
                canonical_url=url.canonical_url,
                canonical_url_hash=url.url_hash,
                platform="instagram",
                platform_content_id=url.shortcode,
                content_type=_content_type(url.kind),
                creator_username=ref.creator_username,
                caption=ref.caption,
                first_captured_at=ref.captured_at,
                extracted_data={"needs_url_resolution": True}
                if url.kind is UrlKind.SHARE_LINK else {},
                tags=[],
            )
            self.session.add(item)
            self.session.flush()
            return item, True

        new_type = merge_content_type(item.content_type, _content_type(url.kind))
        if new_type != item.content_type:
            item.content_type = new_type
            item.canonical_url = url.canonical_url
            item.canonical_url_hash = url.url_hash
        if not item.creator_username and ref.creator_username:
            item.creator_username = ref.creator_username
        if ref.caption and (not item.caption or ref.source_type in _AUTHORITATIVE_CAPTION):
            item.caption = ref.caption
        if ref.captured_at and (
            item.first_captured_at is None
            or _naive(ref.captured_at) < _naive(item.first_captured_at)
        ):
            item.first_captured_at = ref.captured_at
        return item, False

    def source_exists(self, source_key: str) -> bool:
        return self.session.scalar(
            select(ContentSource.id).where(ContentSource.source_key == source_key)
        ) is not None

    def add_source(
        self, item: ContentItem, ref: ContentReference, batch_id: uuid.UUID | None
    ) -> bool:
        """Attach a source association. Idempotent: returns False if it already exists."""
        if self.source_exists(ref.source_key):
            return False
        self.session.add(
            ContentSource(
                content_id=item.id,
                import_batch_id=batch_id,
                source_key=ref.source_key,
                source_type=ref.source_type.value,
                source_name=ref.source_name,
                source_reference=ref.source_reference,
                sender=ref.sender,
                original_url=ref.url.raw,
                captured_at=ref.captured_at,
                meta={**ref.metadata, "url_kind": ref.url.kind.value},
            )
        )
        self.session.flush()
        return True


def _naive(dt):
    # SQLite returns naive datetimes (stored as UTC); compare consistently in naive UTC.
    return dt.astimezone(UTC).replace(tzinfo=None) if dt.tzinfo else dt
