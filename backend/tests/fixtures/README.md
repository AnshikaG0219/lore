# Test fixtures

## `synthetic_export_v1/` — SYNTHETIC (legacy-shape fallback tests)

Hand-written, **not** taken from a real Instagram export. It mirrors the commonly reported Meta
JSON export layout (`your_instagram_activity/...`) and deliberately includes edge cases:

- Meta mojibake (`caf\u00c3\u00a9` → `café`)
- the same media saved as `/reel/` and `/p/`, in a collection and shared in a group chat
- case-sensitive shortcodes (`CaseSens01` vs `casesens01`)
- `/tv/` URL, tracking params (`igsh`, `utm_source`), a `share/reel` link, a profile URL
- a saved record without a URL (deleted content)
- a thread split across `message_1.json` / `message_2.json`
- a malformed JSON file, an HTML file, a media file, unrelated JSON files
- a private non-link message that must never be persisted

## `real_shape_2026_11/` — REAL STRUCTURE, FAKE DATA

Keys, nesting, value types and timestamp formats copied exactly from a real export (2026-11-06).
All names, captions, shortcodes and IDs are invented. Includes: label_values saved posts (incl. the
duplicated-Caption/no-Owner variant), nested collections (incl. an empty one and a trailing-space
name), a group thread with reel/story/`_u` profile shares, and a `message_requests` thread.

## Running against your own export (not committed)

```bash
INSTABRAIN_REAL_EXPORT=/path/to/export.zip .venv/bin/pytest tests/integration/test_real_exports.py
```

## `real/` — anonymised real exports (optional)

Put anonymised excerpts from real exports here, keeping the original directory structure.
Replace names and message text, but keep keys, nesting, timestamp formats and URL shapes exactly.
