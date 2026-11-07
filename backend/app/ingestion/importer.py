"""Resumable import of an Instagram export into the database.

Resumability: every archive member gets an ``import_files`` checkpoint row. Each file is
parsed and persisted in its own transaction together with its checkpoint update and the
batch counters, so a crash leaves the database consistent and ``run_import`` simply
continues with the files still marked ``pending``. Source associations are keyed by a
deterministic ``source_key`` so re-processing a file never creates duplicates.
"""

from __future__ import annotations

import hashlib
import json
import logging
import uuid
from collections import Counter
from collections.abc import Callable
from dataclasses import asdict
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from app.db.enums import ImportFileStatus, ImportStatus
from app.db.models import ContentSource, ImportBatch, ImportFile, RawImportRecord
from app.db.repositories.content import ContentRepository, get_or_create_user
from app.ingestion.instagram.archive import (
    ArchiveError,
    ArchiveLimits,
    ArchiveMember,
    ExportArchive,
    file_sha256,
)
from app.ingestion.instagram.detect import classify_path
from app.ingestion.instagram.models import FileParseResult
from app.ingestion.instagram.reader import PARSEABLE, parse_member
from app.ingestion.url_parser import MEDIA_KINDS, UrlKind

log = logging.getLogger(__name__)

HEARTBEAT_STALE_AFTER = timedelta(minutes=5)
MAX_ISSUES_IN_REPORT = 200


class ImportInProgressError(Exception):
    pass


def _now() -> datetime:
    return datetime.now(UTC)


def _aware(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)


def create_batch(
    session: Session, archive_path: Path, filename: str | None = None
) -> ImportBatch:
    user = get_or_create_user(session)
    batch = ImportBatch(
        user_id=user.id,
        filename=filename or archive_path.name,
        archive_path=str(archive_path),
        archive_sha256=file_sha256(archive_path) if archive_path.is_file() else None,
        status=ImportStatus.PENDING,
        report={},
    )
    session.add(batch)
    session.commit()
    log.info("import_batch_created", extra={"batch_id": str(batch.id), "file": batch.filename})
    return batch


def _claim(session: Session, batch_id: uuid.UUID, allow_takeover: bool) -> ImportBatch:
    batch = session.get(ImportBatch, batch_id, with_for_update=True)
    if batch is None:
        raise LookupError(f"Import batch {batch_id} not found")
    if batch.status == ImportStatus.RUNNING and not allow_takeover:
        hb = _aware(batch.heartbeat_at)
        if hb and _now() - hb < HEARTBEAT_STALE_AFTER:
            raise ImportInProgressError(f"Import {batch_id} is already running")
    batch.status = ImportStatus.RUNNING
    batch.started_at = batch.started_at or _now()
    batch.heartbeat_at = _now()
    batch.error_message = None
    session.commit()
    return batch


def _register_files(session: Session, batch: ImportBatch, archive: ExportArchive) -> None:
    if session.scalar(select(func.count()).where(ImportFile.batch_id == batch.id)):
        return
    listing = archive.listing()
    for m in listing.members:
        category = classify_path(m.path)
        skip = category is not None and category not in PARSEABLE
        session.add(
            ImportFile(
                batch_id=batch.id,
                path=m.path,
                size=m.size,
                category=category.value if category else None,
                status=ImportFileStatus.SKIPPED if skip else ImportFileStatus.PENDING,
                summary={},
                issues=[],
            )
        )
    for r in listing.rejected:
        session.add(
            ImportFile(
                batch_id=batch.id,
                path=r.path,
                status=ImportFileStatus.FAILED,
                summary={},
                issues=[{"severity": "error", "message": f"Rejected: {r.reason}"}],
                processed_at=_now(),
            )
        )
    batch.total_files = len(listing.members) + len(listing.rejected)
    batch.failures += len(listing.rejected)
    session.commit()


