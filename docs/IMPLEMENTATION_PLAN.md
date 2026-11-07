# Implementation Plan

Living document. Updated as each phase progresses. Status values: ✅ done (tested) · 🟡 partial · ⏳ not started.

## Repository state at start of Phase 1 (2026-11-07)

The repository contained only `PRD.md`. No code, no fixtures, and no real Instagram export.
Everything below was built from scratch.

**2026-11-07 update:** first real export (JSON, ~1.1 GB, 1,430 files) received and verified.
Saved posts and collections used a different structure than assumed; parsers were rewritten for
the real `label_values` format (old shape kept as fallback). See ADR-013.

## Phase 1 — Foundation & Historical Import (P0)

| # | Deliverable | Status | Notes |
|---|---|---|---|
| 1 | Python backend (FastAPI, Pydantic v2, SQLAlchemy 2, Alembic) | ✅ | `backend/app` |
| 2 | PostgreSQL | ✅ | `pgvector/pgvector:pg16` image (extension not yet enabled; Phase 6) |
| 3 | Docker Compose (postgres, redis, api) | ✅ | Host ports configurable via `INSTABRAIN_PG_HOST_PORT` / `INSTABRAIN_REDIS_HOST_PORT` |
| 4 | Database models + initial migration | ✅ | users, content_items, content_sources, import_batches, import_files, raw_import_records, processing_jobs, audit_logs |
| 5 | Safe ZIP inspection | ✅ | No extraction to disk; traversal/symlink/encrypted/zip-bomb/size/CRC checks |
| 6 | Saved-post + collection parsing | ✅ | Verified on real export: 103 saved posts, 9 collections / 61 items, creator + caption + hashtags |
| 7 | Group/direct message parsing | ✅ | Verified: 591 threads (inbox + message_requests), 5 groups; privacy filter keeps only link-bearing messages |
| 8 | Shared Instagram URL extraction | ✅ | From URL-typed fields and free text |
| 9 | URL normalisation | ✅ | Case-sensitive shortcode identity; `/p` `/reel` `/reels` `/tv`, `instagr.am`, `/{user}/reel/…`, tracking params |
| 10 | Deduplication | ✅ | By shortcode; one `content_item`, many `content_sources` |
| 11 | Import reporting | ✅ | CLI text/JSON + persisted `import_batches.report` + API |
| 12 | Tests with representative fixtures | ✅ | 103 tests (SQLite + PostgreSQL); `real_shape_2026_11` fixture mirrors the real structure; real ZIP smoke test via `INSTABRAIN_REAL_EXPORT` |
| — | `python -m scripts.inspect_instagram_export` | ✅ | |
| — | `python -m scripts.import_instagram_export` (resumable) | ✅ | `--resume <id> [--force]` |
| — | Celery worker + beat sweep, fencing token, retries with backoff | ✅ | `app/workers/`; Compose services `worker`, `beat`, `migrate` |
| — | API: `POST/GET /api/v1/imports`, `GET /imports/{id}`, `POST /imports/{id}/resume`, `/health`, `/ready` | ✅ | API-key protected |

### Definition of done — Phase 1

| Criterion | Status |
|---|---|
| A real Instagram export can be uploaded | ✅ Real 1.1 GB export imported in ~9 s, 0 errors/warnings |
| Saved-post references are extracted | ✅ 103 / 103 |
| Group chat Instagram URLs are extracted | ✅ 1,721 group + 944 direct references |
| Source metadata is preserved | ✅ |
| Duplicate URLs are consolidated | ✅ |
| Import can resume after failure | ✅ (tested by simulated crash) |
| Import results are available in a report | ✅ |

Real-export result: 2,829 content references → **2,579 unique content items** (2,016 reels, 563
posts), 250 duplicates consolidated, 708 story links and 206 profile links reported but not imported.

Phase 1 parsing is verified, and the durable worker is done (ADR-016/017). **Phase 1 is complete.**

### Remaining Phase 1 tasks

1. ~~Run on a real export~~ ✅ done 2026-11-07.
2. ~~Verify collections structure~~ ✅ (was wrong; fixed).
3. Decide on HTML-format exports (currently: require JSON).
4. ~~Durable worker (Celery + Redis)~~ ✅ done 2026-11-07. Tested: unit/eager task tests and a real
   SIGKILL-mid-import recovery in Docker.
5. Upload streaming hash + duplicate-archive detection (warn if same SHA-256 already imported).

### MVP reliability criteria (PRD §18) covered so far
| Criterion | Status |
|---|---|
| Background jobs survive API restarts | ✅ Jobs run on the worker; state in PostgreSQL; queue in Redis (AOF) |
| Failed jobs can be retried | ✅ Automatic (transient), sweep (crashed), `POST /imports/{id}/resume` (manual) |
| Partial results are retained | ✅ Per-file commits |
| Errors are logged | ✅ Structured logs (`INSTABRAIN_LOG_JSON=true` for JSON) |

## Phase 2 — Content Extraction Pipeline 🟡 (started 2026-11-07)

| Step | Status | Notes |
|---|---|---|
| LLM provider interface + Copilot SDK provider | ✅ | ADR-018 |
| Category-aware validated schema (16 categories, evidence per entity) | ✅ | `processing/extraction/schema.py` |
| Caption classification + extraction | 🟡 | Trial: 10/10 succeeded (2 needed a retry), ~15 s/item, `claude-haiku-5.5` |
| Processing queue: one job per item, stages, retries, sweep | ✅ | ADR-021; API `/processing/enqueue`, `/processing/jobs`, `/stats`, `/content/{id}/reprocess`, `/content/{id}/job` |
| Process all ~2,579 items | ⏳ | Pending decision on AI account/cost; `POST /processing/enqueue {"limit": N}` |
| Manual media upload (API + CLI) | ✅ | ADR-020 |
| Image OCR (Tesseract eng+hin) | ✅ | Real-engine test: slide text read at 0.97 confidence |
| Video transcription (faster-whisper small) + frame OCR | ✅ | Real-engine test + Docker media-worker e2e |
| Extraction from caption + on-screen text + transcript | ✅ | e2e: caption without titles + video → 3 books with authors, all explicit |
| Vision-model image understanding | ⏳ | OCR covers text; non-text images (food, places) still need it |
| Public metadata retrieval | ⏳ | |

**Trial findings (10 items from 5 collections):** no fabricated entities. Thin captions were
correctly flagged `information_sufficient=false` with confidence 0.4. Explicit data extracted well
(a book with author, 7 venues with sectors, 5 local products). The model added generic "tips" not
in the post, so prompt v2 forbids that (**v2 not yet re-validated on live model**). Main lesson:
most list posts ("5 books…", "new cafés…") keep the actual list in images/video, so OCR and
transcription are essential, not optional.
## Phase 3 — Notion Integration ⏳
## Phase 4 — Continuous Capture ⏳
## Phase 5 — Dashboard ⏳
## Phase 6 — Semantic Search & Assistant ⏳
## Phase 7 — Advanced Features ⏳

See PRD §17 for scope. Do not start until Phase 1 is verified against a real export.
