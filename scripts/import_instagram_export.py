"""Import an Instagram export into PostgreSQL (resumable).

Usage:
    python -m scripts.import_instagram_export /path/to/export.zip
    python -m scripts.import_instagram_export --resume <batch-id>
    python -m scripts.import_instagram_export /path/to/export.zip --enqueue   # run on the worker

Without --enqueue the import runs in this process (no Redis/worker needed).
"""

from __future__ import annotations

import argparse
import json
import sys
import uuid
from pathlib import Path

from scripts import _bootstrap  # noqa: F401  (must run before app imports)

# isort: split
from app.core.config import get_settings  # noqa: E402
from app.core.logging import configure_logging  # noqa: E402
from app.db.session import get_sessionmaker  # noqa: E402
from app.ingestion.importer import (  # noqa: E402
    ImportInProgressError,
    create_batch,
    limits_from_settings,
    run_import,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("path", nargs="?", help="Instagram export ZIP or extracted directory")
    group.add_argument("--resume", metavar="BATCH_ID", help="Resume an interrupted import")
    parser.add_argument("--force", action="store_true",
                        help="Take over a batch still marked running (e.g. after a crash)")
    parser.add_argument("--enqueue", action="store_true",
                        help="Hand the import to the background worker and return immediately")
    args = parser.parse_args(argv)

    settings = get_settings()
    configure_logging(settings.log_level, settings.log_json)
    factory = get_sessionmaker()
    limits = limits_from_settings(settings)

    if args.resume:
        batch_id = uuid.UUID(args.resume)
    else:
        path = Path(args.path).expanduser().resolve()
        if not path.exists():
            print(f"error: {path} not found", file=sys.stderr)
            return 2
        with factory() as session:
            batch_id = create_batch(session, path).id
        print(f"Created import batch {batch_id}", file=sys.stderr)

    if args.enqueue:
        from app.workers.dispatch import CeleryImportDispatcher, DispatchError

        try:
            CeleryImportDispatcher(settings).enqueue_import(batch_id)
        except DispatchError as exc:
            print(f"warning: could not reach the queue ({exc}); the worker's periodic "
                  "sweep will pick the batch up once Redis is available", file=sys.stderr)
        print(json.dumps({"batch_id": str(batch_id), "status": "queued"}))
        return 0

    try:
        report = run_import(factory, batch_id, limits, allow_takeover=args.force)
    except ImportInProgressError as exc:
        print(f"error: {exc} (use --force if the previous run crashed)", file=sys.stderr)
        return 3
    print(json.dumps(report, indent=2, default=str, ensure_ascii=False))
    return 0 if report["status"] == "completed" else 1


if __name__ == "__main__":
    sys.exit(main())
