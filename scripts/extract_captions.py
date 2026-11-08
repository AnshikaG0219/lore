"""Classify and extract structured knowledge from captions (Phase 2, step 1).

Usage:
    LORE_COPILOT_GITHUB_TOKEN=$(gh auth token -u <account>) \\
      python -m scripts.extract_captions --limit 10 [--model claude-haiku-5.5]
    python -m scripts.extract_captions --collections books,ladakh --per-collection 2
    python -m scripts.extract_captions --limit 3 --dry-run     # show prompts, call nothing

The token is read from the environment only and never stored or logged.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys

from scripts import _bootstrap  # noqa: F401  (must run before app imports)

# isort: split
from sqlalchemy import select  # noqa: E402

from app.core.config import get_settings  # noqa: E402
from app.core.logging import configure_logging  # noqa: E402
from app.db.models import ContentItem, ContentSource  # noqa: E402
from app.db.session import get_sessionmaker  # noqa: E402
from app.processing.extraction.caption import (  # noqa: E402
    JOB_TYPE,
    SYSTEM_PROMPT,
    build_input,
    process_item,
)


def _select(session, args) -> list[ContentItem]:
    base = select(ContentItem).where(ContentItem.caption.is_not(None), ContentItem.caption != "")
    if args.collections:
        items: list[ContentItem] = []
        for name in [c.strip() for c in args.collections.split(",") if c.strip()]:
            q = (base.join(ContentSource, ContentSource.content_id == ContentItem.id)
                 .where(ContentSource.source_type == "saved_collection",
                        ContentSource.source_name == name)
                 .order_by(ContentItem.canonical_url).limit(args.per_collection))
            items += [i for i in session.scalars(q) if i not in items]
        return items
    q = base.order_by(ContentItem.first_captured_at.desc().nulls_last()).limit(args.limit)
    items = list(session.scalars(q))
    if not args.force:
        items = [i for i in items if JOB_TYPE not in (i.extracted_data or {})]
    return items


async def _run(args) -> int:
    factory = get_sessionmaker()
    with factory() as session:
        items = _select(session, args)
        print(f"Selected {len(items)} item(s)", file=sys.stderr)
        if args.dry_run:
            print("=== SYSTEM PROMPT ===\n" + SYSTEM_PROMPT)
            for item in items:
                inp = build_input(session, item)
                print(f"\n=== {item.canonical_url} ===\n{inp.prompt() if inp else '(no caption)'}")
            return 0

        token = os.environ.get("LORE_COPILOT_GITHUB_TOKEN", "")
        from app.processing.ai.copilot_sdk import CopilotSdkProvider

        provider = CopilotSdkProvider(token, args.model)
        results = []
        try:
            for n, item in enumerate(items, 1):
                r = await process_item(session, item, provider, force=args.force)
                r["url"] = item.canonical_url
                results.append(r)
                print(f"[{n}/{len(items)}] {r['status']:9} {item.canonical_url} "
                      f"{r.get('category') or r.get('reason') or r.get('error', '')}",
                      file=sys.stderr)
        finally:
            await provider.close()
    summary = {s: sum(1 for r in results if r["status"] == s)
               for s in ("completed", "failed", "skipped")}
    print(json.dumps({"summary": summary, "results": results}, indent=1, ensure_ascii=False))
    return 0 if not summary["failed"] else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--limit", type=int, default=10)
    parser.add_argument("--collections", help="Comma-separated collection names to sample from")
    parser.add_argument("--per-collection", type=int, default=2)
    parser.add_argument("--model", default="claude-haiku-5.5")
    parser.add_argument("--force", action="store_true", help="Re-extract even if up to date")
    parser.add_argument("--dry-run", action="store_true", help="Print prompts; call no model")
    args = parser.parse_args(argv)
    settings = get_settings()
    configure_logging("WARNING" if not args.dry_run else "ERROR", settings.log_json)
    return asyncio.run(_run(args))


if __name__ == "__main__":
    sys.exit(main())
