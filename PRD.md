# Lore — Personal Content Intelligence & Knowledge Management System

**Document Type:** Product Requirements Document (PRD) + Software Architecture Specification  
**Project Name:** Lore  
**Version:** 1.0  
**Status:** Initial Development Specification  
**Target User:** Single-user personal application (designed for future multi-user support)  
**Primary Platform:** Web application + backend services  
**Primary Knowledge Destination:** Notion  
**Development Approach:** Modular monolith initially, with independently scalable background workers.

---

# 1. Product Overview

## 1.1 Problem Statement

I frequently discover useful information on Instagram through Reels, posts, carousels and recommendations shared by other people.

Currently, I collect this content through two primary methods:

1. Saving Instagram posts and Reels into different Instagram Collections.
2. Sharing Instagram posts and Reels in personal/group Instagram chats.

Over time, I accumulate a large volume of saved and shared content.

The problem is that the amount of information I collect grows faster than my ability to organise, revisit and retrieve it.

As a result:
- I forget which useful information I have saved.
- I cannot remember which Instagram collection contains a particular post.
- Information shared in group conversations gets buried under newer messages.
- I have to manually revisit Reels to find specific information.
- I cannot search across all my saved and shared content.
- I frequently save the same content multiple times.
- Valuable recommendations and insights become inaccessible despite being technically saved.

Instagram is currently acting as an unstructured information repository. It is not an effective personal knowledge management system.

## 1.2 Product Vision

Build a Personal Content Intelligence System that automatically collects content from Instagram, extracts the actual information contained within it, converts it into structured knowledge and makes that knowledge searchable.

The application should work as a personal second brain.

The user should be able to save or share an Instagram post, forget about it, and trust that the useful information contained in that post will be extracted, organised and available whenever needed.

The system must not merely bookmark Instagram URLs or generate generic summaries.

It must understand the underlying information and extract actionable knowledge.

## 1.3 Core Product Principle

**Convert unstructured social media content into structured, searchable, reusable knowledge.**

The original Instagram post should always remain accessible as the source of information.

The extracted information should be independently useful even if the Instagram post is subsequently deleted or becomes inaccessible.

---

# 2. Primary Use Cases

## Use Case 1: Historical Instagram Data Import

The user has already saved hundreds or thousands of Instagram posts and shared numerous posts in group conversations.

The application should allow the user to import historical Instagram data.

Expected workflow:

1. User downloads their Instagram account information using Instagram's Accounts Center export functionality.
2. User uploads the exported ZIP file to Lore.
3. The application inspects the ZIP archive.
4. It identifies relevant files containing:
   - Saved posts.
   - Saved collections.
   - Instagram messages.
   - Group conversations.
   - Shared Instagram URLs.
   - Available timestamps and metadata.
5. The application extracts relevant content references.
6. It identifies duplicate posts.
7. It creates a processing queue.
8. It fetches and processes the accessible content.
9. It stores the extracted knowledge in the database.
10. It synchronises processed entries with Notion.

The importer must support changes in Instagram's export directory structures and JSON formats.

It must not assume that all export files have an identical structure.

## Use Case 2: Continuous Content Capture

The user discovers a useful Reel and wants it added to their personal knowledge base.

Expected workflow:

1. User shares the Reel through Instagram's native Share functionality.
2. User sends the link to an Lore capture endpoint.
3. Lore receives the URL.
4. The URL is validated and normalised.
5. The system checks whether the content already exists.
6. If new, the content is added to the processing queue.
7. The AI extraction pipeline processes it.
8. The structured result is stored in PostgreSQL.
9. The result is synchronised with Notion.

The capture process should require minimal user interaction.

Potential capture interfaces:

- Telegram bot.
- WhatsApp integration, if technically and legally feasible.
- Mobile share-sheet application.
- Web-based capture form.
- Browser extension.
- Personal webhook endpoint.

Prioritise a simple web capture endpoint for the initial MVP. Add Telegram or another convenient share destination in a later phase.

Do not assume Instagram supports arbitrary automated reading of personal saved collections or group DMs through its official APIs.

## Use Case 3: Search Previously Collected Information

The user wants to retrieve information without remembering the original Instagram post.

Examples:

- Find all the cafés I saved in Ladakh.
- Show me bookstores recommended by Instagram creators.
- Which restaurants did I save in Japan?
- Find the Reel where someone recommended five books for autumn.
- Show me all the AI tools I have saved.
- Find recipes that contain mushrooms.
- Did I save any tutorials about system design?
- Show me all travel recommendations related to Kashmir.
- Find the post where someone talked about buying second-hand books.
- Which posts contain recommendations for productivity applications?

Search should work using:
- Exact keywords.
- Natural language queries.
- Semantic similarity.
- Category and tag filters.
- Creator names.
- Original source.
- Date ranges.

## Use Case 4: Extract Actionable Knowledge

The system must understand content according to its subject.

Examples:

