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

## ADR-008: Phase 1 background execution uses FastAPI BackgroundTasks
**Trade-off:** Simple, no extra service, but a job is lost if the API process dies mid-import (the
batch stays resumable via `/resume`). Celery + Redis worker is planned before Phase 2 so that
"background jobs survive API restarts" (PRD §18) is met. Redis is already in Compose.

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
