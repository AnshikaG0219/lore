"""Processing queue management and stats."""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, Depends, Request, status
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.deps import get_session
from app.api.v1.content import _dispatch, _job_out
from app.core.config import Settings, get_settings
from app.core.security import require_api_key
from app.db.models import ContentItem, ProcessingJob
from app.processing import queue
from app.processing.selection import select_for_processing
from app.workers.dispatch import ImportDispatcher

router = APIRouter(tags=["processing"], dependencies=[Depends(require_api_key)])


def get_dispatcher(request: Request) -> ImportDispatcher:
    return request.app.state.dispatcher


class EnqueueRequest(BaseModel):
    limit: int = Field(default=10, ge=1, le=5000)
    collection: str | None = None
    import_batch_id: uuid.UUID | None = None
    only_new: bool = True
    require_caption: bool = False
    status: str | None = None  # e.g. NEEDS_MANUAL_INPUT (use with only_new=false)
    saved_only: bool = False   # only posts you saved yourself (not chat shares)
    priority: int = Field(default=100, ge=0, le=1000)


@router.post("/processing/enqueue", status_code=status.HTTP_202_ACCEPTED)
def enqueue_many(req: EnqueueRequest, session: Session = Depends(get_session),
                 dispatcher: ImportDispatcher = Depends(get_dispatcher),
                 settings: Settings = Depends(get_settings)) -> dict[str, Any]:
    ids = select_for_processing(session, limit=req.limit, collection=req.collection,
                                only_new=req.only_new, import_batch_id=req.import_batch_id,
                                require_caption=req.require_caption, status=req.status,
                                saved_only=req.saved_only)
    jobs = [queue.enqueue(session, cid, reason="bulk", force=not req.only_new,
                          priority=req.priority, max_attempts=settings.pipeline_max_attempts)
            for cid in ids]
    session.commit()
    to_run = [j.job_id for j in jobs if j.should_dispatch]
    _dispatch(dispatcher, to_run)
    return {"selected": len(ids), "queued": len(to_run)}


@router.get("/processing/jobs")
def list_jobs(status_filter: str | None = None, limit: int = 50, offset: int = 0,
              session: Session = Depends(get_session)) -> list[dict[str, Any]]:
    q = select(ProcessingJob).order_by(ProcessingJob.updated_at.desc())
    if status_filter:
        q = q.where(ProcessingJob.status == status_filter)
    return [_job_out(j) for j in session.scalars(q.limit(max(1, min(limit, 200))).offset(offset))]


@router.get("/stats")
def stats(session: Session = Depends(get_session)) -> dict[str, Any]:
    jobs = dict(session.execute(select(ProcessingJob.status, func.count())
                                .group_by(ProcessingJob.status)).all())
    content = dict(session.execute(select(ContentItem.processing_status, func.count())
                                   .group_by(ContentItem.processing_status)).all())
    categories = dict(session.execute(
        select(ContentItem.primary_category, func.count())
        .where(ContentItem.primary_category.is_not(None))
        .group_by(ContentItem.primary_category)).all())
    return {"content_total": sum(content.values()), "content_by_status": content,
            "jobs_by_status": jobs, "categories": categories}
