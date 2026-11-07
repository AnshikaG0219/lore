"""Saved posts parser (``saved_posts.json``).

Primary format (verified against a real export, 2026-11): a top-level list of records with
``timestamp`` (save time, epoch seconds) and ``label_values`` (URL, Caption, Hashtags, Owner);
see ``label_values.py``.

Fallback: for any other shape, the main list of records is located without relying on key
names and URLs/timestamps are harvested from anywhere inside each record.
"""

from __future__ import annotations

from typing import Any

from app.ingestion.instagram.jsonutil import find_list_of_dicts, first_timestamp, parse_timestamp
from app.ingestion.instagram.models import (
    ContentReference,
    FileCategory,
    FileParseResult,
    ParseIssue,
    SourceType,
)
from app.ingestion.instagram.parsers.label_values import label_items, media_fields
from app.ingestion.instagram.parsers.shared_links import links_from_node
from app.ingestion.url_parser import parse_url

PARSER_NAME = "saved_posts.v2"


def _fallback_creator(record: dict) -> str | None:
    title = record.get("title")
    return title.strip() or None if isinstance(title, str) else None


def parse_saved_posts(path: str, data: Any) -> FileParseResult:
    result = FileParseResult(path=path, category=FileCategory.SAVED_POSTS, parser=PARSER_NAME)
    list_key, records = find_list_of_dicts(data)
    if not records:
        result.issues.append(
            ParseIssue(path, "No saved-post records found (unrecognised structure)", "warning")
        )
        return result

    for index, record in enumerate(records):
        result.records_seen += 1
        locator = f"{path}#{list_key or ''}[{index}]"
        saved_at = parse_timestamp(record.get("timestamp")) or first_timestamp(record)

        items = label_items(record)
        fields = media_fields(items) if items else None
        if fields and fields.url:
            parsed = parse_url(fields.url)
            links = [parsed] if parsed else []
        else:
            links = links_from_node(record, include_free_text=False)

        if not links:
            result.unresolved_records.append(record)
            result.issues.append(
                ParseIssue(path, "Saved record has no Instagram URL", "warning", locator)
            )
            continue
        for link in links:
            result.references.append(
                ContentReference(
                    url=link,
                    source_type=SourceType.SAVED_POST,
                    source_name="Saved",
                    source_reference=locator,
                    captured_at=saved_at,
                    creator_username=(fields.creator_username if fields else None)
                    or _fallback_creator(record) or link.username,
                    caption=fields.caption if fields else None,
                    metadata=fields.metadata() if fields else {},
                    raw=record,
                )
            )
    return result
