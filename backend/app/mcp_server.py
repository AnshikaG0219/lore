"""Lore MCP server: lets an AI assistant (e.g. Claude Desktop on another machine, which has a
Notion connection) read your useful posts and keep a Notion database in sync (ADR-026).

Access: bearer key (LORE_MCP_KEY), separate from the admin API key. Capabilities are limited
to reading posts and recording Notion sync status; nothing can be hidden, deleted or reprocessed.
Run: python -m app.mcp_server          (HTTP on LORE_MCP_HOST:LORE_MCP_PORT, default :8765)
     python -m app.mcp_server --stdio  (launched directly by a local assistant, no network)
"""

from __future__ import annotations

import secrets
import uuid
from datetime import UTC, datetime
from typing import Any

from mcp.server.mcpserver import MCPServer
from mcp.server.transport_security import TransportSecuritySettings
from mcp.types import ToolAnnotations
from sqlalchemy import func, or_, select
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse

from app.api.v1.browse import _found
from app.core.config import get_settings
from app.db.models import ContentFacet, ContentItem
from app.db.session import get_sessionmaker

EXTRACTION_KEY = "caption_extraction"
SECTIONS = ("eat_drink", "place", "trip", "property", "job", "recommendations", "how_to",
            "shopping", "event")
FILTERABLE = {  # tool argument → facet name
    "type": "type", "topic": "topic", "city": "city", "zone": "zone", "area": None,
    "state": "state", "country": "country", "cuisine": "cuisine", "venue_type": "venue_type",
    "trip_type": "trip_type", "origin": "origin",
}
MAX_LIMIT = 50

INSTRUCTIONS = """Lore holds the user's Instagram saves and posts friends shared, already
classified into facts. Hidden posts (memes, quotes, posts with no information) are excluded
unless asked for. Start with overview() to see every category; use list_category to fetch all
posts of a category (paged), search_posts for targeted questions and get_post for one post.
To sync Notion use the 'sync_to_notion' prompt, posts_to_sync and mark_synced. Never invent
facts: copy them from Lore's data."""

mcp = MCPServer(name="lore", title="Lore: your Instagram knowledge base",
                instructions=INSTRUCTIONS, version="1.0")
READ = ToolAnnotations(readOnlyHint=True, openWorldHint=False)


def _session():
    return get_sessionmaker()()


def _facets(session, ids: list[uuid.UUID]) -> dict[uuid.UUID, dict[str, list[str]]]:
    out: dict[uuid.UUID, dict[str, list[str]]] = {i: {} for i in ids}
    if ids:
        for cid, f, v in session.execute(select(ContentFacet.content_id, ContentFacet.facet,
                                                ContentFacet.value)
                                         .where(ContentFacet.content_id.in_(ids))):
            out[cid].setdefault(f, []).append(v)
    return out


def _card(item: ContentItem, facets: dict[str, list[str]]) -> dict[str, Any]:
    ex = (item.extracted_data or {}).get(EXTRACTION_KEY) or {}
    return {
        "id": str(item.id), "instagram_url": item.canonical_url, "title": item.title,
        "gist": ex.get("gist"), "type": ex.get("post_type") or item.primary_category,
        "topic": ex.get("topic"), "subtopic": ex.get("subtopic"),
        "creator": item.creator_username,
        "saved_at": item.first_captured_at.isoformat() if item.first_captured_at else None,
        "origin": (facets.get("origin") or [None])[0],
        "location": {k: facets.get(k) for k in ("country", "state", "city", "zone")
                     if facets.get(k)},
        "areas": sorted({v for f, vs in facets.items() if f.endswith(".area") for v in vs}),
        "facets": {k: v for k, v in facets.items() if "." not in k},
        "found": _found(ex), "confidence": item.extraction_confidence,
        "hidden": item.archived_at is not None, "hidden_reason": item.archive_reason,
        "notion_page_id": item.notion_page_id,
    }


def _full(item: ContentItem, facets: dict[str, list[str]], include_source: bool) -> dict[str, Any]:
    ex = (item.extracted_data or {}).get(EXTRACTION_KEY) or {}
    data = {**_card(item, facets),
            "key_points": ex.get("key_takeaways") or [], "tips": ex.get("tips") or [],
            "tags": item.tags or [],
            "details": {k: ex[k] for k in SECTIONS if ex.get(k)}}
    if include_source:
        data["source"] = {"summary": item.summary, "caption": item.caption}
    return data


