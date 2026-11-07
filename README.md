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

## API

```bash
docker compose up -d --build          # postgres, redis, api (runs migrations)
curl localhost:8000/health
curl -H "X-API-Key: $KEY" -F file=@export.zip localhost:8000/api/v1/imports
curl -H "X-API-Key: $KEY" localhost:8000/api/v1/imports/<id>
curl -X POST -H "X-API-Key: $KEY" localhost:8000/api/v1/imports/<id>/resume
```

Local without Docker: `cd backend && .venv/bin/uvicorn app.main:app --reload`.

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
