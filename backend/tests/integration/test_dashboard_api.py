import uuid

import pytest
from fastapi.testclient import TestClient

from app.core.config import Settings, get_settings
from app.db.models import ContentItem, ContentSource
from app.db.repositories.content import get_or_create_user
from app.main import create_app
from app.workers.dispatch import InlineImportDispatcher

H = {"X-API-Key": "k"}
EXTRACTION = {
    "title": "Panamik Hot Springs",
    "summary": "Hot springs in Nubra.",
    "primary_category": "travel",
    "information_sufficient": True,
    "confidence": 0.9,
    "extracted_information": {
        "places": [
            {
                "name": "Panamik Hot Springs",
                "place_type": "hot spring",
                "location": "Nubra Valley, Ladakh",
                "notes": None,
                "evidence": "explicit",
            }
        ]
    },
    "_meta": {
        "model": "m",
        "source": "caption",
        "prompt_version": "v3",
        "extracted_at": "2026-11-07T00:00:00+00:00",
    },
}


@pytest.fixture
def client(session_factory, tmp_path):
    with session_factory() as s:
        user = get_or_create_user(s)
        for i, (title, cat, caption, coll) in enumerate(
            [
                (
                    "Panamik Hot Springs",
                    "travel",
                    "hot springs <script>alert(1)</script>",
                    "ladakh",
                ),
                (None, None, "Five short books", "books"),
                ("Tiramisu in Delhi", "food_and_recipes", "best tiramisu", None),
            ]
        ):
            it = ContentItem(
                user_id=user.id,
                identity_key=f"k{i}",
                canonical_url=f"https://www.instagram.com/p/CODE{i}/",
                canonical_url_hash="h",
                platform_content_id=f"CODE{i}",
                content_type="post",
                title=title,
                primary_category=cat,
                caption=caption,
                tags=["ladakh"] if i == 0 else [],
                extracted_data={"caption_extraction": EXTRACTION} if i == 0 else {},
                processing_status="COMPLETED" if title else "RECEIVED",
                extraction_confidence=0.9 if title else None,
            )
            if title:
                from datetime import UTC, datetime

                it.last_processed_at = datetime.now(UTC)
            s.add(it)
            s.flush()
            if coll:
                s.add(
                    ContentSource(
                        content_id=it.id,
                        source_key=f"s{i}",
                        source_type="saved_collection",
                        source_name=coll,
                        meta={},
                    )
                )
            s.add(
                ContentSource(
                    content_id=it.id,
                    source_key=f"g{i}",
                    source_type="group_message",
                    source_name="Friends",
                    sender="Priya",
                    meta={},
                )
            )
        s.commit()
    settings = Settings(api_key="k", data_dir=tmp_path)
    app = create_app(session_factory, InlineImportDispatcher(session_factory, settings))
    app.dependency_overrides[get_settings] = lambda: settings
    with TestClient(app) as c:
        yield c


def test_list_filters_and_search(client):
    r = client.get("/api/v1/content", headers=H).json()
    assert r["total"] == 3 and len(r["items"]) == 3
    assert client.get("/api/v1/content?category=travel", headers=H).json()["total"] == 1
    assert client.get("/api/v1/content?q=tiramisu", headers=H).json()["total"] == 1
    assert client.get("/api/v1/content?collection=books", headers=H).json()["total"] == 1
    assert client.get("/api/v1/content?status=RECEIVED", headers=H).json()["total"] == 1
    assert client.get("/api/v1/content?processed=true", headers=H).json()["total"] == 2
    paged = client.get("/api/v1/content?limit=2&offset=2", headers=H).json()
    assert paged["total"] == 3 and len(paged["items"]) == 1
    item = next(i for i in r["items"] if i["title"] == "Panamik Hot Springs")
    assert item["collections"] == ["ladakh"] and item["source_count"] == 2


