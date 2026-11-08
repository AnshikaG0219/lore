"""Attach screenshots/videos to an imported post, run OCR + transcription, optionally re-extract.

Usage:
    python -m scripts.add_media <instagram-url|shortcode|content-id> file1.jpg file2.mp4 ...
    python -m scripts.add_media <SHORTCODE> reel.mp4 --process            # run now, in-process
    LORE_COPILOT_GITHUB_TOKEN=... python -m scripts.add_media <SHORTCODE> reel.mp4 \\
        --process --extract                                              # then re-extract

Without --process the files are queued for the media worker.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import uuid
from pathlib import Path

from scripts import _bootstrap  # noqa: F401  (must run before app imports)

# isort: split
from sqlalchemy import or_, select  # noqa: E402

from app.core.config import get_settings  # noqa: E402
from app.core.logging import configure_logging  # noqa: E402
from app.db.models import ContentItem  # noqa: E402
from app.db.session import get_sessionmaker  # noqa: E402
from app.ingestion.url_parser import parse_url  # noqa: E402
from app.processing.media.engines import build_engines, build_store  # noqa: E402
from app.processing.media.pipeline import attach_media, media_text_for, process_asset  # noqa: E402
from app.storage.media import MediaRejectedError  # noqa: E402


def find_item(session, ref: str) -> ContentItem | None:
    try:
        return session.get(ContentItem, uuid.UUID(ref))
    except ValueError:
        pass
    parsed = parse_url(ref)
    shortcode = parsed.shortcode if parsed else ref.strip("/")
    return session.scalar(select(ContentItem).where(or_(
        ContentItem.platform_content_id == shortcode,
        ContentItem.canonical_url == (parsed.canonical_url if parsed else ref))))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("item", help="Instagram URL, shortcode or content UUID")
    parser.add_argument("files", nargs="+", type=Path)
    parser.add_argument("--process", action="store_true", help="Process now instead of queueing")
    parser.add_argument("--extract", action="store_true",
                        help="After processing, re-run AI extraction (needs Copilot token)")
    args = parser.parse_args(argv)

    settings = get_settings()
    configure_logging("WARNING", settings.log_json)
    store = build_store(settings)
    with get_sessionmaker()() as session:
        item = find_item(session, args.item)
        if item is None:
            print(f"error: no imported content matches {args.item!r}", file=sys.stderr)
            return 2
        assets = []
        for f in args.files:
            try:
                with f.open("rb") as fh:
                    asset, created = attach_media(session, store, item.id, fh, f.name)
            except (OSError, MediaRejectedError) as exc:
                print(f"skip {f}: {exc}", file=sys.stderr)
                continue
            print(f"{'added' if created else 'already attached'}: {f.name} → "
                  f"{asset.asset_type} {asset.id}", file=sys.stderr)
            assets.append(asset.id)

        if not args.process:
            from app.workers.dispatch import CeleryImportDispatcher

            for a in assets:
                CeleryImportDispatcher(settings).enqueue_media(a)
            print(json.dumps({"content_id": str(item.id), "queued": [str(a) for a in assets]}))
            return 0

        engines = build_engines(settings)
        for a in assets:
            r = process_asset(session, a, engines, store)
            print(f"processed {a}: {r['status']} {r.get('error', '')}", file=sys.stderr)
        text = media_text_for(session, item.id)
        out = {"content_id": str(item.id), "url": item.canonical_url,
               "on_screen_text": text.on_screen_text, "transcript": text.transcript,
               "transcript_language": text.transcript_language}

        if args.extract:
            from app.processing.ai.copilot_sdk import CopilotSdkProvider
            from app.processing.extraction.caption import JOB_TYPE, process_item

            token = os.environ.get("LORE_COPILOT_GITHUB_TOKEN", "")

            async def run():
                provider = CopilotSdkProvider(token, settings.copilot_model)
                try:
                    return await process_item(session, item, provider)
                finally:
                    await provider.close()

            out["extraction_status"] = asyncio.run(run())
            session.refresh(item)
            out["extraction"] = (item.extracted_data or {}).get(JOB_TYPE)
    print(json.dumps(out, indent=1, ensure_ascii=False, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
