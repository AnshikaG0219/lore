"""In-memory deduplication of content references by content identity."""

from __future__ import annotations

from collections import OrderedDict
from collections.abc import Iterable
from dataclasses import dataclass, field

from app.ingestion.instagram.models import ContentReference
from app.ingestion.url_parser import ParsedInstagramUrl


@dataclass(slots=True)
class ContentGroup:
    identity_key: str
    url: ParsedInstagramUrl
    references: list[ContentReference] = field(default_factory=list)

    @property
    def source_count(self) -> int:
        return len({r.source_key for r in self.references})


def group_by_identity(references: Iterable[ContentReference]) -> OrderedDict[str, ContentGroup]:
    groups: OrderedDict[str, ContentGroup] = OrderedDict()
    for ref in references:
        key = ref.url.identity_key
        group = groups.get(key)
        if group is None:
            group = groups[key] = ContentGroup(identity_key=key, url=ref.url)
        group.references.append(ref)
    return groups


def merge_content_type(existing: str | None, incoming: str) -> str:
    """Prefer the most specific type: a reel seen anywhere is a reel."""
    rank = {"reel": 3, "tv": 2, "post": 1}
    if existing is None:
        return incoming
    return incoming if rank.get(incoming, 0) > rank.get(existing, 0) else existing
