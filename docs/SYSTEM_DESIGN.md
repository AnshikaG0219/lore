# Lore — Personal Content Intelligence System

> **Working name:** *Lore* (the ChatGPT chat's pick; alternatives were *Sift* and *Mosaic*). The final name isn't settled yet, and "InstaBrain" is retired. The name deliberately leaves Instagram out, because Instagram is only the first source.

**One-line pitch:** You save or share something interesting → Lore captures it → AI understands it → it becomes structured, searchable knowledge → you can ask for it whenever you need it.

**The one metric that matters:** *How easily can the user retrieve useful information they previously discovered and collected?* Optimise for this, not for the number of posts processed.

---

## 1. Problem

Instagram is acting as an **unstructured information repository**, not a knowledge system.

| Pain | Example |
|---|---|
| Forget what was saved | "I know I saved a café in Ladakh…" |
| Can't find the right collection | Same Reel could be in *Travel* or *Weekend Ideas* |
| Group-chat shares get buried | Friends' recommendations scroll away |
| Must rewatch Reels to find one fact | The 3rd book title is only on screen for 2 seconds |
| No cross-source search | Saved posts and DMs live in separate places |
| Duplicates | The same Reel is saved twice and shared in three chats |

**Core principle:** convert unstructured social content into **structured, searchable, reusable knowledge**. The original post always stays linked as the source, and the extracted knowledge must still be useful if the post is later deleted.

---

## 2. Use cases

| # | Use case | Summary |
|---|---|---|
| UC1 | **Historical import** | Upload the Instagram data-export ZIP → parse saved posts, collections and group chats → extract URLs → dedupe → process → Notion |
| UC2 | **Continuous capture** | Share a Reel to Lore (web form → later a Telegram bot / share sheet / extension) → queued → processed automatically |
| UC3 | **Search** | Use keywords, natural language, semantic similarity, or filters (category, tag, creator, source, date) |
| UC4 | **Actionable extraction** | Category-specific schemas: a travel Reel with 6 cafés becomes **6 place entities**, not a one-paragraph summary |

Example questions Lore must answer:
*"Which cafés did I save in Ladakh?"* · *"Recipes with mushrooms"* · *"AI tools I saved for coding"* · *"The Reel with five autumn books"* · *"Everything about Kashmir"*

---

## 3. High-level architecture

```mermaid
flowchart TB
    subgraph IN["1 · Ingestion layer"]
        EXP["Instagram export ZIP<br/>(historical)"]
        CAP["Capture API / web form<br/>(continuous)"]
        UP["Manual upload<br/>(video · images · screenshots)"]
    end

    REG["2 · Content registry<br/>canonical IDs · dedup · source tracking"]
    MAL["3 · Media acquisition layer<br/>resolve URL · fetch accessible media · fallbacks"]

    subgraph PIPE["4 · Async processing pipeline (workers)"]
        AUD["Audio → transcription"]
        IMG["Images → OCR + vision"]
        VID["Video → frame sampling + OCR"]
        TXT["Caption → text + hashtags"]
    end

    INT["5 · Intelligence layer<br/>classify · extract entities · structure · summarise · tag"]

    subgraph STORE["6 · Storage & indexing"]
        PG[("PostgreSQL<br/>source of truth")]
        OBJ[("Object / file storage<br/>media assets")]
        VEC[("pgvector<br/>embeddings")]
    end

    NOTION["Notion<br/>presentation layer"]
    SEARCH["Search + Q&A<br/>hybrid retrieval · grounded answers"]
    UI["Web dashboard"]

    EXP --> REG
    CAP --> REG
    UP --> REG
    REG -->|"new unique item → enqueue"| MAL
    MAL -->|"media available"| PIPE
    PIPE --> INT
    INT -->|"structured knowledge"| PG
    MAL -->|"raw media"| OBJ
    INT -->|"chunks + embeddings"| VEC
    PG -->|"idempotent sync"| NOTION
    PG --> SEARCH
    VEC --> SEARCH
    PG --> UI
    SEARCH --> UI
```

### Key architecture decisions

| # | Decision | Why |
|---|---|---|
| D1 | **PostgreSQL is the source of truth; Notion is only for presentation** | Avoids depending on Notion's rate limits, schema limits and uptime. You can swap Notion for something else later without losing data. |
| D2 | **Ingestion is decoupled from processing** | Capture returns immediately, and AI work runs asynchronously on a queue. |
| D3 | **Historical import comes before automation** | Clears the existing backlog first. |
| D4 | **Structured extraction, not generic summaries** | A list of real cafés, books and ingredients is far more useful than a paragraph about a Reel. |
| D5 | **One canonical record, many sources** | The same Reel can be saved in 2 collections and shared in 3 chats and still be processed once. |
| D6 | **Graceful handling of inaccessible media** | Getting media from Instagram is the biggest external risk, so the product must stay useful when it fails. |
| D7 | **Modular monolith + independent workers** | No premature microservices, but module boundaries are clean enough to split out later. |
| D8 | **Provider abstraction for LLM, STT, OCR and embeddings** | You can swap models without rewriting, and set a model per stage to control cost. |

---

## 4. Ingestion flows

```mermaid
flowchart TB
    subgraph H["Historical (one-off, batch)"]
        direction TB
        H1["Accounts Center →<br/>Export your information<br/>(JSON · Saved + Messages · All time)"] --> H2["Upload ZIP"]
        H2 --> H3["Safe inspect<br/>(no path traversal, size limits)"]
        H3 --> H4["Parsers:<br/>saved posts · collections ·<br/>DMs · group chats"]
        H4 --> H5["Extract + normalise<br/>Instagram URLs"]
    end

    subgraph C["Continuous (ongoing, immediate)"]
        direction TB
        C1["Share Reel"] --> C2["POST /api/v1/capture<br/>(web form → Telegram bot →<br/>share sheet → extension)"]
        C2 --> C3["Validate + normalise URL"]
    end

    H5 --> D{"Already exists?<br/>(canonical URL / shortcode)"}
    C3 --> D
    D -->|"yes"| S["Attach new source only<br/>(no reprocessing)"]
    D -->|"no"| N["Create content item<br/>+ source record"]
    N --> Q[["Processing queue"]]
```

**What the export actually contains:** saved posts and collections are **references only** (post URL, creator username, saved-at time, collection name), with *no caption and no media*. Posts and Reels shared in chats arrive as a `share` object with the link, often the caption text, and the original creator. Only things *you* uploaded (your own posts, and photos or videos sent directly in DMs) come as real files. So the export gives you **the list of what to fetch**, and the media acquisition layer has to get the actual content.

**Supported URL shapes:** `/p/`, `/reel/`, `/reels/`, `/tv/`, short links (where resolvable), and URLs with tracking parameters. All are normalised to a canonical form (shortcode-based) without merging unrelated content.

---

## 5. Media acquisition (the hardest problem)

A URL is only a *reference*. It contains no video, audio or images. A successful **capture** is not the same as successful **media acquisition**, and Lore must never pretend it transcribed something it only has a link to.

```mermaid
flowchart TD
    A["Content item<br/>(URL + whatever metadata)"] --> B{"Attachment already in<br/>export / user upload?"}
    B -->|"yes"| MA["MEDIA_AVAILABLE"]
    B -->|"no"| C{"Authorised retrieval<br/>of public post works?"}
    C -->|"video / images"| MA
    C -->|"caption + metadata only"| MO["METADATA_ONLY"]
    C -->|"private / login wall"| AD["ACCESS_DENIED"]
    C -->|"deleted / 404"| NF["MEDIA_NOT_FOUND"]

    MO --> P1["Process caption now<br/>+ flag for richer input"]
    AD --> MI["MANUAL_INPUT_REQUIRED<br/>notify user → upload Reel / screenshots"]
    NF --> MI
    MI -->|"user uploads"| MA
    MA --> PIPE[["Full processing pipeline"]]
    P1 --> PIPE

    style MA fill:#d9f2e3,stroke:#2e8b57
    style MI fill:#fde7cf,stroke:#d2691e
```

**Three fallback levels:**

1. **Automatic:** media is available from an authorised source, so it's processed automatically.
2. **Semi-automatic:** only the URL is available, so Lore notifies you to upload the Reel or screenshots.
3. **Manual:** you upload the video, images or screenshots directly.

| Content | Can Lore get the media automatically? |
|---|---|
| Public Reel / carousel | Sometimes (via an authorised retrieval method) |
| Private-account Reel | Usually needs a user upload |
| Saved post (from export) | Export gives a reference, not the media |
| Reel shared in a group chat | Export gives the URL (+ caption text), not the video |
| Screenshot / video you upload | ✅ Yes |

**Guardrails from the spec:** no storing Instagram passwords or session cookies, no bypassing logins, CAPTCHAs or rate limits, and no unrestricted DM scraping. Inaccessible items are **never silently dropped**. They go to `NEEDS_MANUAL_INPUT`.

**Contract sketch:**

```python
class AcquisitionStatus(StrEnum):
    MEDIA_AVAILABLE = "media_available"
    METADATA_ONLY = "metadata_only"
    ACCESS_DENIED = "access_denied"
    MEDIA_NOT_FOUND = "media_not_found"
    MANUAL_INPUT_REQUIRED = "manual_input_required"

class MediaAcquirer(Protocol):
    async def acquire(self, item: ContentItem) -> AcquisitionResult: ...
    # AcquisitionResult: status, assets: list[MediaAsset], caption, metadata, provenance
```

---

## 6. Processing pipeline

Once media is available, the audio, frame and image branches **run in parallel** and then merge in the intelligence layer.

```mermaid
flowchart TB
    M["Media assets"] --> A1["FFmpeg / PyAV<br/>extract audio (WAV)"]
    M --> V1["FFmpeg frame sampling<br/>every 1–2 s + scene-change"]
    M --> I1["Carousel slides<br/>(keep slide order)"]
    M --> T1["Caption"]

    A1 --> A2["Whisper / faster-whisper<br/>timestamped transcript<br/>+ language detect"]
    V1 --> V2["OCR on frames<br/>(PaddleOCR / Tesseract)"]
    V2 --> V3["Drop repeated text<br/>across frames"]
    I1 --> I2["OCR per slide"]
    I2 --> I3["Vision LLM<br/>layout + meaning"]
    T1 --> T2["Hashtags · mentions ·<br/>links · entities"]

    A2 --> F["Fuse evidence<br/>(tagged by provenance:<br/>audio / OCR / caption / manual)"]
    V3 --> F
    I3 --> F
    T2 --> F

    F --> C1["Classify<br/>(small, cheap model)"]
    C1 --> C2["Category-specific<br/>structured extraction<br/>(capable model, JSON schema)"]
    C2 --> C3["Summary · key takeaways · tags"]
    C3 --> C4["Chunk + embed"]
    C4 --> DB[("PostgreSQL + pgvector")]
    DB --> NS["Notion sync"]
```

**Rules for the AI layer:**
- Output must match a **validated JSON schema** (Pydantic).
- Label every fact as **explicit**, **inferred** or **unavailable**. Never invent prices, addresses, names or recommendations; unknowns are `null`.
- Record **provenance** for every fact (audio vs on-screen text vs caption vs manual).
- Include a confidence score, flag low-confidence items for review, and allow manual correction.
- Never assume the caption matches what's said in the video.
- Don't OCR every frame: use adaptive sampling plus scene-change detection.

### Category-specific schemas

| Category | Extracted fields |
|---|---|
| ✈️ Travel | destination, places[], location, accommodation, cafés/restaurants, activities, tips, itinerary, cost *(only if stated)* |
| 🍳 Recipe | name, ingredients[] + quantities, steps[], duration, temperature, substitutions |
| 📚 Book | title, author, genre, description, creator's opinion, why it's recommended |
| 💻 Tech | technologies, tools[], concepts, code snippets, tutorials, resources, takeaways |
| 🛍️ Shopping | product, brand, model, price, features, pros/cons, link |
| 🎓 Educational carousel | topic, concepts[], facts, frameworks/steps, examples, references |
| ⏱️ Productivity | technique, routine, steps, tools, actionable recommendations |

**Starting taxonomy (configurable):** Travel · Food & Recipes · Books & Reading · Technology & AI · Career & Learning · Business · Finance · Shopping · Fashion & Beauty · Health & Fitness · Productivity · Lifestyle · Home & Interior · Entertainment · Education · Miscellaneous.

---

## 7. Processing state machine

Every item has an explicit state. Each stage can be retried on its own, and partial results are kept. For example, if Notion sync fails, the transcript is **not** redone; only the sync is retried.

```mermaid
stateDiagram-v2
    [*] --> RECEIVED
    RECEIVED --> VALIDATING
    VALIDATING --> DUPLICATE_DETECTED: already known
    DUPLICATE_DETECTED --> [*]: source attached
    VALIDATING --> QUEUED: new item
    QUEUED --> FETCHING
    FETCHING --> NEEDS_MANUAL_INPUT: no media
    NEEDS_MANUAL_INPUT --> FETCHING: user uploads
    FETCHING --> EXTRACTING_MEDIA
    EXTRACTING_MEDIA --> TRANSCRIBING
    TRANSCRIBING --> CLASSIFYING
    CLASSIFYING --> STRUCTURING
    STRUCTURING --> INDEXING
    INDEXING --> NOTION_SYNC_PENDING
    NOTION_SYNC_PENDING --> COMPLETED
    NOTION_SYNC_PENDING --> NOTION_SYNC_PENDING: retry (backoff)
    COMPLETED --> QUEUED: reprocess

    FETCHING --> FAILED: permanent error
    EXTRACTING_MEDIA --> FAILED
    TRANSCRIBING --> FAILED
    STRUCTURING --> FAILED
    FAILED --> QUEUED: manual retry
    COMPLETED --> [*]
```

**Job rules:** retry transient errors with exponential backoff, don't retry permanent failures forever, make jobs idempotent, never lose a captured URL if a worker crashes, and keep job state in PostgreSQL (Redis is only the broker).

---

## 8. End-to-end sequence (one Reel)

```mermaid
sequenceDiagram
    autonumber
    actor U as You
    participant API as Capture API
    participant REG as Registry (Postgres)
    participant Q as Queue (Redis/Celery)
    participant MA as Media acquisition
    participant W as Workers (STT · OCR · frames)
    participant AI as Intelligence (LLM)
    participant N as Notion

    U->>API: Share Reel URL
    API->>REG: normalise + dedup check
    alt already known
        REG-->>API: existing content_id
        API->>REG: add source row
        API-->>U: {duplicate: true}
    else new
        REG-->>API: new content_id
        API->>Q: enqueue job
        API-->>U: {status: "queued"} (returns immediately)
        Q->>MA: acquire media
        alt media available
            MA->>W: video / images
            par in parallel
                W->>W: transcribe audio
            and
                W->>W: sample frames + OCR
            and
                W->>W: caption parse
            end
            W->>AI: fused evidence
            AI->>REG: structured knowledge + embeddings
            REG->>N: create / update page (idempotent)
            N-->>REG: notion_page_id
        else not accessible
            MA->>REG: NEEDS_MANUAL_INPUT
            REG-->>U: "Upload this Reel or screenshots"
        end
    end
```

---

## 9. Deduplication and source tracking

```mermaid
flowchart LR
    S1["Saved: Travel"] --> C
    S2["Saved: Weekend Ideas"] --> C
    S3["Group chat: Friends"] --> C
    S4["Group chat: College"] --> C
    S5["Manual capture: Nov 2026"] --> C
    C(["ONE content item<br/>reel/ABC123<br/>processed once"])
```

**Match on (strongest first):** canonical URL → platform shortcode / media ID → canonical URL hash → media fingerprint → carefully controlled fuzzy match. **Never merge on caption similarity alone.**

Each source keeps its type, name, timestamp, sender, import batch and original message reference.

---

## 10. Data model

```mermaid
erDiagram
    USERS ||--o{ CONTENT_ITEMS : owns
    CONTENT_ITEMS ||--o{ CONTENT_SOURCES : "found via"
    CONTENT_ITEMS ||--o{ MEDIA_ASSETS : has
    CONTENT_ITEMS ||--o{ TRANSCRIPTS : has
    CONTENT_ITEMS ||--o{ OCR_RESULTS : has
    CONTENT_ITEMS ||--o{ PROCESSING_JOBS : "processed by"
    CONTENT_ITEMS ||--o{ CONTENT_CHUNKS : "indexed as"
    IMPORT_BATCHES ||--o{ CONTENT_SOURCES : produced
    MEDIA_ASSETS ||--o{ OCR_RESULTS : "read from"

    CONTENT_ITEMS {
        uuid id PK
        text canonical_url UK
        text platform
        text platform_content_id UK
        text content_type
        text title
        text creator_username
        text caption
        text summary
        text primary_category
        text secondary_category
        jsonb extracted_data
        jsonb tags
        float extraction_confidence
        text processing_status
        text notion_page_id
        text notion_sync_status
        timestamptz published_at
        timestamptz first_captured_at
        timestamptz last_processed_at
    }
    CONTENT_SOURCES {
        uuid id PK
        uuid content_id FK
        uuid import_batch_id FK
        text source_type
        text source_name
        text source_reference
        text sender
        timestamptz captured_at
        jsonb metadata
    }
    MEDIA_ASSETS {
        uuid id PK
        uuid content_id FK
        text asset_type
        text storage_path
        text mime_type
        bigint file_size
        text checksum
    }
    TRANSCRIPTS {
        uuid id PK
        uuid content_id FK
        text language
        text transcript_text
        jsonb timestamped_segments
        text provider
    }
    OCR_RESULTS {
        uuid id PK
        uuid content_id FK
        uuid media_asset_id FK
        int frame_or_slide_index
        text text
    }
    PROCESSING_JOBS {
        uuid id PK
        uuid content_id FK
        text job_type
        text status
        int retry_count
        text error_message
    }
    CONTENT_CHUNKS {
        uuid id PK
        uuid content_id FK
        int chunk_index
        text chunk_text
        vector embedding
    }
    IMPORT_BATCHES {
        uuid id PK
        text filename
        text status
        int records_found
        int duplicates_found
        int failures
    }
```

Also include `audit_logs` (entity, action, details JSONB). Index `canonical_url`, `platform_content_id`, `processing_status`, `primary_category`, `published_at`, `captured_at`, `notion_page_id`, plus the full-text and vector indexes. Use UUID primary keys, and don't hard-code single-user assumptions.

---

## 11. Search and Q&A

```mermaid
flowchart TB
    Q["'Which cafés did I<br/>save in Ladakh?'"] --> P["Query understanding<br/>(intent + filters:<br/>category=travel, place=Ladakh)"]
    P --> FT["Full-text search<br/>(Postgres tsvector)"]
    P --> VS["Vector search<br/>(pgvector over chunks)"]
    FT --> R["Hybrid rank<br/>+ filters"]
    VS --> R
    R --> G["LLM answer<br/>grounded ONLY in retrieved records"]
    G --> A["Answer + Instagram links<br/>+ Notion links<br/>or 'nothing found'"]
```

Embed titles, summaries, takeaways, extracted info and (chunked) transcripts. **Never** embed a long transcript as a single vector. The assistant must say so clearly when nothing matches, and must never invent results.

---

## 12. Notion integration

**Database: `Content Inbox`**

| Property | Type | Property | Type |
|---|---|---|---|
| Title | Title | Tags | Multi-select |
| Source URL | URL | Date Saved | Date |
| Creator | Rich text | Date Published | Date |
| Content Type | Select | Processing Status | Select |
| Primary Category | Select | Extraction Confidence | Number |
| Secondary Category | Rich text | Original Collection | Rich text |
| Summary | Rich text | Content ID | Rich text |
| Review Required | Checkbox | Last Processed | Date |

**Page body:** IG link → metadata → summary → key takeaways → entities → category-specific section → transcript → OCR text → original caption → tags → source history → processing metadata.

**Views:** All · Recently Added · Pending Review · Travel · Books · Food · Tech & AI · Shopping · Tutorials · Productivity · Failed.

**Sync rules:** idempotent (store `notion_page_id` and update rather than duplicate), retry with backoff, respect rate limits and block-size limits, handle archived databases, and support manual resync.

---

## 13. API surface

| Area | Endpoints |
|---|---|
| Capture | `POST /api/v1/capture` |
| Content | `GET /content` · `GET /content/{id}` · `PATCH /content/{id}` · `DELETE /content/{id}` · `POST /content/{id}/reprocess` |
| Imports | `POST /imports` · `GET /imports` · `GET /imports/{id}` · `POST /imports/{id}/resume` |
| Search | `GET /search?q=` · `POST /ask` |
| Notion | `POST /notion/sync/{content_id}` · `POST /notion/sync-all` · `GET /notion/status` |
| System | `GET /health` · `GET /ready` · `GET /api/v1/stats` |

```jsonc
// POST /api/v1/capture
{ "url": "https://www.instagram.com/reel/EXAMPLE/", "source": "manual",
  "collection": "Travel", "notes": "Interesting places to visit" }
// → 202
{ "success": true, "content_id": "uuid", "status": "queued", "duplicate": false }
```

---

## 14. Tech stack

| Layer | Choice |
|---|---|
| Backend | Python 3.12+, FastAPI, Pydantic, SQLAlchemy 2.x, Alembic |
| Database | PostgreSQL + pgvector + JSONB |
| Jobs | Celery + Redis (broker); job state lives in Postgres |
| Media | FFmpeg / PyAV, PaddleOCR or Tesseract, Pillow |
| AI | LLM with structured JSON output; Whisper / faster-whisper; vision LLM; embedding model (all behind interfaces) |
| Frontend | Next.js, TypeScript, React, Tailwind, shadcn/ui *(start with a plain HTML dashboard)* |
| Integrations | Notion API, Instagram export parser, REST capture API |
| Infra | Docker + Docker Compose; local FS first, with an object-storage abstraction later |
| Testing | Pytest, HTTPX, integration and E2E tests, fixture-based parser tests |

**Cost controls:** dedupe *before* AI, cache transcripts and OCR, use a small model for classification and a capable one for extraction, configure models per stage, and track token cost.

**Security:** auth on the dashboard, API keys and rate limits on capture, secrets kept in env/secret manager, no Instagram passwords, safe ZIP extraction (path-traversal guard, size and MIME limits), data export and delete, and no sending unrelated private DMs to AI providers.

---

## 15. Roadmap

```mermaid
flowchart LR
    P1["<b>Phase 1 · P0</b><br/>Foundation +<br/>historical import"] --> P2["<b>Phase 2 · P0</b><br/>Extraction pipeline<br/>(STT · OCR · LLM)"]
    P2 --> P3["<b>Phase 3 · P0</b><br/>Notion sync"]
    P3 --> P4["<b>Phase 4 · P1</b><br/>Continuous capture<br/>(+ Telegram bot)"]
    P4 --> P5["<b>Phase 5 · P1</b><br/>Dashboard UI"]
    P5 --> P6["<b>Phase 6 · P2</b><br/>Semantic search<br/>+ Q&A assistant"]
    P6 --> P7["<b>Phase 7 · P3</b><br/>Itineraries · reading lists ·<br/>digests · more platforms"]

    style P1 fill:#d9f2e3,stroke:#2e8b57
```

| Phase | Definition of done |
|---|---|
| 1 | A real export is uploaded → saved posts and group-chat URLs are extracted → sources preserved → duplicates merged → resumable → import report |
| 2 | A Reel, post or carousel becomes validated structured info with a source reference |
| 3 | Processed items appear in Notion with summary, metadata, extraction and the IG link, with no duplicate pages |
| 4 | Sharing a URL processes it end to end with no manual import |
| 5 | Dashboard, inbox, detail page, import manager, editing, bulk ops, settings |
| 6 | Natural-language questions are answered from stored records, with links |
| 7 | Recommendations, auto-collections, itineraries, reading lists, YouTube/TikTok/Pinterest |

**First deliverable (Phase 1):**

```bash
python -m scripts.inspect_instagram_export /path/to/instagram-export.zip
# → files discovered · saved posts · collections · conversations ·
#   URLs extracted · duplicates · unrecognised files · parse errors
python -m scripts.import_instagram_export /path/to/instagram-export.zip   # → PostgreSQL
```

---

## 16. Repository layout

```
lore/
├── backend/
│   ├── app/
│   │   ├── api/v1/        capture · content · imports · search · notion · system
│   │   ├── core/          config · security · logging
│   │   ├── db/            models/ · repositories/ · session.py
│   │   ├── ingestion/     instagram/ · adapters/ · url_parser.py · deduplication.py
│   │   ├── acquisition/   resolver · upload handler · fallbacks
│   │   ├── processing/    media/ · ocr/ · transcription/ · classification/ · extraction/ · pipeline.py
│   │   ├── integrations/  notion/
│   │   ├── search/        embeddings · retrieval · assistant
│   │   ├── workers/  schemas/  main.py
│   ├── tests/  alembic/  requirements.txt
├── frontend/               (plain HTML dashboard first → Next.js later)
├── scripts/                inspect_instagram_export.py · import_instagram_export.py
├── docs/                   IMPLEMENTATION_PLAN.md · DECISIONS.md · KNOWN_LIMITATIONS.md
├── docker-compose.yml  .env.example  README.md
```

---

## 17. Open decisions

| Decision | Options | Recommendation |
|---|---|---|
| **Final name** | Lore · Sift · Mosaic · Curio · Memora… | **Lore** |
| **Expected volume** | <1k · 1k–10k · 10k–100k · 100k+ | Size it from the real export count |
| **Capture mechanism** | Import + share/paste · auto-monitor IG · manual only | **Import + share/paste** |
| **Processing strategy** | Immediate · scheduled batch · hybrid | **Hybrid**: immediate for new items, batched for the historical backlog |
| **Fallback when media is inaccessible** | Telegram bot upload · web upload | Ask the user; this materially changes reliability |
| **Media retrieval for public posts** | Official/authorised only · unofficial downloader as an opt-in adapter | Keep any unofficial downloader as an **optional, isolated adapter**, never a core dependency |

**Next step:** request the Instagram export (JSON, Saved + Messages, all time). The Phase 1 parser should be built against the **real** export structure, not guessed schemas.
