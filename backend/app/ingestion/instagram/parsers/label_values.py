"""Helpers for the ``label_values`` record format used by current Instagram exports.

Verified against a real export (2026-11): saved posts and collection items look like::

    {"timestamp": 1792783662, "media": [], "label_values": [
        {"label": "URL", "value": "<url>", "href": "<url>"},
        {"label": "Caption", "value": "..."},
        {"label": "Title", "value": ""},
        {"title": "Hashtags", "dict": [{"dict": [{"label": "Name", "value": "tag"}], "title": ""}]},
        {"title": "Owner", "dict": [{"dict": [{"label": "URL", "value": ""},
                                             {"label": "Name", "value": "Display Name"},
                                             {"label": "Username", "value": "handle"}],
                                    "title": ""}]},
        {"title": "Brand partner", "dict": []}]}

Some records repeat ``Caption``/``Title`` and omit ``Owner``; the first occurrence wins.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


def _items(container: Any) -> list[dict]:
    return [x for x in container if isinstance(x, dict)] if isinstance(container, list) else []


def label_items(record: dict) -> list[dict]:
    return _items(record.get("label_values"))


def label(items: list[dict], name: str) -> dict | None:
    for item in items:
        if isinstance(item.get("label"), str) and item["label"].lower() == name.lower():
            return item
    return None


def label_str(items: list[dict], name: str) -> str | None:
    item = label(items, name)
    if item is None:
        return None
    value = item.get("value")
    return value.strip() or None if isinstance(value, str) else None


def section(items: list[dict], title: str) -> list[list[dict]]:
    """Entries of a titled section, each entry being its own list of label items."""
    for item in items:
        if isinstance(item.get("title"), str) and item["title"].lower() == title.lower():
            return [_items(entry.get("dict")) for entry in _items(item.get("dict"))]
    return []


def has_section(items: list[dict], title: str) -> bool:
    return any(
        isinstance(i.get("title"), str) and i["title"].lower() == title.lower() for i in items
    )


@dataclass(slots=True)
class MediaFields:
    url: str | None
    caption: str | None = None
    creator_username: str | None = None
    creator_name: str | None = None
    hashtags: list[str] = field(default_factory=list)

    def metadata(self) -> dict[str, Any]:
        data: dict[str, Any] = {}
        if self.caption:
            data["caption"] = self.caption
        if self.hashtags:
            data["hashtags"] = self.hashtags
        if self.creator_name:
            data["creator_name"] = self.creator_name
        return data


def media_fields(items: list[dict]) -> MediaFields:
    url_item = label(items, "URL")
    url = None
    if url_item:
        for key in ("href", "value"):
            if isinstance(url_item.get(key), str) and url_item[key].strip():
                url = url_item[key].strip()
                break
    owner = next(iter(section(items, "Owner")), [])
    hashtags = [tag for entry in section(items, "Hashtags") if (tag := label_str(entry, "Name"))]
    return MediaFields(
        url=url,
        caption=label_str(items, "Caption"),
        creator_username=label_str(owner, "Username"),
        creator_name=label_str(owner, "Name"),
        hashtags=hashtags,
    )
