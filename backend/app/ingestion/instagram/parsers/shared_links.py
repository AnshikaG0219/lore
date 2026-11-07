"""Shared-link extraction: turn arbitrary JSON/text into parsed Instagram URLs."""

from __future__ import annotations

from typing import Any

from app.ingestion.instagram.jsonutil import iter_strings, iter_url_values
from app.ingestion.url_parser import ParsedInstagramUrl, extract_urls, parse_url


def links_from_node(node: Any, include_free_text: bool = True) -> list[ParsedInstagramUrl]:
    """All distinct Instagram URLs under URL-like keys and (optionally) inside any text."""
    found: dict[str, ParsedInstagramUrl] = {}
    for value in iter_url_values(node):
        parsed = parse_url(value)
        if parsed:
            found.setdefault(parsed.identity_key, parsed)
    if include_free_text:
        for text in iter_strings(node):
            if "instagr" in text.lower():
                for parsed in extract_urls(text):
                    found.setdefault(parsed.identity_key, parsed)
    return list(found.values())
