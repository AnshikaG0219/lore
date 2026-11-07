"""Read-only browsing endpoints used by the dashboard (PRD §14: GET /content, /content/{id})."""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.api.deps import get_session
from app.api.v1.content import _asset_out, _job_out
from app.core.security import require_api_key
from app.db.models import ContentItem, ContentSource, ImportBatch, MediaAsset, ProcessingJob
from app.processing.extraction.caption import JOB_TYPE as EXTRACTION_KEY
from app.processing.media.pipeline import media_text_for
from app.processing.queue import PIPELINE_JOB

router = APIRouter(tags=["browse"], dependencies=[Depends(require_api_key)])

SORTS = {
    "recent": (ContentItem.first_captured_at.desc().nulls_last(), ContentItem.id),
    "processed": (ContentItem.last_processed_at.desc().nulls_last(), ContentItem.id),
    "confidence": (ContentItem.extraction_confidence.desc().nulls_last(), ContentItem.id),
    "title": (ContentItem.title.asc().nulls_last(), ContentItem.id),
}


def _summary(item: ContentItem, collections: list[str], source_count: int) -> dict[str, Any]:
    ex = (item.extracted_data or {}).get(EXTRACTION_KEY) or {}
    return {
        "id": str(item.id), "url": item.canonical_url, "shortcode": item.platform_content_id,
        "content_type": item.content_type, "creator": item.creator_username,
        "title": item.title, "summary": item.summary,
        "caption_preview": (item.caption or "")[:200] or None,
        "primary_category": item.primary_category,
        "secondary_category": item.secondary_category,
        "tags": item.tags or [], "confidence": item.extraction_confidence,
        "information_sufficient": ex.get("information_sufficient"),
        "status": item.processing_status,
        "first_captured_at": item.first_captured_at.isoformat() if item.first_captured_at else None,
        "last_processed_at": item.last_processed_at.isoformat() if item.last_processed_at else None,
        "collections": collections, "source_count": source_count,
    }


@router.get("/content")
def list_content(
    session: Session = Depends(get_session),
    q: str | None = Query(None, max_length=200),
    category: str | None = None,
    status_filter: str | None = Query(None, alias="status"),
    collection: str | None = None,
    content_type: str | None = None,
    processed: bool | None = None,
    sort: str = "recent",
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
) -> dict[str, Any]:
    stmt = select(ContentItem)
    if q:
        like = f"%{q.strip()}%"
        stmt = stmt.where(or_(ContentItem.title.ilike(like), ContentItem.summary.ilike(like),
                              ContentItem.caption.ilike(like),
                              ContentItem.creator_username.ilike(like)))
    if category:
        stmt = stmt.where(or_(ContentItem.primary_category == category,
                              ContentItem.secondary_category == category))
    if status_filter:
        stmt = stmt.where(ContentItem.processing_status == status_filter)
    if content_type:
        stmt = stmt.where(ContentItem.content_type == content_type)
    if processed is not None:
        stmt = stmt.where(ContentItem.last_processed_at.is_not(None) if processed
                          else ContentItem.last_processed_at.is_(None))
    if collection:
        stmt = stmt.where(ContentItem.id.in_(select(ContentSource.content_id).where(
            ContentSource.source_type == "saved_collection",
            ContentSource.source_name == collection)))
    total = session.scalar(select(func.count()).select_from(stmt.subquery()))
    items = session.scalars(stmt.order_by(*SORTS.get(sort, SORTS["recent"]))
                            .limit(limit).offset(offset)).all()

    ids = [i.id for i in items]
    colls: dict[uuid.UUID, list[str]] = {i: [] for i in ids}
    counts: dict[uuid.UUID, int] = dict.fromkeys(ids, 0)
    if ids:
        for cid, stype, name in session.execute(
                select(ContentSource.content_id, ContentSource.source_type,
                       ContentSource.source_name).where(ContentSource.content_id.in_(ids))):
            counts[cid] += 1
            if stype == "saved_collection" and name and name not in colls[cid]:
                colls[cid].append(name)
    return {"total": total, "limit": limit, "offset": offset,
            "items": [_summary(i, colls[i.id], counts[i.id]) for i in items]}