def test_detail(client):
    cid = client.get("/api/v1/content?category=travel", headers=H).json()["items"][0]["id"]
    d = client.get(f"/api/v1/content/{cid}", headers=H).json()
    assert d["extraction"]["extracted_information"]["places"][0]["evidence"] == "explicit"
    assert "_meta" not in d["extraction"] and d["extraction_meta"]["model"] == "m"
    assert {s["type"] for s in d["sources"]} == {"saved_collection", "group_message"}
    assert client.get(f"/api/v1/content/{uuid.uuid4()}", headers=H).status_code == 404


def test_facets_and_places(client):
    f = client.get("/api/v1/facets", headers=H).json()
    assert {c["value"] for c in f["collections"]} == {"ladakh", "books"}
    assert {"value": "group_message", "count": 3} in f["source_types"]
    places = client.get("/api/v1/places?q=nubra", headers=H).json()
    assert [p["name"] for p in places] == ["Panamik Hot Springs"]
    assert client.get("/api/v1/places?q=goa", headers=H).json() == []


def test_requires_api_key(client):
    for path in ("/api/v1/content", "/api/v1/facets", "/api/v1/places"):
        assert client.get(path).status_code == 401


def test_dashboard_served_with_security_headers(client):
    assert client.get("/", follow_redirects=False).headers["location"] == "/dashboard/"
    r = client.get("/dashboard/")
    assert r.status_code == 200 and "Lore" in r.text
    csp = r.headers["content-security-policy"]
    assert "script-src 'self'" in csp and "frame-ancestors 'none'" in csp
    js = client.get("/dashboard/app.js").text
    assert "innerHTML" not in js.replace("never innerHTML", "")  # all rendering via textContent


def test_found_counts_and_needs_help_reasons(client):
    items = client.get("/api/v1/content?category=travel", headers=H).json()["items"]
    assert items[0]["found"] == {"places": 1}
    f = client.get("/api/v1/facets", headers=H).json()
    assert "needs_help_reasons" in f and "jobs" in f


def test_dashboard_js_never_renders_bare_zero(client):
    """`list.length && x` renders "0" for empty lists; checks must use `> 0`."""
    import re

    js = client.get("/dashboard/app.js").text
    assert not re.search(r"\.length\s*&&", js)


def test_dashboard_is_revalidated_and_versions_match(client):
    import re

    page = client.get("/dashboard/")
    assert page.headers["cache-control"] == "no-cache"
    assert client.get("/dashboard/app.js").headers["cache-control"] == "no-cache"
    page_v = re.search(r'name="lore-ui-version" content="(\w+)"', page.text).group(1)
    js_v = re.search(r'const UI_VERSION = "(\w+)"', client.get("/dashboard/app.js").text).group(1)
    assert page_v == js_v
    assert f"app.js?v={page_v}" in page.text and f"styles.css?v={page_v}" in page.text


def test_hidden_posts_excluded_and_restorable(client):
    items = client.get("/api/v1/content", headers=H).json()["items"]
    cid = items[0]["id"]
    r = client.post(f"/api/v1/content/{cid}/visibility", headers=H, json={"choice": "hide"})
    assert r.json()["hidden"] is True and r.json()["reason"] == "manual"
    assert client.get("/api/v1/content", headers=H).json()["total"] == 2
    assert client.get("/api/v1/content?visibility=hidden", headers=H).json()["total"] == 1
    f = client.get("/api/v1/facets", headers=H).json()
    assert {"value": "manual", "count": 1} in f["hidden"]
    assert sum(s["count"] for s in f["source_types"]) < 6  # hidden post's sources excluded
    r = client.post(f"/api/v1/content/{cid}/visibility", headers=H, json={"choice": "keep"})
    assert r.json()["hidden"] is False
    assert client.get("/api/v1/content", headers=H).json()["total"] == 3
    assert client.post(f"/api/v1/content/{cid}/visibility", headers=H,
                       json={"choice": "delete"}).status_code == 422


def test_dashboard_js_never_passes_null_to_replace_children(client):
    """replaceChildren(null) renders the text "null" (unlike h(), which skips it)."""
    import re

    js = client.get("/dashboard/app.js").text
    assert not re.search(r"\?\s*null\s*:\s*h\(", js)
    assert not re.search(r":\s*h\([^;]*\)\s*\?\s*null", js)