**Travel Reel**
- Destination.
- Places mentioned.
- Location.
- Estimated cost, if explicitly available.
- Accommodation recommendations.
- Restaurants and cafés.
- Activities.
- Travel tips.
- Itinerary details.

**Recipe Reel**
- Recipe name.
- Ingredients.
- Quantities.
- Preparation steps.
- Cooking duration.
- Temperature.
- Tips and substitutions.

**Book Recommendation**
- Book title.
- Author.
- Genre.
- Description.
- Creator's opinion.
- Reason for recommendation.

**Technology Reel**
- Technology or framework.
- Tools mentioned.
- Technical concepts.
- Code snippets.
- Tutorials.
- Useful resources.
- Key takeaways.

**Shopping Reel**
- Product name.
- Brand.
- Model.
- Price, if available.
- Product features.
- Pros and cons.
- Purchase link, if available.

**Educational Carousel**
- Topic.
- Individual concepts.
- Important facts.
- Steps or frameworks.
- Examples.
- References.

**Lifestyle and Productivity**
- Technique.
- Recommended routine.
- Steps.
- Tools.
- Actionable recommendations.

The system should use category-specific extraction schemas rather than forcing every type of content into the same generic summary format.

---

# 3. Functional Requirements

## 3.1 Instagram Data Ingestion

### FR-001: Historical Data Import

The system must accept Instagram data exports in ZIP format.

Requirements:

- Upload ZIP archives.
- Validate archive integrity.
- Extract files safely.
- Prevent ZIP path traversal attacks.
- Inspect nested directory structures.
- Identify JSON, HTML and media files.
- Parse available saved-post data.
- Parse saved collection metadata where available.
- Parse Instagram message exports.
- Extract shared Instagram URLs from group conversations.
- Preserve original timestamps.
- Preserve available sender and conversation metadata.
- Preserve collection and conversation associations.
- Generate an import report.

### FR-002: Flexible Export Parser

Build a modular Instagram export parser.

Do not tightly couple the parser to a single JSON schema.

Requirements:

- Support different Instagram export layouts.
- Handle nested JSON objects.
- Handle missing optional fields.
- Handle inconsistent timestamp formats.
- Handle different URL formats.
- Handle deleted or unavailable content references.
- Log unrecognised structures.
- Preserve raw import records for debugging.
- Provide fixture-based unit tests.

The parser should have separate modules for:

1. Saved posts.
2. Collections.
3. Direct messages.
4. Group conversations.
5. Shared links.
6. Export metadata.

### FR-003: Instagram URL Extraction

Extract Instagram URLs from imported messages.

Support:

- instagram.com/p/
- instagram.com/reel/
- instagram.com/reels/
- instagram.com/tv/
- Instagram short links where resolvable.
- URLs containing tracking parameters.

Normalise URLs to a canonical format whenever possible.

Do not accidentally merge unrelated content.

### FR-004: Continuous Content Capture

Provide a REST API endpoint:

POST /api/v1/capture

Example request:

{
  "url": "https://www.instagram.com/reel/EXAMPLE/",
  "source": "manual",
  "collection": "Travel",
  "notes": "Interesting places to visit"
}

Expected response:

{
  "success": true,
  "content_id": "uuid",
  "status": "queued",
  "duplicate": false
}

The endpoint must:
- Validate incoming URLs.
- Reject unsupported domains.
- Normalise URLs.
- Check for existing content.
- Create a new record if necessary.
- Record capture metadata.
- Queue processing asynchronously.
- Return immediately without waiting for AI processing.

### FR-005: Multiple Ingestion Sources

Design ingestion as an adapter-based system.

Initial adapters:

- Instagram export adapter.
- Manual URL capture adapter.

Future adapters:

- Telegram bot.
- Mobile share sheet.
- Browser extension.
- WhatsApp Business integration, where supported.
- Additional social platforms.

Each adapter must produce a common internal content representation.

---

# 4. Content Extraction Engine

This is the most important component of the application.

## 4.1 Content Retrieval

Given an Instagram URL, attempt to retrieve publicly or legitimately accessible content.

Extraction should use the following priority:

1. Publicly accessible post metadata.
2. Caption and description.
3. User-provided media or export attachments.
4. Available video or image media.
5. Alternative authorised content sources.

Do not use the user's Instagram password, session cookies or browser credentials as a default scraping mechanism.

Do not bypass login restrictions, access controls, rate limits or platform protections.

> **Amended 2026-11-09 (ADR-023):** the user approved fetching *public* posts with yt-dlp *logged out* (no credentials or cookies), rate-limited with a circuit breaker. Logging in, using session cookies, CAPTCHA bypass and private-account access remain prohibited.


For inaccessible posts, create a record with status `needs_manual_input` and provide an interface to upload screenshots, images or video files.

The application must never silently discard a captured URL because its content cannot be retrieved.

## 4.2 Media Processing

Support:

**Images**
- Download or accept uploaded images.
- OCR extraction.
- Vision-based interpretation.
- Carousel image ordering.
- Text extraction from screenshots.
- Image descriptions where useful.

