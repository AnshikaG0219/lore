"""Flatten a classification into (facet, value) rows for drill-down filters and counts."""

from __future__ import annotations

from typing import Any

from app.processing.classification.gazetteer import resolve


def _loc_fields(loc: dict | None, extra_name: str | None = None) -> list[str | None]:
    loc = loc or {}
    return [loc.get("area"), loc.get("place"), extra_name, loc.get("city"), loc.get("state"),
            loc.get("country")]


def _add_place(rows: set, prefix: str, *fields: str | None) -> None:
    p = resolve(*fields)
    for facet, value in (("country", p.country), ("state", p.state), ("city", p.city),
                         ("zone", p.zone), ("area", p.area)):
        if value:
            rows.add((f"{prefix}{facet}", value))


def build_facets(c: dict[str, Any], *, origin: str | None = None) -> list[tuple[str, str]]:
    """Facets for one item. Location facets are prefixed by what they locate:
    ``venue.zone``, ``trip.state``, ``property.city`` … plus a combined ``city``/``state``."""
    rows: set[tuple[str, str]] = set()

    def add(facet: str, value: Any) -> None:
        if value not in (None, "", [], "other"):
            rows.add((facet, str(value)))

    post_type = c.get("post_type") or "other"
    add("type", post_type)
    add("topic", c.get("topic"))
    if c.get("subtopic"):
        add("subtopic", str(c["subtopic"]).strip().lower()[:80])
    add("origin", origin)
    if c.get("information_sufficient") is False:
        add("quality", "unclear")

    ed = c.get("eat_drink") or {}
    for v in ed.get("venues") or []:
        add("venue_type", v.get("venue_type"))
        for x in v.get("cuisines") or []:
            add("cuisine", x)
        for x in v.get("good_for") or []:
            add("good_for", x)
        for x in v.get("vibe") or []:
            add("vibe", x)
        add("price_level", v.get("price_level"))
        _add_place(rows, "venue.", *_loc_fields(v.get("location"), v.get("name")))

    for pv in (c.get("place") or {}).get("places") or []:
        add("place_kind", pv.get("kind"))
        for x in pv.get("good_for") or []:
            add("good_for", x)
        for x in pv.get("vibe") or []:
            add("vibe", x)
        _add_place(rows, "place.", *_loc_fields(pv.get("location"), pv.get("name")))

    tr = c.get("trip") or {}
    if tr:
        add("trip_scope", tr.get("scope"))
        for x in tr.get("trip_types") or []:
            add("trip_type", x)
        for x in tr.get("terrain") or []:
            add("terrain", x)
        for x in tr.get("stays") or []:
            add("stay", x)
        trek = tr.get("trek") or {}
        if trek:
            add("trip_type", "trek")
            add("trek_difficulty", trek.get("difficulty"))
        for d in tr.get("destinations") or []:
            _add_place(rows, "trip.", *_loc_fields(d))
        for pl in tr.get("places_to_visit") or []:
            _add_place(rows, "trip.", *_loc_fields(pl.get("location"), pl.get("name")))
        if any(f == "trip.country" and v != "India" for f, v in rows):
            add("trip_scope", "international")
        elif any(f == "trip.state" for f, _ in rows):
            add("trip_scope", "india")

    pr = c.get("property") or {}
    if pr:
        add("deal", pr.get("deal"))
        if pr.get("bhk"):
            add("bhk", f"{pr['bhk']} BHK")
        _add_place(rows, "property.", *_loc_fields(pr.get("location"), pr.get("project")))

    job = c.get("job") or {}
    if job:
        add("job_mode", job.get("mode"))
        _add_place(rows, "job.", *_loc_fields(job.get("location")))

    rec = c.get("recommendations") or {}
    if rec:
        add("rec_kind", rec.get("kind"))
        for item in rec.get("items") or []:
            add("genre", item.get("genre"))

    ht = c.get("how_to") or {}
    if ht:
        add("howto_domain", ht.get("domain"))
        recipe = ht.get("recipe") or {}
        add("cuisine", recipe.get("cuisine"))
        add("diet", recipe.get("diet"))
        for part in (ht.get("workout") or {}).get("body_parts") or []:
            add("body_part", str(part).strip().lower()[:40])

    ev = c.get("event") or {}
    if ev:
        _add_place(rows, "event.", *_loc_fields(ev.get("location")))

    add("theme", (c.get("thought") or {}).get("theme"))
    add("humour", (c.get("fun") or {}).get("humour_type"))

    # Combined location facets across all sections (for "everything in Noida").
    for facet, value in list(rows):
        if "." in facet and facet.split(".", 1)[1] in ("city", "state", "country", "zone"):
            rows.add((facet.split(".", 1)[1], value))
    return sorted(rows)


def item_origin(session, content_id) -> str:
    from sqlalchemy import select

    from app.db.models import ContentSource

    types = set(session.scalars(select(ContentSource.source_type).where(
        ContentSource.content_id == content_id)))
    return "saved" if types & {"saved_post", "saved_collection"} else "shared"


def replace_facets(session, content_id, classification: dict[str, Any]) -> int:
    """Rewrite an item's facet rows (idempotent). Caller commits."""
    from sqlalchemy import delete

    from app.db.models import ContentFacet

    session.execute(delete(ContentFacet).where(ContentFacet.content_id == content_id))
    rows = build_facets(classification, origin=item_origin(session, content_id))
    session.add_all(ContentFacet(content_id=content_id, facet=f, value=v) for f, v in rows)
    return len(rows)
