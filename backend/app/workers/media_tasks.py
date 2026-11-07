"""Media processing and AI extraction tasks."""

from __future__ import annotations

import asyncio
import logging
import uuid
from typing import Any

from celery import Task
from sqlalchemy import select

from app.core.config import get_settings
from app.db.models import ContentItem, MediaAsset
from app.processing.media.engines import build_engines, build_store
from app.processing.media.pipeline import process_asset
from app.workers.celery_app import celery_app
from app.workers.tasks import _factory, backoff_seconds

log = logging.getLogger(__name__)


@celery_app.task(bind=True, name="instabrain.media.process", max_retries=3)
def process_media_task(self: Task, asset_id: str, extract_after: bool = True) -> dict[str, Any]:
    settings = get_settings()
    factory = _factory()
    with factory() as session:
        try:
            result = process_asset(session, uuid.UUID(asset_id), build_engines(settings),
                                   build_store(settings))
        except Exception as exc:
            from app.ingestion.importer import is_transient_db_error

            if is_transient_db_error(exc):
                raise self.retry(exc=exc, countdown=backoff_seconds(self.request.retries)) from exc
            raise
        if result["status"] == "completed" and extract_after:
            asset = session.get(MediaAsset, uuid.UUID(asset_id))
            pending = session.scalar(select(MediaAsset.id).where(
                MediaAsset.content_id == asset.content_id,
                MediaAsset.processing_status.in_(["pending", "processing"])))
            if pending is None and settings.copilot_github_token:
                extract_content_task.apply_async(args=[str(asset.content_id)])
    return result


@celery_app.task(name="instabrain.extraction.run")
def extract_content_task(content_id: str, force: bool = False) -> dict[str, Any]:
    from app.processing.ai.copilot_sdk import CopilotSdkProvider
    from app.processing.extraction.caption import process_item

    settings = get_settings()
    if not settings.copilot_github_token:
        return {"content_id": content_id, "status": "skipped", "reason": "no AI credentials"}

    async def run() -> dict[str, Any]:
        provider = CopilotSdkProvider(settings.copilot_github_token.get_secret_value(),
                                      settings.copilot_model)
        try:
            with _factory()() as session:
                item = session.get(ContentItem, uuid.UUID(content_id))
                if item is None:
                    return {"content_id": content_id, "status": "missing"}
                return await process_item(session, item, provider, force=force)
        finally:
            await provider.close()

    return asyncio.run(run())
