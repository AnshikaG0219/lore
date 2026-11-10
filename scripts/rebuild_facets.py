"""Recompute content_facets from stored v4 classifications. No AI calls.

Run after extending the gazetteer (new neighbourhoods, destinations…):
    python -m scripts.rebuild_facets
"""

from __future__ import annotations

import sys

from scripts import _bootstrap  # noqa: F401  (must run before app imports)

# isort: split
from sqlalchemy import select  # noqa: E402

from app.db.models import ContentItem  # noqa: E402
from app.db.session import get_sessionmaker  # noqa: E402
from app.processing.classification.facets import replace_facets  # noqa: E402
from app.processing.classification.schema import PROMPT_VERSION  # noqa: E402


def main() -> int:
    done = rows = 0
    with get_sessionmaker()() as session:
        for item in session.scalars(select(ContentItem).where(
                ContentItem.last_processed_at.is_not(None))):
            c = (item.extracted_data or {}).get("caption_extraction") or {}
            if (c.get("_meta") or {}).get("prompt_version") != PROMPT_VERSION:
                continue
            rows += replace_facets(session, item.id, c)
            done += 1
        session.commit()
    print(f"rebuilt facets for {done} items ({rows} rows)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