**Videos**
- Audio extraction.
- Speech-to-text transcription.
- Subtitle extraction when available.
- Frame sampling.
- OCR on sampled frames.
- Visual content understanding.
- Identification of important on-screen text.

**Captions**
- Extract caption text.
- Preserve original text.
- Extract hashtags.
- Identify mentioned entities.
- Extract links where available.

## 4.3 Video Transcription

Use a speech-to-text provider.

Requirements:

- Detect spoken language where supported.
- Preserve timestamps where supported.
- Handle multiple speakers where practical.
- Handle background music.
- Distinguish spoken content from on-screen text.
- Retain original transcription.
- Handle transcription failures gracefully.

Do not assume that a Reel's caption accurately represents its spoken content.

## 4.4 AI Extraction Pipeline

Implement an asynchronous processing pipeline.

Recommended stages:

1. Content validation.
2. Metadata extraction.
3. Caption extraction.
4. Media retrieval.
5. OCR.
6. Video transcription.
7. Content understanding.
8. Category classification.
9. Entity extraction.
10. Structured information extraction.
11. Summary generation.
12. Tag generation.
13. Embedding generation.
14. Database persistence.
15. Notion synchronisation.

Each stage should be independently retryable.

AI output must follow a validated JSON schema.

The AI must distinguish between:

- Explicitly stated information.
- Information inferred from context.
- Information not available.

Never invent prices, addresses, locations, names or recommendations.

Unknown information must be represented as null or marked unavailable.

## 4.5 Structured Extraction Schema

Every processed content item should produce a common structure.

Example:

{
  "title": "Five Interesting Bookstores in Japan",
  "content_type": "reel",
  "category": "travel",
  "subcategory": "bookstores",
  "creator": "example_creator",
  "summary": "A collection of independent bookstores in Japan.",
  "key_takeaways": [
    "Independent bookstores worth visiting",
    "Unique architectural spaces"
  ],
  "entities": [
    {
      "name": "Example Bookstore",
      "entity_type": "bookstore",
      "location": "Tokyo, Japan",
      "description": "Independent bookstore"
    }
  ],
  "extracted_information": {
    "places": [],
    "products": [],
    "books": [],
    "ingredients": [],
    "steps": [],
    "tools": []
  },
  "tags": [
    "Japan",
    "Bookstores",
    "Travel"
  ],
  "confidence": 0.95
}

This is an illustrative schema. The actual schema should be category-aware and validated.

---

# 5. AI Classification and Knowledge Organisation

## 5.1 Category Taxonomy

Start with these top-level categories:

1. Travel.
2. Food and Recipes.
3. Books and Reading.
4. Technology and AI.
5. Career and Learning.
6. Business and Entrepreneurship.
7. Finance and Investing.
8. Shopping and Products.
9. Fashion and Beauty.
10. Health and Fitness.
11. Productivity.
12. Lifestyle.
13. Home and Interior.
14. Entertainment.
15. Education.
16. Miscellaneous.

Categories should be configurable.

The AI should automatically assign:
- Primary category.
- Secondary category.
- Relevant tags.
- Named entities.
- Geographic locations where applicable.

Support multiple tags per item.

Allow the user to edit categories and tags manually.

## 5.2 Knowledge Extraction vs Summarisation

Do not implement a system that only produces a paragraph summary.

The goal is to extract reusable information.

For example, a travel Reel mentioning six cafés should produce six structured place entities, not just a summary saying that the Reel recommends cafés.

Similarly, a recipe should produce ingredients and instructions separately.

Maintain both:
- Original content.
- Human-readable summary.
- Structured extracted information.

## 5.3 Confidence and Verification

Every AI-generated extraction should include an appropriate confidence indicator.

Requirements:

- Do not display false precision.
- Flag low-confidence extraction.
- Preserve source evidence where possible.
- Allow manual correction.
- Distinguish between extracted facts and AI-generated interpretation.

---

# 6. Duplicate Detection and Content Identity

Deduplication is a critical requirement.

The same Reel may be:
- Saved in multiple collections.
- Shared in several group chats.
- Captured through the web endpoint.
- Imported from multiple Instagram exports.

It must create one canonical content record.

## 6.1 Deduplication Strategy

Use multiple matching methods:

1. Canonical Instagram URL.
2. Instagram media shortcode or content identifier.
3. Platform-specific media identifier, when available.
4. Canonical URL hash.
5. Media fingerprint where available.
6. Carefully controlled fuzzy matching.

Do not merge records based only on similar captions.

## 6.2 Source Tracking

Maintain a separate source association table.

Example:

Content:
Instagram Reel ABC

Sources:
- Saved collection: Travel.
- Saved collection: Weekend Ideas.
- Group chat: Friends.
- Group chat: College.
- Manual capture: November 2026.

One canonical content item can have multiple source associations.

Preserve:
- Source type.
- Source name.
- Source timestamp.
- Sender where available.
- Import batch.
- Original message reference where available.

