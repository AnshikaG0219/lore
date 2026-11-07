"""Ingestion adapter contract.

Every source (Instagram export, manual capture, future Telegram bot…) yields the same
``ContentReference`` objects, so deduplication and persistence are source-agnostic.
"""

from collections.abc import Iterator
from typing import Protocol

from app.ingestion.instagram.models import ContentReference


class IngestionAdapter(Protocol):
    name: str

    def references(self) -> Iterator[ContentReference]: ...
