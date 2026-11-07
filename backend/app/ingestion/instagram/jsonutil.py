"""Schema-tolerant helpers for Instagram export JSON.

Meta's JSON exports are known to encode non-ASCII text as UTF-8 bytes escaped as
Latin-1 code points ("mojibake", e.g. "cafÃ©" for "café"). ``repair_text`` reverses that
only when the round-trip is lossless, so correctly-encoded text is left untouched.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from datetime import UTC, datetime
from typing import Any

# Plausible range for content timestamps: 2010-01-01 .. 2100-01-01 (seconds).
_MIN_TS = 1262304000
_MAX_TS = 4102444800

URL_KEYS = ("href", "link", "url", "uri")
TIMESTAMP_KEYS = (
    "timestamp", "timestamp_ms", "timestamp_value", "creation_timestamp", "taken_at", "time",
)


def repair_text(value: str) -> str:
    if not value or value.isascii():
        return value
    try:
        return value.encode("latin-1").decode("utf-8")
    except (UnicodeEncodeError, UnicodeDecodeError):
        return value


def repair_tree(node: Any) -> Any:
    if isinstance(node, str):
        return repair_text(node)
    if isinstance(node, list):
        return [repair_tree(v) for v in node]
    if isinstance(node, dict):
        return {repair_text(k): repair_tree(v) for k, v in node.items()}
    return node


def load_json(data: bytes) -> Any:
    """Decode export JSON (UTF-8, optional BOM) and repair mojibake. Raises ValueError."""
    text = data.decode("utf-8-sig")
    return repair_tree(json.loads(text))


def parse_timestamp(value: Any) -> datetime | None:
    """Normalise epoch seconds, epoch milliseconds, numeric strings or ISO-8601 to UTC."""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, str):
        s = value.strip()
        if not s:
            return None
        try:
            value = float(s)
        except ValueError:
            try:
                dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
            except ValueError:
                return None
            return dt.replace(tzinfo=UTC) if dt.tzinfo is None else dt.astimezone(UTC)
    if isinstance(value, int | float):
        ts = float(value)
        for divisor in (1, 1_000, 1_000_000):
            if _MIN_TS <= ts / divisor <= _MAX_TS:
                return datetime.fromtimestamp(ts / divisor, UTC)
    return None


def walk(node: Any, path: tuple[str | int, ...] = ()) -> Iterator[tuple[tuple, Any]]:
    yield path, node
    if isinstance(node, dict):
        for k, v in node.items():
            yield from walk(v, (*path, k))
    elif isinstance(node, list):
        for i, v in enumerate(node):
            yield from walk(v, (*path, i))


def iter_url_values(node: Any) -> Iterator[str]:
    """Yield every string value stored under a URL-like key anywhere in ``node``."""
    for path, value in walk(node):
        if (
            path
            and isinstance(path[-1], str)
            and path[-1].lower() in URL_KEYS
            and isinstance(value, str)
        ):
            yield value


def iter_strings(node: Any) -> Iterator[str]:
    for _, value in walk(node):
        if isinstance(value, str):
            yield value


def first_timestamp(node: Any) -> datetime | None:
    """Earliest-in-document timestamp found under a timestamp-like key."""
    for path, value in walk(node):
        if path and isinstance(path[-1], str) and path[-1].lower() in TIMESTAMP_KEYS:
            ts = parse_timestamp(value)
            if ts:
                return ts
    return None


def find_list_of_dicts(node: Any) -> tuple[str | None, list[dict]]:
    """Find the main record list in a top-level export object.

    Instagram wraps records as ``{"<some_key>": [ {...}, ... ]}``; the key name has changed
    between export versions, so we pick the largest list of dicts rather than a fixed key.
    """
    if isinstance(node, list):
        return None, [x for x in node if isinstance(x, dict)]
    if not isinstance(node, dict):
        return None, []
    best: tuple[str | None, list[dict]] = (None, [])
    for key, value in node.items():
        if isinstance(value, list):
            dicts = [x for x in value if isinstance(x, dict)]
            if len(dicts) > len(best[1]):
                best = (key, dicts)
    return best


def labelled_value(record: dict, label: str) -> Any:
    """Read a labelled field from either known record shape.

    - ``{"string_map_data": {"Name": {"value": ...}}}``
    - ``{"label_values": [{"label": "Name", "value": ...}]}``
    Returns the inner dict for the label (or None).
    """
    smd = record.get("string_map_data")
    if isinstance(smd, dict):
        for k, v in smd.items():
            if isinstance(k, str) and k.lower() == label.lower() and isinstance(v, dict):
                return v
    lv = record.get("label_values")
    if isinstance(lv, list):
        for item in lv:
            if (
                isinstance(item, dict)
                and isinstance(item.get("label"), str)
                and item["label"].lower() == label.lower()
            ):
                return item
    return None
