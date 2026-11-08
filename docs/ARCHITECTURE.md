# Lore — Architecture & Flow

Rendered SVGs are in [`diagrams/`](diagrams/) (regenerate (outputs flow-N.svg) with
`npx -p @mermaid-js/mermaid-cli mmdc -i docs/ARCHITECTURE.md -o docs/diagrams/flow.svg`).

Diagrams reflect the implemented system as of 2026-11-07 (Phase 1 complete, Phase 2 in progress).
Decisions referenced as ADR-xxx are in [`DECISIONS.md`](DECISIONS.md).

## 1. End-to-end flow

```mermaid
flowchart TD
    subgraph User
        ZIP[/"Instagram export ZIP<br/>(Accounts Center, JSON)"/]
        MEDIA[/"Screenshots / screen recordings<br/>(optional)"/]
    end

    subgraph API["FastAPI (X-API-Key)"]
        UP["POST /imports"]
        RES["POST /imports/{id}/resume"]
        ENQ["POST /processing/enqueue"]
        UPM["POST /content/{id}/media"]
        REP["POST /content/{id}/reprocess"]
        STATS["GET /stats · /processing/jobs<br/>/content/{id}/job · /content/{id}/media"]
    end

    subgraph Import["Import worker — queue lore.imports"]
        SAFE["Safe ZIP reader<br/>no extraction · traversal / symlink / bomb checks"]
        DETECT["Classify files<br/>path suffix → JSON shape"]
        PARSE["Parsers<br/>saved_posts · saved_collections · messages"]
        URL["URL parser<br/>canonical URL · shortcode identity"]
        DEDUPE["Deduplicate<br/>1 content_item ← N content_sources"]
        CKPT["Per-file checkpoint<br/>import_files (resumable)"]
    end

    subgraph Pipeline["Pipeline worker — queue lore.pipeline (1 job per post)"]
        VAL["VALIDATING"]
        MED["EXTRACTING_MEDIA / TRANSCRIBING<br/>Tesseract OCR · ffmpeg frames · faster-whisper"]
        STR["STRUCTURING<br/>LLM via Copilot SDK (no tools)<br/>→ Pydantic schema, 1 corrective retry"]
    end

    subgraph Beat["Celery beat (every 60s)"]
        SWI["imports.sweep<br/>stale / pending batches"]
        SWP["pipeline.sweep<br/>queued · due retries · stale jobs"]
    end

    subgraph Store["PostgreSQL (system of record) + local media store"]
        DB[("content_items · content_sources<br/>import_batches · import_files · raw_import_records<br/>processing_jobs · media_assets · ocr_texts · transcripts<br/>audit_logs")]
        FS[("data/media/&lt;content&gt;/&lt;sha256&gt;.ext")]
    end

    REDIS{{"Redis (broker, AOF)"}}
    LLM(["GitHub Copilot<br/>(caption · on-screen text · transcript only)"])

    ZIP --> UP --> REDIS --> SAFE --> DETECT --> PARSE --> URL --> DEDUPE --> DB
    PARSE -. progress .-> CKPT --> DB
    RES --> REDIS

    ENQ -- "create / re-arm job" --> DB
    ENQ --> REDIS
    MEDIA --> UPM --> FS
    UPM -- "re-arm job (priority)" --> DB
    REP -- "re-arm job" --> DB
    REDIS --> VAL --> MED --> STR
    MED <--> FS
    MED --> DB
    STR <--> LLM
    STR --> DB

    SWI --> REDIS
    SWP --> REDIS
    DB -.-> SWI
    DB -.-> SWP
    STATS -.-> DB
```

## 2. Lifecycle of one post's job (`processing_jobs`, one row per post)

```mermaid
stateDiagram-v2
    [*] --> QUEUED: enqueue (bulk · import · media added · reprocess)
    QUEUED --> VALIDATING: worker claims job (fencing token)
    VALIDATING --> FAILED: content deleted
    VALIDATING --> EXTRACTING_MEDIA: has image assets
    VALIDATING --> TRANSCRIBING: has video / audio assets
    VALIDATING --> STRUCTURING: no media
    EXTRACTING_MEDIA --> TRANSCRIBING
    EXTRACTING_MEDIA --> STRUCTURING
    TRANSCRIBING --> STRUCTURING: partial results kept even if an asset fails

    STRUCTURING --> NEEDS_MANUAL_INPUT: nothing to read, or caption too thin<br/>and no media text (never guessed)
    STRUCTURING --> COMPLETED: extraction valid & sufficient<br/>(or input unchanged → cached)
    STRUCTURING --> RETRY_WAIT: AI error / invalid output
    STRUCTURING --> FAILED: no AI provider configured

    RETRY_WAIT --> VALIDATING: next_attempt_at reached (30s·2ⁿ, ≤1h)
    RETRY_WAIT --> FAILED: attempts ≥ max_attempts (5)

    COMPLETED --> QUEUED: reprocess / new media
    NEEDS_MANUAL_INPUT --> QUEUED: media uploaded / reprocess
    FAILED --> QUEUED: reprocess

    note right of VALIDATING
        Crash at any running stage:
        heartbeat goes stale → pipeline.sweep
        re-dispatches → new runner takes over,
        old runner is fenced out.
    end note
```

## 3. Historical import (resumable)

```mermaid
sequenceDiagram
    autonumber
    actor U as User
    participant API
    participant PG as PostgreSQL
    participant R as Redis
    participant W as Import worker
    participant B as Beat sweep

    U->>API: POST /imports (ZIP)
    API->>API: stream to disk · size limit · ZIP magic bytes
    API->>PG: import_batch (pending)
    API->>R: lore.imports.run(batch)
    API-->>U: 202 {id, status: pending}
    R->>W: deliver task
    W->>PG: claim batch (runner_id, heartbeat)
    W->>PG: register import_files (one per archive member)
    loop each pending file — one transaction per file
        W->>W: parse (saved posts / collections / messages)
        W->>PG: upsert content_items, add content_sources,<br/>raw records, mark file done, update counters
    end
    W->>PG: batch completed + report
    Note over W,B: Worker crashes mid-import
    B->>PG: find RUNNING batch with stale heartbeat
    B->>R: re-dispatch run(batch)
    R->>W: new worker continues from remaining pending files
```

## 4. What is sent to the AI (privacy boundary)

```mermaid
flowchart LR
    subgraph Local["Stays on your machine"]
        C1["Chat names, senders,<br/>message text"]
        C2["Images / video / audio files"]
        C3["Raw export records"]
    end
    subgraph Sent["Sent to the LLM"]
        S1["Caption + hashtags"]
        S2["Creator username · content type"]
        S3["Your collection names"]
        S4["OCR text · transcript<br/>(derived locally)"]
    end
    C2 -- "Tesseract / Whisper (local)" --> S4
    Sent --> LLM(["Copilot SDK<br/>tools disabled · permissions denied"])
```
