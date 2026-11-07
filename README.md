# InstaBrain

Personal content intelligence for Instagram. See [`PRD.md`](PRD.md) for the full specification.

**Current status: Phase 1 (historical export inspection and import).** See
[`docs/IMPLEMENTATION_PLAN.md`](docs/IMPLEMENTATION_PLAN.md),
[`docs/DECISIONS.md`](docs/DECISIONS.md) and
[`docs/KNOWN_LIMITATIONS.md`](docs/KNOWN_LIMITATIONS.md).

## Get your Instagram export

Instagram → Accounts Center → *Your information and permissions* → *Download your information*.
Choose **JSON** format (HTML is not supported) and include at least *Saved* and *Messages*.

## Setup

```bash
cp .env.example .env              # set INSTABRAIN_API_KEY
cd backend
uv venv -p 3.12 .venv             # or: python3.12 -m venv .venv
uv pip install -p .venv -r requirements-dev.txt
cd ..
docker compose up -d postgres redis
(cd backend && .venv/bin/alembic upgrade head)
```

If ports 5432/6379 are taken, run
`INSTABRAIN_PG_HOST_PORT=5433 INSTABRAIN_REDIS_HOST_PORT=6380 docker compose up -d postgres redis`
and set `INSTABRAIN_DATABASE_URL` to use port 5433.

## Inspect an export (no database needed)

```bash
backend/.venv/bin/python -m scripts.inspect_instagram_export /path/to/instagram-export.zip
backend/.venv/bin/python -m scripts.inspect_instagram_export /path/to/export.zip --json > report.json
```

Accepts a ZIP or an extracted folder. Exit code 0 means clean, 1 means parsing errors were reported,
and 2 means the archive is invalid.

## Import into PostgreSQL

```bash
backend/.venv/bin/python -m scripts.import_instagram_export /path/to/instagram-export.zip
backend/.venv/bin/python -m scripts.import_instagram_export --resume <batch-id> [--force]
```

Re-importing is safe: content and source associations are idempotent.

## API + background worker

```bash
docker compose up -d --build          # postgres, redis, migrate, api, worker, beat
curl localhost:8000/ready             # checks database and queue
curl localhost:8000/health
curl -H "X-API-Key: $KEY" -F file=@export.zip localhost:8000/api/v1/imports
curl -H "X-API-Key: $KEY" localhost:8000/api/v1/imports/<id>
curl -X POST -H "X-API-Key: $KEY" localhost:8000/api/v1/imports/<id>/resume
```

Uploads return `202` immediately and the import runs on the Celery **worker**. If the worker or
API crashes, the **beat** sweep resumes the import from its last committed file once its heartbeat
is stale (default 5 min). See `docs/DECISIONS.md` ADR-016/017.

Local without Docker (Postgres + Redis still from Compose):

```bash
cd backend
.venv/bin/uvicorn app.main:app --reload
.venv/bin/celery -A app.workers.celery_app worker -Q instabrain.imports --loglevel=INFO
.venv/bin/celery -A app.workers.celery_app beat --loglevel=INFO
```

Set `INSTABRAIN_TASK_BACKEND=inline` to run imports inside the API process instead (no Redis/worker;
for development only).

CLI: `python -m scripts.import_instagram_export export.zip --enqueue` hands the import to the worker.
Without `--enqueue` it runs in the foreground.

## Tests

```bash
cd backend
.venv/bin/pytest                          # SQLite only
INSTABRAIN_TEST_DATABASE_URL=postgresql+psycopg://instabrain:instabrain@localhost:5432/instabrain_test \
  .venv/bin/pytest                        # also runs DB tests on PostgreSQL
.venv/bin/ruff check . ../scripts
```

## Layout

```
backend/app/
  api/v1/            imports.py, system.py
  core/              config, logging, security (API key)
  db/                models, enums, repositories, session
  ingestion/
    url_parser.py    URL detection, canonicalisation, identity
    deduplication.py
    importer.py      resumable DB import (per-file checkpoints)
    adapters/        adapter contract + Instagram export adapter
    instagram/       archive (safe ZIP), detect, jsonutil, reader, inspector
      parsers/       saved_posts, collections, messages, shared_links, metadata
backend/alembic/     migrations
backend/tests/       unit/, integration/, fixtures/ (see fixtures/README.md)
scripts/             inspect_instagram_export.py, import_instagram_export.py
```

## Verified export format

Tested against a real JSON export from 2026-11. See `docs/KNOWN_LIMITATIONS.md`. To check your
own export without committing it:
`cd backend && INSTABRAIN_REAL_EXPORT=/path/to/export.zip .venv/bin/pytest tests/integration/test_real_exports.py`

## Caption extraction (Phase 2, trial)

```bash
# preview exactly what would be sent (no model call)
backend/.venv/bin/python -m scripts.extract_captions --limit 5 --dry-run
# run with a GitHub account that has Copilot (token read from env only, never stored)
INSTABRAIN_COPILOT_GITHUB_TOKEN=$(gh auth token -h github.com -u <account>) \
  backend/.venv/bin/python -m scripts.extract_captions --limit 10 --model claude-haiku-5.5
```

## Media: OCR + transcription

Your Instagram export doesn't contain the images/videos of saved posts, so attach them yourself:

```bash
# screenshots of a carousel and/or a screen recording of a Reel
backend/.venv/bin/python -m scripts.add_media https://www.instagram.com/p/DaDFMBdkjr0/ \
    slide1.jpg slide2.jpg --process
# …and re-run extraction with the new text
INSTABRAIN_COPILOT_GITHUB_TOKEN=$(gh auth token -h github.com -u <account>) \
  backend/.venv/bin/python -m scripts.add_media DaDFMBdkjr0 reel.mp4 --process --extract
```

Or via the API: `POST /api/v1/content/{id}/media` (multipart `files`) and
`GET /api/v1/content/{id}/media`. Requires `ffmpeg` and `tesseract`
(`brew install ffmpeg tesseract tesseract-lang`). The Docker image includes both.

## Processing queue (one job per post)

Each post/Reel has exactly one processing job that moves through
`QUEUED → VALIDATING → EXTRACTING_MEDIA/TRANSCRIBING → STRUCTURING → COMPLETED | NEEDS_MANUAL_INPUT | FAILED`,
with automatic retries and crash recovery.

```bash
# start a pipeline worker (host; token only in this process's environment)
cd backend && INSTABRAIN_COPILOT_GITHUB_TOKEN=$(gh auth token -h github.com -u <account>) \
  INSTABRAIN_DATABASE_URL=postgresql+psycopg://instabrain:instabrain@localhost:5433/instabrain \
  INSTABRAIN_REDIS_URL=redis://localhost:6380/0 \
  .venv/bin/celery -A app.workers.celery_app worker -Q instabrain.pipeline --concurrency=1

# queue work (explicit limits = cost control)
curl -H "X-API-Key: $KEY" -H 'Content-Type: application/json' \
     -d '{"limit": 20, "collection": "ladakh"}' localhost:8000/api/v1/processing/enqueue
curl -H "X-API-Key: $KEY" localhost:8000/api/v1/stats
curl -H "X-API-Key: $KEY" "localhost:8000/api/v1/processing/jobs?status_filter=needs_manual_input"
curl -X POST -H "X-API-Key: $KEY" localhost:8000/api/v1/content/<id>/reprocess
```
Or in Docker: `INSTABRAIN_COPILOT_GITHUB_TOKEN=... docker compose --profile pipeline up -d`.
