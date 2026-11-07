"""Saved collections parser (``saved_collections.json``).

Known shape (to be confirmed against real fixtures): a flat, ordered list where a
"header" record (no URL, has a ``Name`` value) starts a collection, and subsequent
records with a URL belong to that collection until the next header.
"""

from __future__ import annotations

from typing import Any

from app.ingestion.instagram.jsonutil import find_list_of_dicts, first_timestamp, labelled_value
from app.ingestion.instagram.models import (
    ContentReference,
    FileCategory,
    FileParseResult,
    ParseIssue,
    SourceType,
)
from app.ingestion.instagram.parsers.shared_links import links_from_node

PARSER_NAME = "saved_collections.v1"


def _name_value(record: dict) -> str | None:
    field = labelled_value(record, "Name")
    if isinstance(field, dict):
        value = field.get("value")
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _timestamp_for(record: dict, *labels: str):
    for label in labels:
        field = labelled_value(record, label)
        if isinstance(field, dict):
            ts = first_timestamp(field)
            if ts:
                return ts
    return first_timestamp(record)


def parse_saved_collections(path: str, data: Any) -> FileParseResult:
    result = FileParseResult(
        path=path, category=FileCategory.SAVED_COLLECTIONS, parser=PARSER_NAME
    )
    list_key, records = find_list_of_dicts(data)
    if not records:
        result.issues.append(
            ParseIssue(path, "No collection records found (unrecognised structure)", "warning")
        )
        return result

    current: str | None = None
    for index, record in enumerate(records):
        result.records_seen += 1
        locator = f"{path}#{list_key or ''}[{index}]"
        links = links_from_node(record, include_free_text=False)

        if not links:
            name = _name_value(record)
            if name:
                current = name
                result.collections.setdefault(current, 0)
            else:
                result.unresolved_records.append(record)
                result.issues.append(
                    ParseIssue(path, "Collection record has no URL or name", "warning", locator)
                )
            continue

        if current is None:
            result.issues.append(
                ParseIssue(path, "Item appears before any collection header", "warning", locator)
            )
        added_at = _timestamp_for(record, "Added Time", "Added time")
        creator = _name_value(record) or (
            record.get("title") if isinstance(record.get("title"), str) else None
        )
        for link in links:
            result.references.append(
                ContentReference(
                    url=link,
                    source_type=SourceType.SAVED_COLLECTION,
                    source_name=current,
                    source_reference=locator,
                    captured_at=added_at,
                    creator_username=creator or link.username,
                    metadata={"collection": current},
                    raw=record,
                )
            )
        if current is not None:
            result.collections[current] = result.collections.get(current, 0) + 1
    return result