---

# 7. Notion Integration

Notion will act as the primary human-readable knowledge base.

PostgreSQL remains the system of record.

## 7.1 Notion Database

Create one central database named:

**Content Inbox**

Suggested properties:

| Property | Type |
|---|---|
| Title | Title |
| Source URL | URL |
| Creator | Rich text |
| Content Type | Select |
| Primary Category | Select |
| Secondary Category | Rich text |
| Summary | Rich text |
| Tags | Multi-select |
| Date Saved | Date |
| Date Published | Date |
| Processing Status | Select |
| Extraction Confidence | Number |
| Original Collection | Rich text |
| Content ID | Rich text |
| Last Processed | Date |
| Review Required | Checkbox |

## 7.2 Notion Page Content

Each database entry should contain a detailed page.

Suggested page layout:

1. Original Instagram link.
2. Content metadata.
3. AI-generated summary.
4. Key takeaways.
5. Extracted entities.
6. Category-specific information.
7. Full transcription, if available.
8. OCR text, if useful.
9. Original caption.
10. AI-generated tags.
11. Source history.
12. Processing metadata.

## 7.3 Notion Synchronisation

Requirements:

- Create new pages.
- Update existing pages.
- Avoid duplicate pages.
- Store Notion page ID in PostgreSQL.
- Retry failed synchronisations.
- Handle Notion API rate limits.
- Respect Notion's rich-text and block-size constraints.
- Handle missing or archived databases.
- Provide a manual resync option.
- Track sync status and errors.

Use idempotent synchronisation.

A repeated sync must update the existing Notion entry instead of creating a duplicate.

## 7.4 Notion Views

Create these views:

- All Content.
- Recently Added.
- Pending Review.
- Travel.
- Books.
- Food and Recipes.
- Technology and AI.
- Shopping.
- Tutorials.
- Productivity.
- Failed Processing.

The user should be able to filter by creator, date, content type and category.

---

# 8. Search and Personal Knowledge Assistant

Build semantic search after the core ingestion pipeline is stable.

## 8.1 Search Features

Support:

- Full-text search.
- Semantic vector search.
- Hybrid search.
- Category filters.
- Date filters.
- Content type filters.
- Creator filters.
- Tag filters.

## 8.2 Natural Language Interface

Build an interface where the user can ask questions about their saved content.

Examples:

"Which cafés did I save in Ladakh?"

"Show me the books I saved that are about women and relationships."

"Find the Reel where someone explains how to make Japanese cheesecake."

"Which AI tools did I save for coding?"

"Give me all the travel recommendations I saved for Japan."

"Show me all the recipes that use mushrooms."

The assistant should:

1. Understand the query.
2. Search the PostgreSQL knowledge base.
3. Retrieve relevant content.
4. Generate an answer grounded in retrieved records.
5. Include links to original Instagram posts.
6. Include links to corresponding Notion entries.
7. Clearly indicate when no matching information exists.

The assistant must not fabricate results or claim that an item exists when it cannot retrieve supporting records.

## 8.3 Semantic Search Implementation

Use PostgreSQL with pgvector.

Generate embeddings from:
- Titles.
- Summaries.
- Key takeaways.
- Extracted information.
- Transcriptions, where useful.

Avoid creating only one embedding for an entire long video transcript.

For long content, implement chunking and retain references to the parent content item.

Support hybrid retrieval combining full-text search and vector similarity.

---

# 9. User Interface Requirements

Build a clean, responsive web application.

The UI should prioritise functionality over visual complexity.

## 9.1 Dashboard

Display:

- Total captured posts.
- Successfully processed posts.
- Pending posts.
- Failed posts.
- Posts requiring manual review.
- Recent activity.
- Category distribution.
- Most frequently saved topics.

## 9.2 Content Inbox

A central list of all captured items.

Each item should show:

- Thumbnail, where available.
- Title.
- Creator.
- Category.
- Short summary.
- Date captured.
- Processing status.
- Original Instagram link.
- Notion sync status.

Allow:
- Search.
- Sorting.
- Filtering.
- Bulk selection.
- Manual categorisation.
- Manual deletion.
- Reprocessing.

## 9.3 Content Detail Page

Show:

- Original source.
- Extracted metadata.
- AI summary.
- Structured information.
- Transcript.
- OCR results.
- Tags.
- Source history.
- Processing logs.
- Notion link.

Allow users to correct AI-generated data.

## 9.4 Import Dashboard

Show:

- Uploaded export name.
- Import date.
- Total files scanned.
- Saved posts found.
- Conversations found.
- URLs extracted.
- Duplicates detected.
- Successful records.
- Failed records.
- Import progress.

The importer should support resumable processing.

## 9.5 Settings

Allow configuration of:

- Notion integration.
- Notion database.
- AI provider.
- Model selection.
- Processing preferences.
- Categories and tags.
- Capture API keys.
- Retention policies.
- Import history.

---

# 10. Technical Architecture

