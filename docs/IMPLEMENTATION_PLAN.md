# Implementation Plan

Living document. Updated as each phase progresses. Status values: ✅ done (tested) · 🟡 partial · ⏳ not started.

## Repository state at start of Phase 1 (2026-11-07)

The repository contained only `PRD.md`. No code, no fixtures, and **no real Instagram export**.
Everything below was built from scratch.

## Phase 1 — Foundation & Historical Import (P0)

| # | Deliverable | Status | Notes |
|---|---|---|---|
| 1 | Python backend (FastAPI, Pydantic v2, SQLAlchemy 2, Alembic) | ✅ | `backend/app` |
| 2 | PostgreSQL | ✅ | `pgvector/pgvector:pg16` image (extension not yet enabled; Phase 6) |
| 3 | Docker Compose (postgres, redis, api) | ✅ | Host ports configurable via `INSTABRAIN_PG_HOST_PORT` / `INSTABRAIN_REDIS_HOST_PORT` |
| 4 | Database models + initial migration | ✅ | users, content_items, content_sources, import_batches, import_files, raw_import_records, processing_jobs, audit_logs |
| 5 | Safe ZIP inspection | ✅ | No extraction to disk; traversal/symlink/encrypted/zip-bomb/size/CRC checks |
| 6 | Saved-post parsing | 🟡 | Works on synthetic fixtures; **awaiting real export to confirm** |
| 7 | Group/direct message parsing | 🟡 | Same; privacy filter keeps only link-bearing messages |
| 8 | Shared Instagram URL extraction | ✅ | From URL-typed fields and free text |
| 9 | URL normalisation | ✅ | Case-sensitive shortcode identity; `/p` `/reel` `/reels` `/tv`, `instagr.am`, `/{user}/reel/…`, tracking params |
| 10 | Deduplication | ✅ | By shortcode; one `content_item`, many `content_sources` |
| 11 | Import reporting | ✅ | CLI text/JSON + persisted `import_batches.report` + API |
| 12 | Tests with representative fixtures | 🟡 | 95 tests (SQLite + PostgreSQL). Fixtures are **synthetic** |
| — | `python -m scripts.inspect_instagram_export` | ✅ | |
| — | `python -m scripts.import_instagram_export` (resumable) | ✅ | `--resume <id> [--force]` |
| — | API: `POST/GET /api/v1/imports`, `GET /imports/{id}`, `POST /imports/{id}/resume`, `/health`, `/ready` | ✅ | API-key protected |

### Definition of done — Phase 1

| Criterion | Status |
|---|---|
| A real Instagram export can be uploaded | 🟡 Upload works; never run against a real export |
| Saved-post references are extracted | 🟡 Synthetic only |
| Group chat Instagram URLs are extracted | 🟡 Synthetic only |
| Source metadata is preserved | ✅ |
| Duplicate URLs are consolidated | ✅ |
| Import can resume after failure | ✅ (tested by simulated crash) |
| Import results are available in a report | ✅ |

**Phase 1 is not complete until it has been run against at least one real export.** See
`docs/KNOWN_LIMITATIONS.md` → "Awaiting real fixtures".

### Remaining Phase 1 tasks

1. **Run `inspect_instagram_export` on a real export** (JSON format) and add anonymised excerpts
   to `backend/tests/fixtures/real/`. Adjust parsers only where reality differs.
2. Verify `saved_collections.json` header/item ordering assumption.
3. Decide on HTML-format exports (support vs. require JSON).
4. Move imports from FastAPI `BackgroundTasks` to a durable worker (Celery + Redis) — required
   for "jobs survive API restarts". Today, resume is manual (`/resume` or CLI `--resume --force`).
5. Upload streaming hash + duplicate-archive detection (warn if same SHA-256 already imported).

## Phase 2 — Content Extraction Pipeline ⏳
## Phase 3 — Notion Integration ⏳
## Phase 4 — Continuous Capture ⏳
## Phase 5 — Dashboard ⏳
## Phase 6 — Semantic Search & Assistant ⏳
## Phase 7 — Advanced Features ⏳

See PRD §17 for scope. Do not start until Phase 1 is verified against a real export.
