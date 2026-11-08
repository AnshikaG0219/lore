# Kickoff prompt for Claude Code / OpenCode

Paste this as your first message, (`docs/SYSTEM_DESIGN.md` is already in this repo).

---

Read `docs/SYSTEM_DESIGN.md` in full. You are the senior engineer implementing this project (working name **Lore**).

**Scope for this session: Phase 1 only.** That means Historical Instagram Export Inspection and Import Foundation. Do not start AI processing, Notion integration, or frontend work.

**Before writing code:**
1. Inspect the repository and every existing file. Reuse working code, and don't overwrite anything you don't understand.
2. Compare what exists against the design doc.
3. Write a short implementation plan to `docs/IMPLEMENTATION_PLAN.md`.
4. List missing information and technical risks.

**Then build, in small testable steps:**
- Docker Compose with PostgreSQL (with pgvector) and Redis
- FastAPI app skeleton, centralised config from env vars, structured logging
- SQLAlchemy 2.x models and Alembic migrations for `content_items`, `content_sources`, `import_batches`, `processing_jobs`, `audit_logs`
- A safe ZIP inspector: path-traversal guard, size limits, no executing files
- Parsers for saved posts, collections, DMs and group chats. Build them against **real export fixtures** in `tests/fixtures/`. If none exist yet, build the inspection framework and document in `docs/KNOWN_LIMITATIONS.md` what is waiting on real data
- URL extraction for `/p/`, `/reel/`, `/reels/` and `/tv/`, including tracking parameters, normalised to a canonical shortcode form
- Deduplication: one content item, many source rows
- Resumable import with an import report

**Deliverables:**
```bash
python -m scripts.inspect_instagram_export /path/to/export.zip
python -m scripts.import_instagram_export  /path/to/export.zip
```
The inspector reports files found, saved posts, collections, conversations, URLs extracted, duplicates, unrecognised files, and parse errors.

**Rules:** use type hints and Pydantic validation, put tests next to the code, and keep external services behind interfaces. Never hard-code secrets. Never silently drop a URL. Don't invent Instagram export schemas or API capabilities, and don't use scraping or browser automation. Keep `docs/DECISIONS.md` and `docs/KNOWN_LIMITATIONS.md` updated.

**When you finish, report:** files created or changed, the architecture implemented, features completed, tests run with results, known limitations, the exact commands to run the app, and the next recommended task.
