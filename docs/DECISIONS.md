# Architecture Decision Records

## ADR-001: Modular monolith, Python 3.12+/FastAPI/SQLAlchemy 2/Alembic
Per PRD §10. Local dev venv uses Python 3.13; Docker image uses 3.12.

## ADR-002: Parsers are shape-tolerant, not schema-bound
**Context:** Instagram changes export layouts and key names (e.g. `your_instagram_activity/`
prefix, `string_map_data` vs `label_values`). No real export was available.
**Decision:**
- Files are classified by **path suffix** (independent of the export's root folder), falling back to
  **JSON shape** for unknown files whose path mentions saved/messages/inbox.
- Within a file, the parser picks the **largest list of objects**, not a hard-coded key, and harvests
  URLs from any `href`/`link`/`url`/`uri` key and timestamps from any timestamp-like key.
- Unknown structures yield warnings, never crashes; a parser exception fails only that file.
**Consequence:** Robust to renames, but structural assumptions (e.g. collection header ordering)
must still be verified against real data.

## ADR-003: Content identity = case-sensitive Instagram shortcode
`/p/X`, `/reel/X`, `/reels/X`, `/tv/X` and `/{user}/reel/X` refer to the same media → one
`content_item` with `identity_key = instagram:shortcode:X`. Shortcodes are **case-sensitive**, so
`CaseSens01` ≠ `casesens01`. Non-media URLs (share links, profiles, stories) never get a shortcode
identity, which prevents accidental merging. Share links (`/share/…`, `/s/…`) become content items
keyed by canonical URL, flagged `needs_url_resolution` (resolution needs network; Phase 2).
Profiles/stories/other are counted in the report but do not create content items.
`content_type` upgrades post → tv → reel if the same shortcode is seen with a more specific path.

## ADR-004: Source associations are idempotent via `source_key`
`content_sources.source_key = sha256(identity_key, source_type, source_name, source_reference)` with a
unique constraint. Re-importing the same export, or re-processing a file after a crash, never creates
duplicate associations. Message locators use `conversation_id|timestamp_ms|sender` (not file index)
so they stay stable across re-exports where `message_N.json` numbering shifts.

## ADR-005: Per-file checkpoints for resumability
`import_files` has one row per archive member. Each file is parsed and committed in a single
transaction together with its checkpoint row and batch counters. A crash leaves pending files pending;
`run_import` resumes from them. A heartbeat on `import_batches` prevents two concurrent runs; a stale
heartbeat (>5 min) or `--force` / `allow_takeover` allows takeover.

## ADR-006: Never extract archives to disk
Members are streamed from the ZIP into memory one at a time with a per-member byte cap. Unsafe
members are listed as rejected (and recorded as failed files in the import) rather than read.

## ADR-007: Privacy filter on messages
Only messages containing an Instagram URL produce references or raw records. Other message text is
read in memory to search for links and then discarded; it is never persisted or reported.
`personal_information` and other unrelated JSON files are not even loaded.

## ADR-008: ~~Phase 1 background execution uses FastAPI BackgroundTasks~~ (superseded by ADR-016)

## ADR-009: Enums stored as strings; JSON columns portable
Status enums are `String` columns (no PostgreSQL ENUM types → easier migrations). JSON columns use
`JSON().with_variant(JSONB, "postgresql")` so unit tests run on SQLite while production uses JSONB.
The DB test suite runs on both when `INSTABRAIN_TEST_DATABASE_URL` is set.

## ADR-010: Mojibake repair
Meta JSON exports commonly encode UTF-8 as Latin-1 code points. `repair_text` reverses it only when
the round-trip is lossless, leaving correct text untouched.

## ADR-011: Single default owner user
`content_items.user_id` is NOT NULL and uniqueness is `(user_id, identity_key)`. Phase 1 attaches all
imports to a default `owner@instabrain.local` user. Multi-user ownership requires auth (Phase 4/5).

## ADR-012: API-key auth for admin endpoints in Phase 1
All `/api/v1/*` routes require `X-API-Key` (constant-time compare). If no key is configured the API
returns 503 rather than running open. Session auth for the dashboard comes with Phase 5.

## ADR-013: Parse the verified `label_values` export format first, keep old shapes as fallback
**Context:** The first real export (2026-11) showed saved posts as a top-level list of
`label_values` records and collections as one record per collection with nested `Media` items. The
original assumption (flat `string_map_data` list with header rows) was wrong and lost every
collection name.
**Decision:** `parsers/label_values.py` reads the verified structure (URL, Caption, Hashtags,
Owner.Username). Collections switch to the nested parser when any record has a `Media` section,
otherwise use the legacy flat parser. Captions, hashtags and creator display names are stored on the
source (`content_sources.metadata`) and the caption on `content_items.caption`. The anonymised
fixture `tests/fixtures/real_shape_2026_11/` mirrors the real keys exactly.
**Never invent:** collection items have no add time, so none is recorded.

## ADR-014: Caption precedence
Saved records carry the full caption; DM shares only a `share_text` preview. Saved-source captions
overwrite; message captions fill only when empty. When manual editing arrives (Phase 5), a
`user_edited` guard must stop imports from overwriting user changes.

## ADR-015: Stories and profiles are not content items
Story links (expire after 24h, not retrievable later) and profile links (`/{user}/`, `/_u/{user}`)
are counted in reports and kept in raw records, but don't create `content_items`. This is revisitable
if stories become capturable through an authorised route.

## ADR-016: Celery + Redis worker with a recovery sweep
**Context:** PRD §18 requires that background jobs survive API restarts and that captured data is
never lost if workers crash.
**Decision:**
- The API persists the batch first and only then enqueues `instabrain.imports.run`. If enqueueing
  fails (Redis down) the upload still returns 202 and the batch stays `pending`.
- Celery: `acks_late`, `reject_on_worker_lost`, `prefetch_multiplier=1`, JSON serialisation, no
  result backend (state lives in PostgreSQL). Redis runs with AOF persistence.
- A **sweep** task (Celery beat, plus once at worker start) re-enqueues `running` batches whose
  heartbeat is stale and `pending` batches older than a grace period. This is the main recovery path;
  broker redelivery (visibility timeout of 6h) is only a backstop.
- Retries: transient DB errors use exponential backoff (5s doubling, capped at 10 min, max 8). A
  live runner causes retries every heartbeat interval. Unexpected errors mark the batch `failed`
  (resumable). Duplicate deliveries of finished batches are no-ops.
- `task_backend=inline` runs imports in-process for tests and Redis-less local use.
**Verified:** worker SIGKILLed mid-import of a real 1.1 GB export → after restart the sweep resumed
the remaining 328/666 files, with totals identical to a clean run.

## ADR-017: Fencing token for import runners
A sweep may start a second runner if a slow runner misses its heartbeat. Each claim writes a new
`import_batches.runner_id`. Every commit locks the batch row (`SELECT … FOR UPDATE`) and checks the
token, so a superseded runner fails its next commit with `ClaimLostError` and stops, and counters
cannot be double-counted. Files already committed are skipped (`status != pending`).

## ADR-018: GitHub Copilot SDK as the first LLM provider (trial)
**Context:** The user asked to use their existing Copilot instead of a separate AI provider.
GitHub Models was retired (2026-07-30). The supported route is the GitHub Copilot SDK (GA 2026-06),
which embeds the Copilot agent runtime and bills the token owner's Copilot plan (usage-based).
**Decision:** `app/processing/ai/copilot_sdk.py` implements the `LLMProvider` interface. Every
session runs with **no tools** (`available_tools=[]`), skills, config discovery and custom
instructions disabled, and a permission handler that rejects everything. It is a plain
completion endpoint. JSON validity is enforced by our Pydantic schema plus one corrective retry.
**Account:** the trial run used the user's Adobe-provided Copilot (work account), at the user's
explicit request. Personal data is sent through an employer licence, so check this against company
policy before bulk processing. A personal plan or another provider is a config change.
**Privacy:** only caption, hashtags, creator, content type and the user's own collection names are
sent. Chat titles, senders and message text are never sent (tested).

## ADR-019: Caption extraction stores provenance and is cached by input hash
Results go to `content_items` (title, summary, categories, tags, confidence) and the full validated
output plus `_meta` (provider, model, prompt_version, input_hash, attempts) to
`extracted_data.caption_extraction`. Each attempt writes a `processing_jobs` row. Re-running skips
items whose input hash (which includes prompt_version) is unchanged. `processing_status` is not
advanced yet, because caption extraction is only one stage and media stages remain.

## ADR-020: Media comes from user uploads; OCR and transcription run locally
**Context:** The real export contains **no media for saved posts** (`"media": []`). Its 763 media
files are photos/videos sent in chats. Fetching Reels from Instagram would be scraping (PRD §13/§19).
**Decision:**
- Media is attached by the user (`POST /api/v1/content/{id}/media` or `scripts/add_media.py`).
  Files are content-addressed under `INSTABRAIN_DATA_DIR/media/<content_id>/<sha256>.<ext>`, typed
  by magic bytes (JPEG/PNG/WebP/HEIC, MP4/MOV, M4A/MP3/WAV), size-limited and never executed.
  Re-uploading the same file is a no-op.
- **OCR: Tesseract** (`eng+hin`), on greyscale images upscaled to at least 1000px. Words below
  confidence 40 are dropped as noise.
- **Transcription: faster-whisper** (`small`, CPU int8, VAD to skip music/silence), with language
  detection and timestamped segments. Audio is extracted with ffmpeg to 16 kHz mono WAV and passed
  to Whisper as samples, which bypasses PyAV (faster-whisper 1.2.1 calls a PyAV argument removed in
  newer releases).
- **Video frames:** one frame every 2s (max 45, spread over long videos) is OCR'd. Repeated overlay
  lines are de-duplicated in reading order.
- All local: no media or audio leaves the machine. Only derived text goes to the LLM.
- Each step (OCR per frame, transcript per asset) commits separately and is skipped if present,
  so failures keep partial results and retries never repeat work.
- Media processing runs inside the per-item pipeline job (ADR-021) on queue `instabrain.pipeline`,
  separate from imports. Uploading media re-arms that item's job.
- Extraction prompt v3 labels `caption` / `on_screen_text` / `spoken_transcript` separately and
  prefers on-screen text for names. Verified: speech "Herman Hesse" vs screen "Hermann Hesse"
  resolved to the on-screen spelling.

## ADR-021: Content processing queue — one durable job per content item
**Context:** Each post must be processed exactly once per change, survive crashes, retry transient
failures, and record its stage (PRD §12). The import worker established the patterns.
**Decision:**
- `processing_jobs` is the queue: `UNIQUE(content_id, job_type)`, `job_type = content_pipeline`.
  Enqueueing an active job is a no-op. A finished job is re-armed (same row) only on purpose:
  new media, manual `POST /content/{id}/reprocess`, or a bulk enqueue with `only_new=false`.
  Attempt history moves to `audit_logs` (migration moved the 10 trial rows).
- PostgreSQL holds the state; Celery only delivers "run job X" (`instabrain.pipeline.run`, queue
  `instabrain.pipeline`, rate limit `INSTABRAIN_PIPELINE_RATE_LIMIT`). Lost or duplicate messages
  are harmless. The beat sweep (`instabrain.pipeline.sweep`) re-dispatches queued jobs that were
  never or long ago dispatched, retries that are due, and running jobs with a stale heartbeat.
- `claim` uses the same fencing token + row lock as imports, so only one runner writes a job.
- Stages: VALIDATING → EXTRACTING_MEDIA/TRANSCRIBING (per asset) → STRUCTURING → COMPLETED |
  NEEDS_MANUAL_INPUT | FAILED. Each is idempotent: media steps skip stored results, and
  extraction is skipped when the input hash is unchanged.
- Failures: AI provider errors or invalid output → `retry_wait` with exponential backoff (30s
  doubling, cap 1h), up to `max_attempts` (5) → `FAILED`. No provider configured → `FAILED`
  immediately (permanent until reprocessed). Transient DB errors leave the job to the sweep.
- Outcome rule: if extraction says `information_sufficient=false` and there is no media text, the
  item becomes `NEEDS_MANUAL_INPUT`. It is never silently "completed" with guessed content.
- Cost control: nothing is queued automatically after imports unless
  `INSTABRAIN_AUTO_PROCESS_IMPORTS=true`. Bulk enqueue takes an explicit limit and an optional
  collection or import batch filter.
- The Docker `pipeline-worker` is opt-in (`--profile pipeline`) because it needs a Copilot token.
  The SDK downloads its runtime on first use. The token is passed through the environment, never
  stored in the image or repo.
**Verified:** 7 real items (collection "ladakh") queued via API through a host pipeline worker
(Adobe Copilot): 5 completed, 2 needs_manual_input, 0 retries, ~11s each. Re-enqueue was ignored.

## ADR-022: Interim dashboard as static files served by FastAPI
**Context:** The user wanted to see the data now. The PRD's Next.js frontend is Phase 5.
**Decision:** A dependency-free dashboard (`backend/app/web/static`: HTML + vanilla JS + CSS) at
`/dashboard/`, using only the public JSON API. New read routes: `GET /content` (search, filters,
pagination, sort), `GET /content/{id}` (extraction, sources, job, media text), `GET /facets`,
`GET /places`. Phase 5 can reuse these routes unchanged.
**Security:** API key is entered by the user and kept in `sessionStorage` (cleared when the tab
closes). All untrusted text is rendered with `textContent` (no `innerHTML`, tested), and links
are restricted to same-origin or `https://*.instagram.com`. Strict CSP: `default-src 'self'`, no
inline script or style attributes (bar widths are set via CSSOM), `frame-ancestors 'none'`,
`nosniff`, `no-referrer`. This is suitable for local, single-user use. Real session auth comes
with Phase 5 (PRD §13).
