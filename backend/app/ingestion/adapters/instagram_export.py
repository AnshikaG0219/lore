from collections.abc import Iterator

from app.ingestion.instagram.archive import ExportArchive
from app.ingestion.instagram.models import ContentReference
from app.ingestion.instagram.reader import iter_parse


class InstagramExportAdapter:
    """Yields references from an export. The DB importer uses the per-file reader directly
    so it can checkpoint; this adapter is the simple, source-agnostic view."""

    name = "instagram_export"

    def __init__(self, archive: ExportArchive) -> None:
        self.archive = archive

    def references(self) -> Iterator[ContentReference]:
        for result in iter_parse(self.archive):
            yield from result.references
