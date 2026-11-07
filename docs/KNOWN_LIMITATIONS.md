# Known Limitations

## Export format verification

Verified against one real JSON export (2026-11-06, `your_instagram_activity/` layout):

- `saved/saved_posts.json`: top-level list; `timestamp` + `label_values` (URL, Caption, Hashtags,
  Owner → Name/Username, Brand partner). Some records repeat Caption/Title and omit Owner.
- `saved/saved_collections.json`: one record per collection, items nested under a `Media` section.
  **Items have no "added" timestamp**, so collection sources have `captured_at = NULL`. When the same
  post is also in `saved_posts.json`, that source carries the save time.
- `messages/{inbox,message_requests}/<thread>/message_N.json`: matched the assumed structure.
  Message requests are imported as direct messages; the folder is visible in `conversation_id`.

Still unverified: older/other export variants (the earlier flat `string_map_data` shapes are only
supported as a fallback, tested with synthetic fixtures), multi-part exports, and HTML exports.
Group vs direct detection uses `participants > 2` (in the real export this matched exactly the
5 threads that have `joinable_mode`). A group that has shrunk to 2 people counts as direct.

## Export formats
- **HTML exports are not parsed.** They are inventoried and reported as unsupported. Request the
  export in **JSON** format from Accounts Center.
- Likes, comments, story interactions and other activity files are intentionally not imported (not in
  PRD scope).

## URLs
- `instagram.com/share/...` and `/s/...` links need a network redirect to obtain a shortcode. They are
  stored as content items with `needs_url_resolution` and keyed by URL, so one may later turn out to
  be a duplicate of a shortcode item. Resolution and merging are Phase 2.
- Profile (including `/_u/<user>`), story and other Instagram URLs are counted in reports but do not
  create content items. Stories expire after 24h and are rarely retrievable; 708 were found in the real
  export, mostly story replies in DMs.
- Captions from message shares (`share_text`) are previews; saved-post/collection captions replace
  them. There is no manual caption editing yet, so this override is safe for now (see ADR-014).
- Non-Instagram URLs in messages are ignored.

## Platform
- Instagram provides no official API for reading saved collections or DMs. Historical data comes only
  from user-provided exports. No scraping, cookies or credentials are used.
- Deleted/unavailable posts may appear as saved records without a URL. They are kept as raw records
  (`resolved = false`) and reported, never silently dropped.

## Operational
- Crash recovery latency: a crashed import resumes after the heartbeat goes stale (default 300s) plus
  up to one sweep interval (60s). The heartbeat is updated per file, so a single file taking longer
  than the stale threshold triggers a takeover. This is safe (fencing token) but wastes work.
- Run exactly **one** `beat` process. Several beats only cause duplicate sweeps, which are harmless
  but noisy.
- Upload and worker must share `INSTABRAIN_DATA_DIR` (one Docker volume). A multi-host deployment
  needs object storage (planned abstraction).
- Concurrent imports of *different* batches that share content rely on DB unique constraints. On a
  race, one file may fail with a persistence error and is reported. Re-importing fixes it.
- Files that failed permanently (e.g. malformed JSON) are not retried by resume. Re-upload a fixed
  export instead.
- Uploaded archives are kept in `INSTABRAIN_DATA_DIR/uploads`; no retention/cleanup policy yet.
- pgvector extension is not enabled yet (Phase 6). `media_assets`, `transcripts`, `content_chunks`
  tables are deferred to the phases that use them.