## 10.1 Architecture Style

Use a modular monolith initially.

Do not introduce microservices unnecessarily.

Separate modules logically so individual components can later be extracted into independent services.

## 10.2 Recommended Technology Stack

**Backend**
- Python 3.12+
- FastAPI
- Pydantic
- SQLAlchemy 2.x
- Alembic

**Database**
- PostgreSQL
- pgvector
- JSONB for flexible extracted information

**Background Processing**
- Celery or an equivalent job-processing framework.
- Redis as broker and cache.

**AI**
- LLM API with structured JSON output.
- Speech-to-text provider.
- Vision-capable model for image and video understanding.
- Embedding model for semantic search.

Provider implementations must be abstracted so models can be changed without rewriting the application.

**Media Processing**
- FFmpeg.
- OCR engine.
- Image processing library.
- Video frame sampling.

**Frontend**
- Next.js.
- TypeScript.
- React.
- Tailwind CSS.
- Component library such as shadcn/ui.

**Integrations**
- Notion API.
- Instagram export parser.
- REST capture API.

**Infrastructure**
- Docker.
- Docker Compose.
- PostgreSQL.
- Redis.
- Local filesystem initially, with an abstraction for object storage later.

**Testing**
- Pytest.
- HTTPX.
- Integration tests.
- Frontend component tests.
- End-to-end tests.

## 10.3 High-Level Components

### API Layer

Responsibilities:
- Authentication.
- Upload management.
- Content capture.
- Search.
- Content retrieval.
- Settings.
- Import management.
- Notion synchronisation triggers.

### Ingestion Layer

Responsibilities:
- Export parsing.
- URL extraction.
- Source metadata.
- URL normalisation.
- Duplicate detection.
- Content record creation.

### Processing Layer

Responsibilities:
- Metadata retrieval.
- Media retrieval.
- OCR.
- Transcription.
- AI classification.
- Structured extraction.
- Embeddings.

### Persistence Layer

Responsibilities:
- Database operations.
- Transaction management.
- Content identity.
- Source associations.
- Processing history.
- Search indexing.

### Integration Layer

Responsibilities:
- Notion page creation.
- Notion page updates.
- Notion database configuration.
- Retry and sync management.

### Search Layer

Responsibilities:
- Full-text retrieval.
- Vector retrieval.
- Hybrid ranking.
- Query interpretation.
- Grounded response generation.

---

# 11. Database Design

Use UUID primary keys.

Suggested tables:

## users

- id
- email
- created_at
- updated_at

Although initially single-user, avoid hardcoding ownership assumptions throughout the application.

## content_items

- id
- canonical_url
- platform
- platform_content_id
- content_type
- title
- creator_username
- caption
- summary
- primary_category
- secondary_category
- extracted_data (JSONB)
- tags (JSONB)
- published_at
- first_captured_at
- last_processed_at
- processing_status
- extraction_confidence
- notion_page_id
- notion_sync_status
- created_at
- updated_at

## content_sources

- id
- content_id
- source_type
- source_name
- source_reference
- sender
- captured_at
- metadata (JSONB)

## media_assets

- id
- content_id
- asset_type
- storage_path
- mime_type
- file_size
- checksum
- metadata (JSONB)
- created_at

## transcripts

- id
- content_id
- language
- transcript_text
- timestamped_segments (JSONB)
- provider
- created_at

## processing_jobs

- id
- content_id
- job_type
- status
- retry_count
- error_message
- started_at
- completed_at
- created_at

## import_batches

- id
- filename
- status
- total_files
- processed_files
- records_found
- records_imported
- duplicates_found
- failures
- created_at
- completed_at

## content_chunks

- id
- content_id
- chunk_index
- chunk_text
- embedding
- metadata (JSONB)

## audit_logs

- id
- entity_type
- entity_id
- action
- details (JSONB)
- created_at

Create indexes for:
- canonical_url.
- platform_content_id.
- processing_status.
- primary_category.
- published_at.
- captured_at.
- notion_page_id.
- Full-text search vectors.
- Vector embeddings.

Enforce uniqueness on canonical content identity where appropriate.

---

# 12. Processing State Machine

Every captured item should move through explicit states.

Recommended states:

- RECEIVED
- VALIDATING
- DUPLICATE_DETECTED
- QUEUED
- FETCHING
- EXTRACTING_MEDIA
- TRANSCRIBING
- CLASSIFYING
- STRUCTURING
- INDEXING
- NOTION_SYNC_PENDING
- COMPLETED
- NEEDS_MANUAL_INPUT
- FAILED

A processing job must record its current stage.

Requirements:
- Retry transient failures.
- Do not retry permanent failures indefinitely.
- Use exponential backoff.
- Make jobs idempotent.
- Support manual retries.
- Preserve partial results.
- Do not lose captured URLs if workers crash.

If transcription succeeds but Notion synchronisation fails, the system must retain the transcript and retry only the failed synchronisation stage.

---

# 13. Security, Privacy and Reliability

