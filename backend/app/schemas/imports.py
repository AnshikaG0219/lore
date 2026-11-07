from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict


class ImportBatchOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    filename: str
    status: str
    total_files: int
    processed_files: int
    records_found: int
    records_imported: int
    duplicates_found: int
    failures: int
    error_message: str | None
    created_at: datetime
    started_at: datetime | None
    completed_at: datetime | None


class ImportBatchDetail(ImportBatchOut):
    report: dict[str, Any]
