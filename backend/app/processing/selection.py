"""Choose which content items to queue (cost control: you decide what gets processed)."""

from __future__ import annotations

import uuid

from sqlalchemy import exists, select
from sqlalchemy.orm import Session

from app.db.models import ContentItem, ContentSource, ProcessingJob
from app.processing.queue import PIPELINE_JOB


def select_for_processing(session: Session, *, limit: int, collection: str | None = None,
                          only_new: bool = True, import_batch_id: uuid.UUID | None = None,
                          require_caption: bool = False) -> list[uuid.UUID]:
    q = select(ContentItem.id).where(ContentItem.platform_content_id.is_not(None))
    if only_new:
        q = q.where(~exists().where(ProcessingJob.content_id == ContentItem.id,
                                    ProcessingJob.job_type == PIPELINE_JOB))
    if require_caption:
        q = q.where(ContentItem.caption.is_not(None), ContentItem.caption != "")
    if collection or import_batch_id:
        src = select(ContentSource.content_id)
        if collection:
            src = src.where(ContentSource.source_type == "saved_collection",
                            ContentSource.source_name == collection)
        if import_batch_id:
            src = src.where(ContentSource.import_batch_id == import_batch_id)
        q = q.where(ContentItem.id.in_(src))
    q = q.order_by(ContentItem.first_captured_at.desc().nulls_last(), ContentItem.id).limit(limit)
    return list(session.scalars(q))
