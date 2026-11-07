from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, Timestamps, UUIDPk, utcnow
from app.db.enums import ImportFileStatus, ImportStatus, NotionSyncStatus, ProcessingStatus


class User(UUIDPk, Timestamps, Base):
    __tablename__ = "users"
    email: Mapped[str] = mapped_column(String(320), unique=True)


class ContentItem(UUIDPk, Timestamps, Base):
    __tablename__ = "content_items"
    __table_args__ = (
        UniqueConstraint("user_id", "identity_key"),
        Index("ix_content_items_user_platform_id", "user_id", "platform", "platform_content_id"),
    )

    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    identity_key: Mapped[str] = mapped_column(String(512))
    canonical_url: Mapped[str] = mapped_column(Text, index=True)
    canonical_url_hash: Mapped[str] = mapped_column(String(64), index=True)
    platform: Mapped[str] = mapped_column(String(32), default="instagram")
    platform_content_id: Mapped[str | None] = mapped_column(String(128), index=True)
    content_type: Mapped[str] = mapped_column(String(32))
    title: Mapped[str | None] = mapped_column(Text)
    creator_username: Mapped[str | None] = mapped_column(String(255))
    caption: Mapped[str | None] = mapped_column(Text)
    summary: Mapped[str | None] = mapped_column(Text)
    primary_category: Mapped[str | None] = mapped_column(String(64), index=True)
    secondary_category: Mapped[str | None] = mapped_column(String(64))
    extracted_data: Mapped[dict[str, Any]] = mapped_column(default=dict)
    tags: Mapped[list[Any]] = mapped_column(default=list)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    first_captured_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    processing_status: Mapped[str] = mapped_column(
        String(32), default=ProcessingStatus.RECEIVED, index=True
    )
    extraction_confidence: Mapped[float | None] = mapped_column(Float)
    notion_page_id: Mapped[str | None] = mapped_column(String(64), index=True)
    notion_sync_status: Mapped[str] = mapped_column(String(32), default=NotionSyncStatus.NOT_SYNCED)

    sources: Mapped[list[ContentSource]] = relationship(
        back_populates="content", cascade="all, delete-orphan"
    )


class ContentSource(UUIDPk, Base):
    __tablename__ = "content_sources"

    content_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("content_items.id", ondelete="CASCADE"), index=True
    )
    import_batch_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("import_batches.id", ondelete="SET NULL"), index=True
    )
    source_key: Mapped[str] = mapped_column(String(64), unique=True)
    source_type: Mapped[str] = mapped_column(String(32), index=True)
    source_name: Mapped[str | None] = mapped_column(Text)
    source_reference: Mapped[str | None] = mapped_column(Text)
    sender: Mapped[str | None] = mapped_column(String(255))
    original_url: Mapped[str | None] = mapped_column(Text)
    captured_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    meta: Mapped[dict[str, Any]] = mapped_column("metadata", default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    content: Mapped[ContentItem] = relationship(back_populates="sources")


class ImportBatch(UUIDPk, Timestamps, Base):
    __tablename__ = "import_batches"

    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    filename: Mapped[str] = mapped_column(Text)
    archive_path: Mapped[str] = mapped_column(Text)
    archive_sha256: Mapped[str | None] = mapped_column(String(64), index=True)
    status: Mapped[str] = mapped_column(String(32), default=ImportStatus.PENDING, index=True)
    total_files: Mapped[int] = mapped_column(Integer, default=0)
    processed_files: Mapped[int] = mapped_column(Integer, default=0)
    records_found: Mapped[int] = mapped_column(Integer, default=0)
    records_imported: Mapped[int] = mapped_column(Integer, default=0)
    duplicates_found: Mapped[int] = mapped_column(Integer, default=0)
    failures: Mapped[int] = mapped_column(Integer, default=0)
    report: Mapped[dict[str, Any]] = mapped_column(default=dict)
    error_message: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    files: Mapped[list[ImportFile]] = relationship(
        back_populates="batch", cascade="all, delete-orphan"
    )


class ImportFile(UUIDPk, Base):
    """Per-file checkpoint: the unit of resumability."""

    __tablename__ = "import_files"
    __table_args__ = (UniqueConstraint("batch_id", "path"),)

    batch_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("import_batches.id", ondelete="CASCADE"), index=True
    )
    path: Mapped[str] = mapped_column(Text)
    size: Mapped[int] = mapped_column(BigInteger, default=0)
    category: Mapped[str | None] = mapped_column(String(32))
    parser: Mapped[str | None] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(16), default=ImportFileStatus.PENDING, index=True)
    records_seen: Mapped[int] = mapped_column(Integer, default=0)
    references_found: Mapped[int] = mapped_column(Integer, default=0)
    summary: Mapped[dict[str, Any]] = mapped_column(default=dict)
    issues: Mapped[list[Any]] = mapped_column(default=list)
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    batch: Mapped[ImportBatch] = relationship(back_populates="files")


class RawImportRecord(UUIDPk, Base):
    """Original export record (only link-bearing / relevant records) for debugging."""

    __tablename__ = "raw_import_records"
    __table_args__ = (UniqueConstraint("batch_id", "record_hash"),)

    batch_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("import_batches.id", ondelete="CASCADE"), index=True
    )
    file_path: Mapped[str] = mapped_column(Text)
    locator: Mapped[str] = mapped_column(Text)
    record_hash: Mapped[str] = mapped_column(String(64))
    resolved: Mapped[bool] = mapped_column(default=True)
    record: Mapped[dict[str, Any]] = mapped_column(default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class ProcessingJob(UUIDPk, Timestamps, Base):
    __tablename__ = "processing_jobs"

    content_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("content_items.id", ondelete="CASCADE"), index=True
    )
    job_type: Mapped[str] = mapped_column(String(64))
    stage: Mapped[str | None] = mapped_column(String(32))
    status: Mapped[str] = mapped_column(String(32), index=True)
    retry_count: Mapped[int] = mapped_column(Integer, default=0)
    error_message: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class AuditLog(UUIDPk, Base):
    __tablename__ = "audit_logs"

    entity_type: Mapped[str] = mapped_column(String(64), index=True)
    entity_id: Mapped[uuid.UUID | None] = mapped_column(index=True)
    action: Mapped[str] = mapped_column(String(64))
    details: Mapped[dict[str, Any]] = mapped_column(default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
