# Known Limitations

## Awaiting real fixtures (highest priority)

No real Instagram export was available during Phase 1. The test fixtures in
`backend/tests/fixtures/synthetic_export_v1/` are **synthetic**, modelled on the structure commonly
seen in Meta "Download your information" JSON exports. They are not authoritative. Specifically
unverified:

- `saved_posts.json`: assumed `{<list>: [{title: creator, string_map_data: {"Saved on": {href, timestamp}}}]}`.
- `saved_collections.json`: assumed a **flat ordered list** where a record with a `Name` value and no
  URL starts a collection and the following records with URLs belong to it. If real exports nest
  items differently, `parsers/collections.py` must change.
- `messages/inbox/<thread>/message_N.json`: assumed `participants`, `messages[].sender_name`,
  `timestamp_ms`, `content`, `share.link`, `share.original_content_owner`, `title`, `thread_path`.
- Folder names other than `inbox` (e.g. `message_requests`, archived threads) are classified by the
  generic `messages/<folder>/<thread>/message_N.json` rule; not verified.
- Group vs direct detection uses `participants > 2`. A group chat that has shrunk to 2 people will be
  classified as direct.

To validate: run `python -m scripts.inspect_instagram_export <real.zip> --json` and compare.
Anonymised real excerpts should go in `backend/tests/fixtures/real/`.

## Export formats
- **HTML exports are not parsed.** They are inventoried and reported as unsupported. Request the
  export in **JSON** format from Accounts Center.
- Likes, comments, story interactions and other activity files are intentionally not imported (not in
  PRD scope).

## URLs
- `instagram.com/share/...` and `/s/...` links need a network redirect to obtain a shortcode. They are
  stored as content items with `needs_url_resolution` and keyed by URL, so one may later turn out to
  be a duplicate of a shortcode item. Resolution and merging are Phase 2.
- Profile, story and other Instagram URLs are counted in reports but do not create content items.
- Non-Instagram URLs in messages are ignored.

## Platform
- Instagram provides no official API for reading saved collections or DMs. Historical data comes only
  from user-provided exports. No scraping, cookies or credentials are used.
- Deleted/unavailable posts may appear as saved records without a URL. They are kept as raw records
  (`resolved = false`) and reported, never silently dropped.

## Operational
- Imports run in FastAPI `BackgroundTasks` (ADR-008). If the API process stops mid-import, the batch
  stays `running` until resumed (`POST /api/v1/imports/{id}/resume` after the 5-minute heartbeat
  expires, or CLI `--resume <id> --force`).
- Single-worker import per batch; concurrent imports of different batches that share content rely on
  DB unique constraints and may fail a file on a race (the file is marked failed and can be resumed).
- Uploaded archives are kept in `INSTABRAIN_DATA_DIR/uploads`; no retention/cleanup policy yet.
- pgvector extension is not enabled yet (Phase 6). `media_assets`, `transcripts`, `content_chunks`
  tables are deferred to the phases that use them.
