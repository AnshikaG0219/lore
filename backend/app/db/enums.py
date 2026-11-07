"""Domain enums persisted as plain strings (no DB enum types → painless migrations)."""

import enum


class ProcessingStatus(enum.StrEnum):
    RECEIVED = "RECEIVED"
    VALIDATING = "VALIDATING"
    DUPLICATE_DETECTED = "DUPLICATE_DETECTED"
    QUEUED = "QUEUED"
    FETCHING = "FETCHING"
    EXTRACTING_MEDIA = "EXTRACTING_MEDIA"
    TRANSCRIBING = "TRANSCRIBING"
    CLASSIFYING = "CLASSIFYING"
    STRUCTURING = "STRUCTURING"
    INDEXING = "INDEXING"
    NOTION_SYNC_PENDING = "NOTION_SYNC_PENDING"
    COMPLETED = "COMPLETED"
    NEEDS_MANUAL_INPUT = "NEEDS_MANUAL_INPUT"
    FAILED = "FAILED"


class ImportStatus(enum.StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    COMPLETED_WITH_ERRORS = "completed_with_errors"
    FAILED = "failed"


class ImportFileStatus(enum.StrEnum):
    PENDING = "pending"
    COMPLETED = "completed"
    FAILED = "failed"
    SKIPPED = "skipped"


class NotionSyncStatus(enum.StrEnum):
    NOT_SYNCED = "not_synced"
    PENDING = "pending"
    SYNCED = "synced"
    FAILED = "failed"
