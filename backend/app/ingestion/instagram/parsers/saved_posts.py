"""Saved posts parser (``saved_posts.json``).

Known shape (to be confirmed against real fixtures, see docs/KNOWN_LIMITATIONS.md):
``{"<list key>": [{"title": "<creator>", "string_map_data": {"Saved on": {"href", "timestamp"}}}]}``
The parser does not depend on those key names: it takes the main list of records and
harvests URLs and timestamps from anywhere inside each record.
"""

from __future__ import annotations

from typing import Any

from app.ingestion.instagram.jsonutil import find_list_of_dicts, first_timestamp
from app.ingestion.instagram.models import (
    ContentReference,
    FileCategory,
    FileParseResult,
    ParseIssue,
    SourceType,
)
from app.ingestion.instagram.parsers.shared_links import links_from_node

PARSER_NAME = "saved_posts.v1"


def _creator(record: dict) -> str | None:
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
        links = links_from_node(record, include_free_text=False)
        if not links:
            result.unresolved_records.append(record)
            result.issues.append(
                ParseIssue(path, "Saved record has no Instagram URL", "warning", locator)
            )
            continue
        saved_at = first_timestamp(record)
        for link in links:
            result.references.append(
                ContentReference(
                    url=link,
                    source_type=SourceType.SAVED_POST,
                    source_name="Saved",
                    source_reference=locator,
                    captured_at=saved_at,
                    creator_username=_creator(record) or link.username,
                    raw=record,
                )
            )
    return result
