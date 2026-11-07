"""Run one content item through the pipeline (PRD §4.4 / §12).

VALIDATING → EXTRACTING_MEDIA / TRANSCRIBING → STRUCTURING → COMPLETED | NEEDS_MANUAL_INPUT
(INDEXING and NOTION_SYNC_PENDING are added in Phases 3 and 6.)
Every stage is idempotent: re-running after a crash or retry only does the missing work.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.db.enums import ProcessingStatus as PS
from app.db.models import ContentItem, MediaAsset
from app.ingestion.importer import is_transient_db_error
from app.processing import queue as q
from app.processing.ai.provider import LLMProvider, ProviderError
from app.processing.extraction.caption import (
    JOB_TYPE as EXTRACTION_KEY,
)
from app.processing.extraction.caption import (
    build_input,
    extract,
    is_up_to_date,
    persist_failure,
    persist_success,
)
from app.processing.media.pipeline import MediaEngines, process_asset
from app.storage.media import LocalMediaStore

log = logging.getLogger(__name__)


class ProviderNotConfiguredError(Exception):
    """No AI provider/credentials: a permanent condition until configuration changes."""


def provider_backoff(retry_count: int, base: int = 30, cap: int = 3600) -> timedelta:
    return timedelta(seconds=min(base * 2 ** retry_count, cap))


class PipelineRunner:
    def __init__(
        self,
        session_factory: sessionmaker[Session],
        engines: Callable[[], MediaEngines],
        store: LocalMediaStore,
        provider: Callable[[], LLMProvider],
        *,
        stale_after: timedelta = timedelta(minutes=10),
    ) -> None:
        self.factory = session_factory
        self.engines = engines
        self.store = store
        self.provider = provider
        self.stale_after = stale_after
        self._loop = asyncio.new_event_loop()

    def close(self) -> None:
        self._loop.close()

    def run(self, job_id: uuid.UUID) -> dict[str, Any]:
        with self.factory() as session:
            token = q.claim(session, job_id, self.stale_after)
            if token is None:
                return {"job_id": str(job_id), "status": "skipped"}
        try:
            return self._run(job_id, token)
        except q.JobClaimLostError:
            log.info("pipeline_superseded", extra={"job_id": str(job_id)})
            return {"job_id": str(job_id), "status": "superseded"}
        except Exception as exc:
            if is_transient_db_error(exc):
                raise  # job stays "running"; heartbeat goes stale; the sweep re-dispatches it
            log.exception("pipeline_unexpected_error", extra={"job_id": str(job_id)})
            with self.factory() as session:
                job = q.owned(session, job_id, token)
                retrying = q.retry_later(session, job_id, token, f"Unexpected: {exc!r}",
                                         provider_backoff(job.retry_count))
            return {"job_id": str(job_id), "status": "retry_wait" if retrying else "failed"}

    def _run(self, job_id: uuid.UUID, token: str) -> dict[str, Any]:
        with self.factory() as session:
            q.set_stage(session, job_id, token, PS.VALIDATING)
            job = q.owned(session, job_id, token)
            content_id = job.content_id
            if session.get(ContentItem, content_id) is None:
                q.finish(session, job_id, token, q.JobStatus.FAILED, content_status=PS.FAILED,
                         error="Content item no longer exists")
                return {"job_id": str(job_id), "status": "failed"}
            session.commit()  # release the row lock before long-running work

        media_summary = self._media_stage(job_id, token, content_id)
        return self._structuring_stage(job_id, token, content_id, media_summary)

    def _media_stage(self, job_id, token, content_id) -> dict[str, Any]:
        with self.factory() as session:
            assets = session.execute(
                select(MediaAsset.id, MediaAsset.asset_type, MediaAsset.processing_status)
                .where(MediaAsset.content_id == content_id).order_by(MediaAsset.position)
            ).all()
            summary = {"assets": len(assets), "processed": 0, "failed": 0, "errors": []}
            engines = None
            for asset_id, asset_type, status in assets:
                if status == "completed":
                    summary["processed"] += 1
                    continue
                q.set_stage(session, job_id, token, PS.EXTRACTING_MEDIA
                            if asset_type == "image" else PS.TRANSCRIBING)
                engines = engines or self.engines()
                r = process_asset(session, asset_id, engines, self.store)
                if r["status"] == "completed":
                    summary["processed"] += 1
                else:
                    summary["failed"] += 1
                    summary["errors"].append(r.get("error", "")[:200])
        return summary

    def _structuring_stage(self, job_id, token, content_id, media) -> dict[str, Any]:
        with self.factory() as session:
            q.set_stage(session, job_id, token, PS.STRUCTURING)
            item = session.get(ContentItem, content_id)
            inp = build_input(session, item)
            base = {"media": media}
            if inp is None:
                q.finish(session, job_id, token, q.JobStatus.NEEDS_MANUAL_INPUT,
                         content_status=PS.NEEDS_MANUAL_INPUT,
                         result={**base, "reason": "no caption or media text"})
                return {"job_id": str(job_id), "status": q.JobStatus.NEEDS_MANUAL_INPUT}

            if not is_up_to_date(item, inp):
                try:
                    provider = self.provider()
                except ProviderNotConfiguredError as exc:
                    q.finish(session, job_id, token, q.JobStatus.FAILED,
                             content_status=PS.FAILED, error=str(exc), result=base)
                    return {"job_id": str(job_id), "status": "failed", "error": str(exc)}
                started = datetime.now(UTC)
                try:
                    result = self._loop.run_until_complete(extract(provider, inp))
                except (ProviderError, ValueError) as exc:
                    persist_failure(session, content_id, str(exc), started)
                    session.commit()
                    job = q.owned(session, job_id, token)
                    retrying = q.retry_later(session, job_id, token, str(exc),
                                             provider_backoff(job.retry_count))
                    return {"job_id": str(job_id),
                            "status": "retry_wait" if retrying else "failed",
                            "error": str(exc)[:300]}
                q.owned(session, job_id, token)  # lock + verify before writing results
                persist_success(session, item, inp, result, started)
                session.commit()

            session.refresh(item)
            ex = (item.extracted_data or {}).get(EXTRACTION_KEY, {})
            outcome = {**base, "category": item.primary_category,
                       "confidence": item.extraction_confidence,
                       "information_sufficient": ex.get("information_sufficient"),
                       "sources": inp.sources}
            # Thin caption and no media to fill the gap → ask the user, never guess.
            needs_input = ex.get("information_sufficient") is False and \
                not (inp.on_screen_text or inp.spoken_transcript)
            status = q.JobStatus.NEEDS_MANUAL_INPUT if needs_input else q.JobStatus.COMPLETED
            q.finish(session, job_id, token, status,
                     content_status=PS.NEEDS_MANUAL_INPUT if needs_input else PS.COMPLETED,
                     result=outcome)
            return {"job_id": str(job_id), "status": status, **outcome}