def _filtered(stmt, filters: dict[str, str | None]):
    for arg, value in filters.items():
        if not value:
            continue
        if arg == "area":
            stmt = stmt.where(ContentItem.id.in_(select(ContentFacet.content_id).where(
                ContentFacet.facet.like("%.area"),
                func.lower(ContentFacet.value) == value.lower())))
        else:
            stmt = stmt.where(ContentItem.id.in_(select(ContentFacet.content_id).where(
                ContentFacet.facet == FILTERABLE[arg], ContentFacet.value == value)))
    return stmt


@mcp.tool(annotations=READ)
def search_posts(query: str | None = None, type: str | None = None, topic: str | None = None,
                 city: str | None = None, zone: str | None = None, area: str | None = None,
                 state: str | None = None, country: str | None = None,
                 cuisine: str | None = None, venue_type: str | None = None,
                 trip_type: str | None = None, origin: str | None = None,
                 include_hidden: bool = False, limit: int = 20, offset: int = 0) -> dict:
    """Search useful posts. Filters use Lore's values, e.g. type="eat_drink"|"place"|"trip"|
    "property"|"job"|"recommendations"|"how_to"|"shopping"|"event"; zone="South Delhi";
    city="Noida"; state="Himachal Pradesh"; cuisine="cafe"/"italian"; trip_type="trek";
    origin="saved"|"shared". Use facet_values to discover valid values. Returns cards
    (title, one-line gist, location, facets, Instagram link)."""
    limit = max(1, min(limit, MAX_LIMIT))
    with _session() as s:
        stmt = select(ContentItem).where(ContentItem.last_processed_at.is_not(None))
        if not include_hidden:
            stmt = stmt.where(ContentItem.archived_at.is_(None))
        if query:
            like = f"%{query.strip()}%"
            stmt = stmt.where(or_(ContentItem.title.ilike(like), ContentItem.summary.ilike(like),
                                  ContentItem.caption.ilike(like),
                                  ContentItem.creator_username.ilike(like)))
        stmt = _filtered(stmt, dict(type=type, topic=topic, city=city, zone=zone, area=area,
                                    state=state, country=country, cuisine=cuisine,
                                    venue_type=venue_type, trip_type=trip_type, origin=origin))
        total = s.scalar(select(func.count()).select_from(stmt.subquery()))
        items = s.scalars(stmt.order_by(ContentItem.first_captured_at.desc().nulls_last(),
                                        ContentItem.id).limit(limit).offset(offset)).all()
        facets = _facets(s, [i.id for i in items])
        return {"total": total, "offset": offset,
                "posts": [_card(i, facets[i.id]) for i in items]}


@mcp.tool(annotations=READ)
def get_post(post_id: str, include_source: bool = False) -> dict:
    """Everything Lore extracted for one post: gist, key points, tips and the type-specific
    details (spots with area/cuisine, trip info, book list, steps, property, job…).
    include_source=true adds the AI summary and original caption."""
    with _session() as s:
        item = s.get(ContentItem, uuid.UUID(post_id))
        if item is None:
            return {"error": "not found"}
        return _full(item, _facets(s, [item.id])[item.id], include_source)


@mcp.tool(annotations=READ)
def facet_values(names: list[str], type: str | None = None, city: str | None = None,
                 state: str | None = None) -> dict:
    """Counts of values for facets among useful posts, e.g. names=["zone","cuisine"] with
    type="eat_drink", or names=["state","trip_type"] with type="trip". Facet names include:
    type, topic, city, zone, state, country, cuisine, venue_type, good_for, vibe, price_level,
    place_kind, trip_type, terrain, trek_difficulty, stay, deal, bhk, job_mode, rec_kind,
    genre, howto_domain, diet, origin, and prefixed areas such as venue.area / place.area."""
    with _session() as s:
        base = _filtered(select(ContentItem.id).where(ContentItem.archived_at.is_(None)),
                         dict(type=type, city=city, state=state))
        out = {}
        for n in names[:20]:
            rows = s.execute(select(ContentFacet.value,
                                    func.count(func.distinct(ContentFacet.content_id)))
                             .where(ContentFacet.facet == n, ContentFacet.content_id.in_(base))
                             .group_by(ContentFacet.value)
                             .order_by(func.count(func.distinct(ContentFacet.content_id)).desc())
                             ).all()
            out[n] = [{"value": v, "count": c} for v, c in rows]
        return out


