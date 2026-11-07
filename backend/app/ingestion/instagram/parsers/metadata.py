"""Export-level metadata: layout, format and file inventory. No personal profile data."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field

from app.ingestion.instagram.archive import ArchiveMember


@dataclass(slots=True)
class ExportMetadata:
    root_prefix: str | None
    layout: str
    formats: dict[str, int] = field(default_factory=dict)
    total_files: int = 0
    total_bytes: int = 0


def describe_export(members: list[ArchiveMember]) -> ExportMetadata:
    paths = [m.path for m in members]
    first_segments = {p.split("/", 1)[0] for p in paths if "/" in p}
    root = None
    if len(first_segments) == 1 and all("/" in p for p in paths):
        root = next(iter(first_segments))

    lowered = [p.lower() for p in paths]
    if any("your_instagram_activity/" in p for p in lowered):
        layout = "your_instagram_activity"
    elif any(p.startswith(("saved/", "messages/")) or "/saved/" in p or "/messages/" in p
             for p in lowered):
        layout = "legacy_flat"
    else:
        layout = "unknown"

    formats = Counter()
    for p in lowered:
        if p.endswith(".json"):
            formats["json"] += 1
        elif p.endswith((".html", ".htm")):
            formats["html"] += 1
        else:
            formats["other"] += 1

    return ExportMetadata(
        root_prefix=root,
        layout=layout,
        formats=dict(formats),
        total_files=len(members),
        total_bytes=sum(m.size for m in members),
    )