def _record_hash(path: str, locator: str, record: Any) -> str:
    payload = json.dumps([path, locator, record], sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(payload.encode()).hexdigest()


def _store_raw(
    session: Session, batch_id: uuid.UUID, path: str, locator: str, record: Any, resolved: bool
) -> None:
    if record is None:
        return
    h = _record_hash(path, locator, record)
    exists = session.scalar(
        select(RawImportRecord.id).where(
            RawImportRecord.batch_id == batch_id, RawImportRecord.record_hash == h
        )
    )
    if exists is None:
        session.add(
            RawImportRecord(
                batch_id=batch_id,
                file_path=path,
                locator=locator,
                record_hash=h,
                resolved=resolved,
                record=record if isinstance(record, dict) else {"value": record},
            )
        )


def _persist_file(
    session: Session, batch_id: uuid.UUID, file_row_id: uuid.UUID, result: FileParseResult
) -> None:
    batch = session.get(ImportBatch, batch_id)
    file_row = session.get(ImportFile, file_row_id)
    assert batch is not None and file_row is not None
    repo = ContentRepository(session, batch.user_id)

    found = imported = duplicates = created = already = 0
    non_content: Counter[str] = Counter()
    raw_seen: set[str] = set()
    for ref in result.references:
        if ref.source_reference not in raw_seen:
            raw_seen.add(ref.source_reference)
            _store_raw(session, batch.id, result.path, ref.source_reference, ref.raw, True)
        if ref.url.kind not in MEDIA_KINDS and ref.url.kind is not UrlKind.SHARE_LINK:
            non_content[ref.url.kind.value] += 1
            continue
        found += 1
        item, was_created = repo.upsert_from_reference(ref)
        created += was_created
        if repo.add_source(item, ref, batch.id):
            imported += 1
            duplicates += not was_created
        else:
            already += 1
    for i, record in enumerate(result.unresolved_records):
        _store_raw(session, batch.id, result.path, f"{result.path}#unresolved[{i}]", record, False)

    has_error = not result.ok
    file_row.category = result.category.value
    file_row.parser = result.parser
    file_row.status = ImportFileStatus.FAILED if has_error else ImportFileStatus.COMPLETED
    file_row.records_seen = result.records_seen
    file_row.references_found = found
    file_row.issues = [asdict(i) for i in result.issues]
    file_row.summary = {
        "collections": result.collections,
        "conversation": asdict(result.conversation) if result.conversation else None,
        "content_items_created": created,
        "already_imported": already,
        "non_content_urls": dict(non_content),
        "unresolved_records": len(result.unresolved_records),
    }
    file_row.processed_at = _now()

    batch.processed_files += 1
    batch.records_found += found
    batch.records_imported += imported
    batch.duplicates_found += duplicates
    batch.failures += has_error
    batch.heartbeat_at = _now()


def run_import(
    session_factory: sessionmaker[Session],
    batch_id: uuid.UUID,
    limits: ArchiveLimits | None = None,
    *,
    allow_takeover: bool = False,
    on_file_committed: Callable[[str], None] | None = None,
) -> dict[str, Any]:
    """Run (or resume) an import. Returns the final report."""
    with session_factory() as session:
        batch = _claim(session, batch_id, allow_takeover)
        archive_path = Path(batch.archive_path)

    try:
        archive = ExportArchive(archive_path, limits)
    except ArchiveError as exc:
        with session_factory() as session:
            batch = session.get(ImportBatch, batch_id)
            batch.status = ImportStatus.FAILED
            batch.error_message = str(exc)
            batch.completed_at = _now()
            session.commit()
        log.error("import_archive_invalid", extra={"batch_id": str(batch_id), "error": str(exc)})
        return build_report_for(session_factory, batch_id)

    with archive:
        with session_factory() as session:
            batch = session.get(ImportBatch, batch_id)
            _register_files(session, batch, archive)
            pending = session.execute(
                select(ImportFile.id, ImportFile.path, ImportFile.size)
                .where(ImportFile.batch_id == batch_id,
                       ImportFile.status == ImportFileStatus.PENDING)
                .order_by(ImportFile.path)
            ).all()

        log.info("import_started", extra={"batch_id": str(batch_id), "pending": len(pending)})
        for file_id, path, size in pending:
            result = parse_member(archive, ArchiveMember(path, size))
            with session_factory() as session:
                try:
                    _persist_file(session, batch_id, file_id, result)
                    session.commit()
                except Exception as exc:
                    session.rollback()
                    log.exception("import_file_failed", extra={"path": path})
                    _mark_file_failed(session, batch_id, file_id, f"Persistence failure: {exc!r}")
            if on_file_committed:
                on_file_committed(path)

    with session_factory() as session:
        batch = session.get(ImportBatch, batch_id)
        batch.status = (
            ImportStatus.COMPLETED_WITH_ERRORS if batch.failures else ImportStatus.COMPLETED
        )
        batch.completed_at = _now()
        batch.report = build_report(session, batch)
        session.commit()
        log.info("import_finished", extra={"batch_id": str(batch_id), "status": batch.status})
        return batch.report


def _mark_file_failed(session: Session, batch_id: uuid.UUID, file_id: uuid.UUID, msg: str) -> None:
    file_row = session.get(ImportFile, file_id)
    batch = session.get(ImportBatch, batch_id)
    file_row.status = ImportFileStatus.FAILED
    file_row.issues = [{"severity": "error", "message": msg, "path": file_row.path}]
    file_row.processed_at = _now()
    batch.processed_files += 1
    batch.failures += 1
    session.commit()


def build_report_for(session_factory: sessionmaker[Session], batch_id: uuid.UUID) -> dict:
    with session_factory() as session:
        batch = session.get(ImportBatch, batch_id)
        report = build_report(session, batch)
        batch.report = report
        session.commit()
        return report


def build_report(session: Session, batch: ImportBatch) -> dict[str, Any]:
    files = session.scalars(select(ImportFile).where(ImportFile.batch_id == batch.id)).all()
    by_category: Counter[str] = Counter()
    by_status: Counter[str] = Counter()
    collections: Counter[str] = Counter()
    non_content: Counter[str] = Counter()
    conversations: dict[str, dict] = {}
    errors: list[dict] = []
    warnings: list[dict] = []
    created = already = unresolved = 0
    for f in files:
        by_category[f.category or "unclassified"] += 1
        by_status[f.status] += 1
        s = f.summary or {}
        collections.update(s.get("collections") or {})
        non_content.update(s.get("non_content_urls") or {})
        created += s.get("content_items_created", 0)
        already += s.get("already_imported", 0)
        unresolved += s.get("unresolved_records", 0)
        conv = s.get("conversation")
        if conv:
            c = conversations.setdefault(conv["conversation_id"], {**conv, "message_count": 0,
                                                                   "messages_with_links": 0})
            c["message_count"] += conv["message_count"]
            c["messages_with_links"] += conv["messages_with_links"]
        for issue in f.issues or []:
            target = errors if issue.get("severity") == "error" else warnings
            if len(target) < MAX_ISSUES_IN_REPORT:
                target.append({"path": f.path, **issue})

    distinct_items = session.scalar(
        select(func.count(func.distinct(ContentSource.content_id))).where(
            ContentSource.import_batch_id == batch.id
        )
    )
    convs = list(conversations.values())
    return {
        "batch_id": str(batch.id),
        "filename": batch.filename,
        "status": batch.status,
        "error_message": batch.error_message,
        "files": {
            "total": batch.total_files,
            "processed": batch.processed_files,
            "by_category": dict(by_category),
            "by_status": dict(by_status),
        },
        "saved_collections": dict(collections),
        "conversations": {
            "total": len(convs),
            "group": sum(1 for c in convs if c["is_group"]),
            "direct": sum(1 for c in convs if not c["is_group"]),
            "with_instagram_links": sum(1 for c in convs if c["messages_with_links"]),
        },
        "references": {
            "found": batch.records_found,
            "imported": batch.records_imported,
            "already_imported": already,
            "duplicates_consolidated": batch.duplicates_found,
            "content_items_created": created,
            "content_items_linked": distinct_items or 0,
            "non_content_urls": dict(non_content),
            "records_without_url": unresolved,
        },
        "failures": batch.failures,
        "errors": errors,
        "warnings": warnings,
    }