@mcp.tool(annotations=READ)
def posts_to_sync(limit: int = 20) -> dict:
    """Work list for keeping Notion in sync.
    - to_upsert: useful posts never synced or changed since their last sync (full details;
      update the page in notion_page_id if present, otherwise create one)
    - to_archive: posts now hidden that still have a Notion page (archive that page)
    Call mark_synced for every post you handle. Repeat until both lists are empty."""
    limit = max(1, min(limit, MAX_LIMIT))
    with _session() as s:
        classified = ContentItem.last_processed_at.is_not(None)
        upsert = s.scalars(select(ContentItem).where(
            classified, ContentItem.archived_at.is_(None),
            or_(ContentItem.notion_page_id.is_(None), ContentItem.notion_synced_at.is_(None),
                ContentItem.updated_at > ContentItem.notion_synced_at))
            .order_by(ContentItem.first_captured_at.desc().nulls_last()).limit(limit)).all()
        archive = s.execute(select(ContentItem.id, ContentItem.notion_page_id, ContentItem.title)
                            .where(ContentItem.archived_at.is_not(None),
                                   ContentItem.notion_page_id.is_not(None),
                                   ContentItem.notion_sync_status != "archived")
                            .limit(limit)).all()
        remaining = s.scalar(select(func.count()).where(
            classified, ContentItem.archived_at.is_(None),
            or_(ContentItem.notion_page_id.is_(None), ContentItem.notion_synced_at.is_(None),
                ContentItem.updated_at > ContentItem.notion_synced_at)))
        facets = _facets(s, [i.id for i in upsert])
        return {"remaining_to_upsert": remaining,
                "to_upsert": [_full(i, facets[i.id], include_source=False) for i in upsert],
                "to_archive": [{"id": str(i), "notion_page_id": p, "title": t}
                               for i, p, t in archive]}


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=False,
                                      idempotentHint=True, openWorldHint=False))
def mark_synced(post_id: str, notion_page_id: str, archived: bool = False) -> dict:
    """Record that a post is now in Notion (or that its page was archived). Idempotent."""
    with _session() as s:
        item = s.get(ContentItem, uuid.UUID(post_id))
        if item is None:
            return {"error": "not found"}
        now = datetime.now(UTC)
        item.notion_page_id = notion_page_id.strip()[:64]
        item.notion_sync_status = "archived" if archived else "synced"
        item.notion_synced_at = now
        item.updated_at = now  # same instant, so this write doesn't count as a change
        s.commit()
        return {"id": post_id, "status": item.notion_sync_status}


# --------------------------------------------------------------------------------------------
# Whole-library access: overview → category → every post (paged), or a category document.

TYPE_LABELS = {"eat_drink": "Eat & drink", "place": "Places to visit", "trip": "Trip ideas",
               "property": "Property", "job": "Jobs & opportunities",
               "recommendations": "Recommendations", "how_to": "How-tos & recipes",
               "shopping": "Shopping", "event": "Events", "other": "Other"}
BREAKDOWN = {  # sub-groups shown per type in overview()
    "eat_drink": ["venue.city", "zone", "venue_type", "cuisine"],
    "place": ["place.city", "zone", "place_kind"],
    "trip": ["trip_scope", "trip.state", "trip.country", "trip_type"],
    "property": ["property.city", "zone", "deal", "bhk"],
    "job": ["job_mode", "job.city"],
    "recommendations": ["rec_kind", "genre"],
    "how_to": ["howto_domain", "diet"],
    "shopping": ["origin"], "event": ["event.city"], "other": ["topic"],
}
KEY_FACETS = ("venue_type", "cuisine", "place_kind", "trip_type", "terrain", "trek_difficulty",
              "deal", "bhk", "job_mode", "rec_kind", "genre", "howto_domain", "diet",
              "good_for", "vibe", "price_level")


