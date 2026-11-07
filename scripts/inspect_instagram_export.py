"""Inspect an Instagram export (ZIP or extracted folder) without touching the database.

Usage:
    python -m scripts.inspect_instagram_export /path/to/instagram-export.zip [--json]
"""

from __future__ import annotations

import argparse
import json
import sys

from scripts import _bootstrap  # noqa: F401  (must run before app imports)

# isort: split
from app.core.logging import configure_logging  # noqa: E402
from app.ingestion.instagram.archive import ArchiveError, ExportArchive  # noqa: E402
from app.ingestion.instagram.inspector import format_report, inspect_export  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("path", help="Instagram export ZIP or extracted directory")
    parser.add_argument("--json", action="store_true", help="Emit the full report as JSON")
    parser.add_argument("--max-items", type=int, default=20, help="Items listed per section")
    args = parser.parse_args(argv)

    configure_logging("WARNING")
    try:
        with ExportArchive(args.path) as archive:
            report = inspect_export(archive)
    except ArchiveError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    if args.json:
        print(json.dumps(report.to_dict(), indent=2, default=str, ensure_ascii=False))
    else:
        print(format_report(report, max_items=args.max_items))
    return 1 if report.errors else 0


if __name__ == "__main__":
    sys.exit(main())