This is a personal data application. Privacy must be a first-class requirement.

## Authentication

- Protect the dashboard with authentication.
- Use secure session handling.
- Support a single-user deployment initially.
- Protect all administrative endpoints.
- Use API keys for capture endpoints.
- Rate-limit externally accessible endpoints.

## Credential Management

- Store credentials in environment variables or a secrets manager.
- Never commit API keys.
- Never log access tokens.
- Never store Instagram passwords.
- Use Notion OAuth or securely managed integration credentials.

## Data Protection

- Validate uploaded files.
- Restrict archive extraction paths.
- Limit upload size.
- Validate MIME types.
- Avoid executing imported files.
- Encrypt secrets.
- Provide data export and deletion functionality.
- Avoid sending unrelated private messages to AI providers.

## Instagram Compliance

The application must respect Instagram's terms, privacy restrictions and applicable platform policies.

Do not build around:
- Bypassing authentication.
- Session-cookie theft.
- Circumventing access controls.
- CAPTCHA bypass.
- Unauthorised private account scraping.
- Unrestricted group DM scraping.

The historical import should use user-provided exports.

Future capture should use supported and authorised methods.

When content is inaccessible, allow manual upload or user-provided text.

## Reliability

- Structured logs.
- Error tracking.
- Job monitoring.
- Retry handling.
- Database backups.
- Import checkpoints.
- Graceful worker shutdown.
- Health-check endpoints.

---

# 14. API Specification

Initial API routes:

## Capture

POST /api/v1/capture

Capture a new Instagram URL.

## Content

GET /api/v1/content

List content with filters.

GET /api/v1/content/{id}

Retrieve content details.

PATCH /api/v1/content/{id}

Update metadata or extracted information.

DELETE /api/v1/content/{id}

Delete content according to the configured retention policy.

POST /api/v1/content/{id}/reprocess

Trigger reprocessing.

## Imports

POST /api/v1/imports

Upload Instagram export ZIP.

GET /api/v1/imports

List import batches.

GET /api/v1/imports/{id}

Get import progress.

POST /api/v1/imports/{id}/resume

Resume interrupted import.

## Search

GET /api/v1/search?q=...

Keyword and semantic search.

POST /api/v1/ask

Natural language question answering.

## Notion

POST /api/v1/notion/sync/{content_id}

Trigger individual synchronisation.

POST /api/v1/notion/sync-all

Trigger batch synchronisation.

GET /api/v1/notion/status

Check integration status.

## System

GET /health

GET /ready

GET /api/v1/stats

All endpoints must have request validation, consistent error responses and appropriate authentication.

---

# 15. Non-Functional Requirements

## Performance

- Capture API should return quickly without waiting for AI processing.
- Content processing must happen asynchronously.
- Dashboard pagination must be supported.
- Search should be optimised with indexes.
- Large imports should not block the API.
- Media should not be loaded entirely into application memory.

## Scalability

Although this begins as a personal application, design it to handle:

- Thousands of content records.
- Large historical exports.
- Multiple simultaneous processing jobs.
- Long videos.
- Large carousel collections.
- Multiple AI providers.

## Maintainability

- Strong module boundaries.
- Type hints.
- Pydantic schemas.
- Dependency injection.
- Centralised configuration.
- Consistent error handling.
- Database migrations.
- Meaningful logging.
- Documentation.
- Automated tests.

## Cost Optimisation

AI processing can become expensive.

Implement:

- Deduplication before processing.
- Cache reusable extraction results.
- Avoid retranscribing unchanged videos.
- Avoid repeated OCR.
- Use smaller models for simple classification.
- Use more capable models for complex extraction.
- Track token and provider costs.
- Allow per-stage model configuration.

---

# 16. Project Directory Structure

Use a clean, maintainable repository structure.

lore/
│
├── backend/
│   ├── app/
│   │   ├── api/
│   │   │   └── v1/
│   │   │       ├── capture.py
│   │   │       ├── content.py
│   │   │       ├── imports.py
│   │   │       ├── search.py
│   │   │       ├── notion.py
│   │   │       └── system.py
│   │   │
│   │   ├── core/
│   │   │   ├── config.py
│   │   │   ├── security.py
│   │   │   └── logging.py
│   │   │
│   │   ├── db/
│   │   │   ├── models/
│   │   │   ├── repositories/
│   │   │   └── session.py
│   │   │
│   │   ├── ingestion/
│   │   │   ├── instagram/
│   │   │   ├── adapters/
│   │   │   ├── url_parser.py
│   │   │   └── deduplication.py
│   │   │
│   │   ├── processing/
│   │   │   ├── media/
│   │   │   ├── ocr/
│   │   │   ├── transcription/
│   │   │   ├── classification/
│   │   │   ├── extraction/
│   │   │   └── pipeline.py
│   │   │
│   │   ├── integrations/
│   │   │   └── notion/
│   │   │
│   │   ├── search/
│   │   │   ├── embeddings.py
│   │   │   ├── retrieval.py
│   │   │   └── assistant.py
│   │   │
│   │   ├── workers/
│   │   ├── schemas/
│   │   └── main.py
│   │
│   ├── tests/
│   ├── alembic/
│   └── requirements.txt
│
├── frontend/
│   ├── app/
│   ├── components/
│   ├── hooks/
│   ├── lib/
│   └── types/
│
├── scripts/
│   ├── inspect_instagram_export.py
│   └── import_instagram_export.py
│
├── docker/
├── docs/
├── docker-compose.yml
├── .env.example
├── .gitignore
└── README.md

