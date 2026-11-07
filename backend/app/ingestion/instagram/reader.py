"""Drive classification and parsing over an export, one file at a time."""

from __future__ import annotations

import logging
from collections.abc import Callable, Iterator
from typing import Any

from app.ingestion.instagram.archive import ArchiveMember, ExportArchive, MemberReadError
from app.ingestion.instagram.detect import (
    classify_json_shape,
    classify_path,
    is_relevant_json_candidate,
)
from app.ingestion.instagram.jsonutil import load_json
from app.ingestion.instagram.models import FileCategory, FileParseResult, ParseIssue
from app.ingestion.instagram.parsers import (
    parse_messages,
    parse_saved_collections,
    parse_saved_posts,
)

log = logging.getLogger(__name__)

PARSERS: dict[FileCategory, Callable[[str, Any], FileParseResult]] = {
    FileCategory.SAVED_POSTS: parse_saved_posts,
    FileCategory.SAVED_COLLECTIONS: parse_saved_collections,
    FileCategory.MESSAGES: parse_messages,
}

PARSEABLE = frozenset(PARSERS)


def parse_member(archive: ExportArchive, member: ArchiveMember) -> FileParseResult:
    """Classify and parse a single member. Never raises for bad content."""
    category = classify_path(member.path)
    if category is not None and category not in PARSEABLE:
        return FileParseResult(path=member.path, category=category)
    if category is None and not is_relevant_json_candidate(member.path):
        return FileParseResult(path=member.path, category=FileCategory.OTHER_JSON)

    try:
        data = load_json(archive.read_bytes(member.path))
    except MemberReadError as exc:
        return _failed(member.path, category, str(exc))
    except (ValueError, UnicodeDecodeError) as exc:
        return _failed(member.path, category, f"Malformed JSON: {exc}")

    if category is None:
        category = classify_json_shape(data)
        if category not in PARSEABLE:
            return FileParseResult(path=member.path, category=category)

    try:
        return PARSERS[category](member.path, data)
    except Exception as exc:  # parser bug or wildly unexpected shape: report, don't abort
        log.exception("parser_failed", extra={"path": member.path, "category": category})
        return _failed(member.path, category, f"Parser failure: {exc!r}")


def _failed(path: str, category: FileCategory | None, message: str) -> FileParseResult:
    result = FileParseResult(path=path, category=category or FileCategory.OTHER_JSON)
    result.issues.append(ParseIssue(path, message, "error"))
    return result


def iter_parse(archive: ExportArchive) -> Iterator[FileParseResult]:
    for member in archive.members():
        yield parse_member(archive, member)
