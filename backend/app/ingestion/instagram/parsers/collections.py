"""Saved collections parser (``saved_collections.json``).

Primary format (verified against a real export, 2026-11): a top-level list with one record per
collection::

    {"timestamp": <created?>, "fbid": "...", "media": [],
     "label_values": [{"label": "Name", "value": "ladakh"},
                      {"label": "Type", "value": "Default"},
                      {"label": "Privacy", "value": "Collaborative"},
                      {"label": "Update time", "timestamp_value": ...},
                      {"title": "Media", "dict": [{"dict": [<media label items>], "title": ""}]}]}

Items carry **no per-item "added" timestamp**, so ``captured_at`` is left empty rather than
guessed; collection timestamps are kept in metadata.

Fallback (unverified, kept for older exports): a flat ordered list where a record with a
``Name`` and no URL starts a collection and following records with URLs belong to it.
"""

from __future__ import annotations

from typing import Any

from app.ingestion.instagram.jsonutil import find_list_of_dicts, first_timestamp, labelled_value
from app.ingestion.instagram.jsonutil import parse_timestamp as _ts
from app.ingestion.instagram.models import (
    ContentReference,
    FileCategory,
    FileParseResult,
    ParseIssue,
    SourceType,
)
from app.ingestion.instagram.parsers.label_values import (
    has_section,
    label,
    label_items,
    label_str,
    media_fields,
    section,
)
from app.ingestion.instagram.parsers.shared_links import links_from_node
from app.ingestion.url_parser import parse_url

PARSER_NAME = "saved_collections.v2"


def _iso(value: Any) -> str | None:
    dt = _ts(value)
    return dt.isoformat() if dt else None


def _parse_nested(path: str, list_key: str | None, records: list[dict],
                  result: FileParseResult) -> None:
    for c_index, record in enumerate(records):
        result.records_seen += 1
        items = label_items(record)
        name = label_str(items, "Name")
        c_locator = f"{path}#{list_key or ''}[{c_index}]"
        if not name:
            result.issues.append(
                ParseIssue(path, "Collection without a name", "warning", c_locator)
            )
        update = label(items, "Update time") or {}
        collection_meta = {
            "collection": name,
            "collection_id": record.get("fbid"),
            "collection_type": label_str(items, "Type"),
            "collection_privacy": label_str(items, "Privacy"),
            "collection_timestamp": _iso(record.get("timestamp")),
            "collection_updated_at": _iso(update.get("timestamp_value")),
        }
        result.collections.setdefault(name or "", 0)

        for m_index, media_items in enumerate(section(items, "Media")):
            locator = f"{c_locator}.media[{m_index}]"
            fields = media_fields(media_items)
            parsed = parse_url(fields.url) if fields.url else None
            if parsed is None:
                result.unresolved_records.append({"label_values": media_items})
                result.issues.append(
                    ParseIssue(path, "Collection item has no Instagram URL", "warning", locator)
                )
                continue
            result.collections[name or ""] += 1
            result.references.append(
                ContentReference(
                    url=parsed,
                    source_type=SourceType.SAVED_COLLECTION,
                    source_name=name,
                    source_reference=locator,
                    captured_at=None,
                    creator_username=fields.creator_username or parsed.username,
                    caption=fields.caption,
                    metadata={**collection_meta, **fields.metadata()},
                    raw={"collection": collection_meta, "label_values": media_items},
                )
            )


def _legacy_name(record: dict) -> str | None:
    field = labelled_value(record, "Name")
    if isinstance(field, dict):
        value = field.get("value")
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _parse_flat(path: str, list_key: str | None, records: list[dict],
                result: FileParseResult) -> None:
    current: str | None = None
    for index, record in enumerate(records):
        result.records_seen += 1
        locator = f"{path}#{list_key or ''}[{index}]"
        links = links_from_node(record, include_free_text=False)
        if not links:
            name = _legacy_name(record)
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
        added = labelled_value(record, "Added Time")
        added_at = first_timestamp(added) if isinstance(added, dict) else None
        creator = _legacy_name(record) or (
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

    nested = any(has_section(label_items(r), "Media") for r in records)
    (_parse_nested if nested else _parse_flat)(path, list_key, records, result)
    return result
