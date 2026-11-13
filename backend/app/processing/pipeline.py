"""Run one content item through the pipeline (PRD §4.4 / §12).

VALIDATING → EXTRACTING_MEDIA / TRANSCRIBING → STRUCTURING → COMPLETED | NEEDS_MANUAL_INPUT
(INDEXING and NOTION_SYNC_PENDING are added in Phases 3 and 6.)
Every stage is idempotent: re-running after a crash or retry only does the missing work.
"""

from __future__ import annotations

import asyncio
import logging
import tempfile
import uuid
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
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
from app.processing.fetch.instagram import (
    FetchError,
    FetchGovernor,
    FetchPausedError,
    MediaFetcher,
)
from app.processing.media.pipeline import MediaEngines, attach_media, process_asset
from app.storage.media import LocalMediaStore

log = logging.getLogger(__name__)


FETCH_KEY = "media_fetch"
FETCH_FINAL = frozenset({"fetched", "unavailable", "no_media"})


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
        fetcher: MediaFetcher | None = None,
        governor: FetchGovernor | None = None,
        max_fetch_errors: int = 3,
    ) -> None:
        self.factory = session_factory
        self.engines = engines
        self.store = store
        self.provider = provider
        self.stale_after = stale_after
        self.fetcher = fetcher
        self.governor = governor
        self.max_fetch_errors = max_fetch_errors
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

        fetch_summary = self._fetch_stage(job_id, token, content_id)
        media_summary = self._media_stage(job_id, token, content_id)
        if fetch_summary:
            media_summary["fetch"] = fetch_summary
        return self._structuring_stage(job_id, token, content_id, media_summary)

    def _fetch_stage(self, job_id, token, content_id) -> dict[str, Any] | None:
        """Download public media for posts that have none (ADR-023). Never raises."""
        if self.fetcher is None:
            return None
        with self.factory() as session:
            item = session.get(ContentItem, content_id)
            has_media = session.scalar(select(MediaAsset.id).where(
                MediaAsset.content_id == content_id).limit(1)) is not None
            state = dict((item.extracted_data or {}).get(FETCH_KEY) or {})
            previous = (item.extracted_data or {}).get(EXTRACTION_KEY) or {}
            # Only download when it can help: never for posts the caption already explains.
            already_understood = previous.get("information_sufficient") is True
            if has_media or already_understood or state.get("status") in FETCH_FINAL or \
                    (state.get("status") == "error"
                     and state.get("attempts", 0) >= self.max_fetch_errors):
                return None
            q.set_stage(session, job_id, token, PS.FETCHING)
            url = item.canonical_url

        attempt = {"at": datetime.now(UTC).isoformat(), "tool": self.fetcher.name,
                   "attempts": state.get("attempts", 0) + 1}
        with tempfile.TemporaryDirectory(prefix="lore-fetch-") as tmp:
            try:
                if self.governor:
                    self.governor.acquire()
                fetched = self.fetcher.fetch(url, Path(tmp))
            except FetchPausedError as exc:
                return self._save_fetch(content_id, {**attempt, "status": "deferred",
                                                     "error": str(exc),
                                                     "attempts": state.get("attempts", 0)})
            except FetchError as exc:
                if exc.reason == "blocked" and self.governor:
                    self.governor.report_block()
                return self._save_fetch(content_id, {**attempt, "status": exc.reason,
                                                     "error": str(exc)[:300]})
            except Exception as exc:  # never let fetching break the pipeline
                log.exception("fetch_unexpected_error", extra={"content_id": str(content_id)})
                return self._save_fetch(content_id, {**attempt, "status": "error",
                                                     "error": repr(exc)[:300]})

            with self.factory() as session:
                attached = 0
                for f in fetched.files:
                    try:
                        with f.open("rb") as fh:
                            _, created = attach_media(session, self.store, content_id, fh,
                                                      f.name, source=self.fetcher.name)
                        attached += created
                    except Exception as exc:  # unsupported/oversized file: skip, keep others
                        log.warning("fetch_attach_failed", extra={"file": f.name,
                                                                  "error": str(exc)})
                item = session.get(ContentItem, content_id)
                # The export only has a chat preview (share_text) for shared posts; the
                # post's own caption is authoritative when longer.
                if fetched.caption and len(fetched.caption) > len(item.caption or ""):
                    item.caption = fetched.caption
                if fetched.published_at and item.published_at is None:
                    item.published_at = fetched.published_at
                session.commit()
        return self._save_fetch(content_id, {**attempt, "status": "fetched", "files": attached,
                                             "tool_version": fetched.tool_version,
                                             "caption_chars": len(fetched.caption or "")})

    def _save_fetch(self, content_id, state: dict[str, Any]) -> dict[str, Any]:
        with self.factory() as session:
            item = session.get(ContentItem, content_id)
            item.extracted_data = {**(item.extracted_data or {}), FETCH_KEY: state}
            session.commit()
        return state

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

                def checkpoint() -> None:  # fencing + heartbeat between video frames
                    with self.factory() as s2:
                        job = q.owned(s2, job_id, token)
                        job.heartbeat_at = q.now()
                        s2.commit()

                r = process_asset(session, asset_id, engines, self.store, checkpoint=checkpoint)
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
                from app.processing.classification.archive import apply_visibility

                apply_visibility(item, {"information_sufficient": False})
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