def _counts(s, facet: str, base) -> list[tuple[str, int]]:
    return [(v, c) for v, c in s.execute(
        select(ContentFacet.value, func.count(func.distinct(ContentFacet.content_id)))
        .where(ContentFacet.facet == facet, ContentFacet.content_id.in_(base))
        .group_by(ContentFacet.value)
        .order_by(func.count(func.distinct(ContentFacet.content_id)).desc())).all()]


def _visible_ids(type_: str | None = None):
    stmt = select(ContentItem.id).where(ContentItem.archived_at.is_(None),
                                        ContentItem.last_processed_at.is_not(None))
    return _filtered(stmt, {"type": type_}) if type_ else stmt


def _compact(item: ContentItem, facets: dict[str, list[str]]) -> str:
    """One dense line: title — gist | where | key facts | link."""
    c = _card(item, facets)
    where = ", ".join(c["areas"][:4] + (c["location"].get("zone") or [])
                      + (c["location"].get("city") or c["location"].get("state")
                         or c["location"].get("country") or [])[:2])
    keys = "; ".join(f"{k}: {', '.join(facets[k][:4])}" for k in KEY_FACETS if facets.get(k))
    parts = [f"{c['title']} — {c['gist']}" if c["gist"] else c["title"]]
    parts += [f"where: {where}" if where else None, keys or None, c["instagram_url"]]
    return " | ".join(p for p in parts if p)


@mcp.tool(annotations=READ)
def overview() -> dict:
    """START HERE to see the whole library: every category (post type) with its number of
    useful posts and its main sub-groups (e.g. Eat & drink by city/zone/cuisine, Trips by
    state/trip type, Recommendations by kind/genre). Then use list_category to fetch every
    post in a category, or read the resource lore://category/{type}."""
    with _session() as s:
        total = s.scalar(select(func.count()).select_from(_visible_ids().subquery()))
        hidden = s.scalar(select(func.count()).where(ContentItem.archived_at.is_not(None)))
        cats = []
        for t, n in _counts(s, "type", _visible_ids()):
            base = _visible_ids(t)
            cats.append({"type": t, "label": TYPE_LABELS.get(t, t), "posts": n,
                         "breakdown": {f: [{"value": v, "count": c} for v, c in
                                           _counts(s, f, base)[:12]]
                                       for f in BREAKDOWN.get(t, [])}})
        return {"useful_posts": total, "hidden_posts": hidden, "categories": cats,
                "how_to_fetch": "list_category(type=..., page=1) until next_page is null"}