@router.get("/content/{content_id}")
def get_content(content_id: uuid.UUID,
                session: Session = Depends(get_session)) -> dict[str, Any]:
    item = session.get(ContentItem, content_id)
    if item is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Content not found")
    sources = session.scalars(select(ContentSource).where(ContentSource.content_id == item.id)
                              .order_by(ContentSource.captured_at.asc().nulls_last())).all()
    job = session.scalar(select(ProcessingJob).where(
        ProcessingJob.content_id == item.id, ProcessingJob.job_type == PIPELINE_JOB))
    assets = session.scalars(select(MediaAsset).where(MediaAsset.content_id == item.id)
                             .order_by(MediaAsset.position)).all()
    media = media_text_for(session, item.id)
    ex = dict((item.extracted_data or {}).get(EXTRACTION_KEY) or {})
    meta = ex.pop("_meta", None)
    collections = sorted({s.source_name for s in sources
                          if s.source_type == "saved_collection" and s.source_name})
    return {
        **_summary(item, collections, len(sources)),
        "caption": item.caption,
        "extraction": ex or None,
        "extraction_meta": meta,
        "sources": [{"type": s.source_type, "name": s.source_name, "sender": s.sender,
                     "captured_at": s.captured_at.isoformat() if s.captured_at else None}
                    for s in sources],
        "job": _job_out(job),
        "media": {"assets": [_asset_out(a) for a in assets],
                  "on_screen_text": media.on_screen_text, "transcript": media.transcript},
    }


@router.get("/facets")
def facets(session: Session = Depends(get_session)) -> dict[str, Any]:
    def grouped(col, *where):
        stmt = select(col, func.count()).group_by(col).order_by(func.count().desc())
        for w in where:
            stmt = stmt.where(w)
        return [{"value": v, "count": c} for v, c in session.execute(stmt).all()
                if v is not None]

    collections = session.execute(
        select(ContentSource.source_name, func.count(func.distinct(ContentSource.content_id)))
        .where(ContentSource.source_type == "saved_collection")
        .group_by(ContentSource.source_name).order_by(ContentSource.source_name)).all()
    last_import = session.scalar(select(ImportBatch).order_by(ImportBatch.created_at.desc()))
    return {
        "categories": grouped(ContentItem.primary_category),
        "statuses": grouped(ContentItem.processing_status),
        "content_types": grouped(ContentItem.content_type),
        "source_types": grouped(ContentSource.source_type),
        "collections": [{"value": n, "count": c} for n, c in collections if n],
        "last_import": None if last_import is None else {
            "filename": last_import.filename, "status": last_import.status,
            "completed_at": last_import.completed_at.isoformat()
            if last_import.completed_at else None,
            "records_found": last_import.records_found,
            "duplicates_found": last_import.duplicates_found},
    }


@router.get("/places")
def places(session: Session = Depends(get_session), q: str | None = Query(None, max_length=200),
           limit: int = Query(500, ge=1, le=2000)) -> list[dict[str, Any]]:
    """Every extracted place across processed items (e.g. "cafés in Ladakh")."""
    rows = session.execute(select(ContentItem.id, ContentItem.title, ContentItem.canonical_url,
                                  ContentItem.extracted_data)
                           .where(ContentItem.last_processed_at.is_not(None))).all()
    needle = (q or "").strip().lower()
    out = []
    for cid, title, url, data in rows:
        info = ((data or {}).get(EXTRACTION_KEY) or {}).get("extracted_information") or {}
        for p in info.get("places") or []:
            text = " ".join(str(p.get(k) or "") for k in ("name", "location", "place_type",
                                                          "notes")).lower()
            if needle and needle not in text and needle not in (title or "").lower():
                continue
            out.append({"name": p.get("name"), "place_type": p.get("place_type"),
                        "location": p.get("location"), "notes": p.get("notes"),
                        "evidence": p.get("evidence"), "content_id": str(cid),
                        "content_title": title, "url": url})
            if len(out) >= limit:
                return out
    return out