This is the target structure. Adjust it where necessary, but preserve logical separation of responsibilities.

---

# 17. Development Roadmap

Build incrementally. Do not attempt to implement everything in one pass.

## Phase 1: Foundation and Historical Import (MVP)

Priority: P0

Deliverables:

1. Set up Python backend.
2. Set up PostgreSQL.
3. Configure Docker Compose.
4. Implement database models.
5. Implement Instagram ZIP inspection.
6. Implement saved-post parsing.
7. Implement group-message parsing.
8. Extract shared Instagram URLs.
9. Implement URL normalisation.
10. Implement deduplication.
11. Implement import reporting.
12. Add tests using representative export fixtures.

Definition of done:

- A real Instagram export can be uploaded.
- Saved-post references are extracted.
- Group chat Instagram URLs are extracted.
- Source metadata is preserved.
- Duplicate URLs are consolidated.
- Import can resume after failure.
- Import results are available in a report.

## Phase 2: Content Extraction Pipeline

Priority: P0

Deliverables:

1. Implement content retrieval adapters.
2. Implement manual media upload.
3. Implement image OCR.
4. Implement video transcription.
5. Implement caption extraction.
6. Implement AI classification.
7. Implement category-specific structured extraction.
8. Implement summaries and tags.
9. Persist extracted information.
10. Implement processing status tracking.

Definition of done:

A Reel, post or carousel can be processed into structured information with a source reference and a validated extraction schema.

## Phase 3: Notion Integration

Priority: P0

Deliverables:

1. Configure Notion integration.
2. Create Content Inbox database.
3. Implement page creation.
4. Implement page updates.
5. Implement duplicate prevention.
6. Implement retries.
7. Add manual sync.
8. Add sync monitoring.

Definition of done:

Successfully processed content appears in Notion with its summary, metadata, structured extraction and original Instagram link.

## Phase 4: Continuous Capture

Priority: P1

Deliverables:

1. Implement authenticated capture endpoint.
2. Build simple capture page.
3. Add Telegram bot as an optional capture interface.
4. Implement automatic queue processing.
5. Add notifications for processing failures.
6. Support multiple capture sources.

Definition of done:

The user can share an Instagram URL with the system and have it processed without manually running an import.

## Phase 5: Dashboard

Priority: P1

Deliverables:

1. Dashboard.
2. Content Inbox.
3. Content detail pages.
4. Import management.
5. Processing status.
6. Manual editing.
7. Bulk operations.
8. Settings.

## Phase 6: Semantic Search and AI Assistant

Priority: P2

Deliverables:

1. pgvector integration.
2. Embedding generation.
3. Text chunking.
4. Hybrid search.
5. Natural language query interface.
6. Grounded responses.
7. Links to original sources.
8. Search filters.

Definition of done:

The user can ask natural language questions about previously collected content and receive answers grounded in stored records.

## Phase 7: Advanced Features

Priority: P3

Potential features:

- Automatic content recommendations.
- Discover recurring interests.
- Cross-reference saved information.
- Generate travel itineraries from saved recommendations.
- Generate reading lists.
- Build shopping comparison lists.
- Create collections automatically.
- Detect conflicting recommendations.
- Generate personal knowledge digests.
- Support additional platforms such as YouTube, TikTok and Pinterest.

---

# 18. MVP Acceptance Criteria

The MVP is not complete until the following tests pass.

### Historical Import

- [ ] Upload a valid Instagram ZIP.
- [ ] Inspect the ZIP without unsafe extraction.
- [ ] Parse saved posts.
- [ ] Parse available collection metadata.
- [ ] Parse group conversations.
- [ ] Extract shared Instagram URLs.
- [ ] Preserve original timestamps.
- [ ] Handle missing fields.
- [ ] Handle malformed JSON.
- [ ] Detect duplicate content.
- [ ] Resume interrupted imports.

### Content Processing

- [ ] Accept an Instagram URL.
- [ ] Validate the URL.
- [ ] Create a content record.
- [ ] Queue asynchronous processing.
- [ ] Extract accessible metadata.
- [ ] Process available images.
- [ ] Process available videos.
- [ ] Transcribe speech where possible.
- [ ] Extract structured information.
- [ ] Classify content.
- [ ] Generate summary and tags.
- [ ] Persist results.
- [ ] Handle inaccessible posts.

### Notion