@mcp.tool(annotations=READ)
def list_category(type: str, page: int = 1, page_size: int = 50, format: str = "compact",
                  city: str | None = None, zone: str | None = None, state: str | None = None,
                  country: str | None = None, cuisine: str | None = None,
                  venue_type: str | None = None, trip_type: str | None = None,
                  origin: str | None = None) -> dict:
    """Every useful post in one category, page by page (oldest pages stay stable: ordered by
    save date, newest first). format="compact" → one line per post (best for reading a whole
    category); format="full" → all details per post (best for creating Notion pages).
    page_size: compact up to 100, full up to 25. Keep calling with next_page until null."""
    full = format == "full"
    page_size = max(1, min(page_size, 25 if full else 100))
    page = max(1, page)
    with _session() as s:
        stmt = _filtered(select(ContentItem).where(ContentItem.archived_at.is_(None),
                                                   ContentItem.last_processed_at.is_not(None)),
                         dict(type=type, city=city, zone=zone, state=state, country=country,
                              cuisine=cuisine, venue_type=venue_type, trip_type=trip_type,
                              origin=origin))
        total = s.scalar(select(func.count()).select_from(stmt.subquery()))
        items = s.scalars(stmt.order_by(ContentItem.first_captured_at.desc().nulls_last(),
                                        ContentItem.id)
                          .limit(page_size).offset((page - 1) * page_size)).all()
        facets = _facets(s, [i.id for i in items])
        pages = max(1, -(-total // page_size))
        return {"type": type, "label": TYPE_LABELS.get(type, type), "total": total,
                "page": page, "pages": pages, "next_page": page + 1 if page < pages else None,
                "posts": [_full(i, facets[i.id], False) if full else
                          {"id": str(i.id), "line": _compact(i, facets[i.id])} for i in items]}


@mcp.resource("lore://category/{type}", title="A whole Lore category",
              description="Every useful post in a category as a Markdown list "
                          "(eat_drink, place, trip, property, job, recommendations, how_to, "
                          "shopping, event, other).", mime_type="text/markdown")
def category_document(type: str) -> str:
    with _session() as s:
        stmt = _filtered(select(ContentItem).where(ContentItem.archived_at.is_(None),
                                                   ContentItem.last_processed_at.is_not(None)),
                         {"type": type})
        items = s.scalars(stmt.order_by(ContentItem.first_captured_at.desc().nulls_last())
                          .limit(2000)).all()
        facets = _facets(s, [i.id for i in items])
        lines = [f"# {TYPE_LABELS.get(type, type)} ({len(items)} posts)", ""]
        lines += [f"- {_compact(i, facets[i.id])}" for i in items]
        return "\n".join(lines)


@mcp.prompt(title="Sync Lore to Notion")
def sync_to_notion(scope: str = "all useful posts") -> str:
    """Step-by-step instructions to create/maintain the Lore database in Notion."""
    return f"""Sync {scope} from Lore into my Notion.

1. Find a Notion database named "Lore" (search). If missing, ask me which page to create it
   under, then create it with properties:
   Title (title) · Instagram (url) · Gist (text) · Type (select) · Topic (select) ·
   City (select) · Zone (select) · Area (multi-select) · State (select) · Country (select) ·
   Cuisine (multi-select) · Venue type (multi-select) · Trip type (multi-select) ·
   Terrain (multi-select) · Good for (multi-select) · Saved or shared (select) ·
   Creator (text) · Saved on (date) · Confidence (number) · Lore ID (text).
   Use friendly labels (eat_drink → "Eat & drink", south delhi stays "South Delhi").
2. Loop: call posts_to_sync(limit=20).
   - For each to_upsert post: if notion_page_id is set, update that page; else create a page.
     Fill properties from the post (location, facets, saved_at, origin, creator, confidence,
     id → Lore ID). Page body: the gist as a callout, then sections built ONLY from
     "details", "key_points" and "tips" (tables or bullets for spots, stops, books, steps…),
     and the Instagram link. Don't invent or embellish anything.
   - Then call mark_synced(post_id, notion_page_id).
   - For each to_archive item: archive that Notion page, then mark_synced(..., archived=true).
   Continue until remaining_to_upsert is 0 and to_archive is empty. Report counts.
3. If the database is new, add views: "Eat & drink" (Type = Eat & drink, grouped by Zone),
   "Trips" (Type = Trip, grouped by State), "Recommendations", "How-tos", "Recently saved"."""


class _BearerAuth(BaseHTTPMiddleware):
    def __init__(self, app, key: str) -> None:
        super().__init__(app)
        self.key = key

    async def dispatch(self, request, call_next):
        supplied = request.headers.get("authorization", "")
        token = supplied[7:] if supplied.lower().startswith("bearer ") else ""
        if not self.key or not secrets.compare_digest(token, self.key):
            return JSONResponse({"error": "unauthorized"}, status_code=401)
        return await call_next(request)


def build_app():
    settings = get_settings()
    hosts = ["localhost", "localhost:*", "127.0.0.1", "127.0.0.1:*"]
    for h in (settings.mcp_allowed_hosts or "").split(","):
        if h.strip():
            hosts += [h.strip(), f"{h.strip()}:*"]
    app = mcp.streamable_http_app(
        host=settings.mcp_host,
        transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=True,
                                                     allowed_hosts=hosts),
    )
    key = settings.mcp_key.get_secret_value() if settings.mcp_key else ""
    app.add_middleware(_BearerAuth, key=key)
    return app


def main() -> None:
    import sys

    if "--stdio" in sys.argv:
        # Local assistant on this machine (e.g. Claude Desktop launching Lore itself): no network,
        # no key. stdout carries the protocol, so nothing else may print to it.
        mcp.run("stdio")
        return

    import uvicorn

    settings = get_settings()
    if not settings.mcp_key:
        raise SystemExit("Set LORE_MCP_KEY first (e.g. `openssl rand -hex 32`).")
    uvicorn.run(build_app(), host=settings.mcp_host, port=settings.mcp_port, log_level="info")


if __name__ == "__main__":
    main()