- [ ] Connect Notion.
- [ ] Create Content Inbox database.
- [ ] Create a page for processed content.
- [ ] Update an existing page.
- [ ] Prevent duplicate pages.
- [ ] Retry failed synchronisations.
- [ ] Preserve original Instagram URL.

### Reliability

- [ ] Background jobs survive API restarts.
- [ ] Failed jobs can be retried.
- [ ] Partial extraction results are retained.
- [ ] Errors are logged.
- [ ] Credentials are not exposed.
- [ ] Imported private data is protected.

---

# 19. Development Instructions for Claude Code / OpenCode

Act as a senior software engineer and technical architect.

Your responsibility is to implement this project incrementally and maintain production-quality engineering standards.

Follow these rules:

1. Do not attempt to implement the entire system in a single coding session.
2. Start by inspecting the existing repository.
3. If a project skeleton already exists, inspect it and reuse useful code.
4. Do not overwrite working code without understanding its purpose.
5. Create a technical implementation plan before each major phase.
6. Break work into small, independently testable tasks.
7. Write tests alongside implementation.
8. Use type hints and strict input validation.
9. Avoid unnecessary abstractions and premature microservices.
10. Use dependency injection where it improves testability.
11. Keep external service integrations behind interfaces.
12. Never hardcode credentials, tokens or provider-specific configuration.
13. Use environment variables for configuration.
14. Document setup and execution instructions.
15. Maintain database migrations.
16. Use structured logging.
17. Handle all external API failures explicitly.
18. Never silently discard imported content.
19. Preserve raw source data when appropriate.
20. Make all processing operations idempotent.
21. Do not fabricate Instagram API capabilities.
22. Verify actual Instagram export structures using supplied fixtures.
23. Do not use browser automation or Instagram scraping as a workaround for restricted access.

> **Amended 2026-11-09 (ADR-023):** the user approved fetching *public* posts with yt-dlp *logged out* (no credentials or cookies), rate-limited with a circuit breaker. Logging in, using session cookies, CAPTCHA bypass and private-account access remain prohibited.

24. Never invent extracted information.
25. Ask for clarification only when an essential implementation decision cannot be made safely.

For external APIs and SDKs, consult their current official documentation rather than relying on outdated assumptions.

## Working Method

For each phase:

1. Explain the implementation approach.
2. List files that need to be created or changed.
3. Implement the feature.
4. Write unit tests.
5. Run tests.
6. Fix failures.
7. Update documentation.
8. Summarise what was implemented.
9. List any outstanding issues.
10. Propose the next logical task.

Maintain a `docs/IMPLEMENTATION_PLAN.md` file and update it as development progresses.

Maintain a `docs/DECISIONS.md` file documenting significant architectural decisions.

Maintain a `docs/KNOWN_LIMITATIONS.md` file documenting platform and provider limitations.

Never mark a feature as completed unless its acceptance criteria have been tested.

---

# 20. First Task: Start Implementation

Do not start building the entire application immediately.

Begin with Phase 1.

Your first assignment:

**Build the Instagram Historical Export Inspection and Import Foundation.**

Specifically:

1. Inspect the repository.
2. Establish the project structure.
3. Set up Docker Compose with PostgreSQL and Redis.
4. Configure FastAPI.
5. Configure SQLAlchemy and Alembic.
6. Implement the initial database models.
7. Build a safe Instagram ZIP inspection utility.
8. Identify saved-post, collection and message export files.
9. Implement a parser for the actual available export structures.
10. Extract Instagram post and Reel URLs.
11. Implement canonical URL normalisation.
12. Implement content deduplication.
13. Add unit tests and sample fixtures.
14. Produce an import report.

Do not begin AI extraction or Notion synchronisation until the historical importer works reliably.

The importer must be designed to process a real user-provided Instagram export, not just an invented sample JSON file.

If the actual export is not available, implement the inspection framework and clearly document which parsing functionality is awaiting real-world fixtures.

**Expected first deliverable:**

A working command:

`python -m scripts.inspect_instagram_export /path/to/instagram-export.zip`

It should report:

- Files discovered.
- Saved posts identified.
- Collections identified.
- Conversations identified.
- Instagram URLs extracted.
- Duplicate URLs detected.
- Unsupported or unrecognised files.
- Potential parsing errors.

A second command should import the extracted references into PostgreSQL.

The first phase should be independently runnable, testable and documented.

**Definition of success:** We can take an actual Instagram export, inspect it, identify the saved content and shared Instagram URLs, and persist the results in our database without losing source information.

---

# 21. Final Product Vision

Lore should eventually become the user's personal knowledge layer across social platforms.

Instagram is only the first source.

The long-term experience should be:

- Save something interesting.
- Share it with Lore.
- Let the system understand it.
- Store the information automatically.
- Ask questions whenever needed.
- Retrieve useful knowledge without revisiting hundreds of saved posts.

The application should optimise for one metric above everything else:

**How easily can the user retrieve useful information they previously discovered and collected?**

Build for that outcome, not simply for the number of posts processed.