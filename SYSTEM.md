Copyright (c) 2026 Ravenkey LLC. All rights reserved.

# Helioryn System

Evidence-based intelligence infrastructure. Not a chatbot, not a dashboard, not a news aggregator. A provenance-first system that archives sources, extracts claims, detects relationships, tracks observations, and clusters narratives.

## Core Principle

The system does not determine truth. It models evidence. Contradictions are features, not bugs. Every output traces back to a source.

---

## Architecture

### 5-Layer Model

```
Layer 1                    Layer 2                    Layer 3                    Layer 4                    Layer 5
Raw Source ──> Extracted ──> Relationship ──> Observation ──> Narrative
Snapshot       Claims        Graph            Tracking       Clusters
   │               │               │               │               │
   v               v               v               v               v
Immutable     Atomic         Epistemic       Temporal       User-facing
archive       assertions     connections     journal        organization
```

| Layer | Name | What it stores | Tables |
|-------|------|----------------|--------|
| 1 | Raw Source | Immutable HTML/text archive with dedup | `source_ingested`, `source_snapshot` |
| 2a | Source Meta | Author, publish date, language, canonical URL, JSON-LD, feeds | JSONB on snapshot |
| 2b | Claims | Atomic assertions (sentence-level) | `claim` |
| 3a | Entities | Named entities extracted from claims | `entity`, `claim_entity` |
| 3b | Same-claim | Embedding similarity (pgvector) detecting repeated claims | `claim_embedding`, `claim_relationship` |
| 3c | Contradictions | Conflicting claims across sources | `claim_relationship` |
| 4 | Observations | Every time a claim was observed from a source (append-only) | `claim_observation` |
| 5 | Narratives | Topic clusters grouping related claims | `narrative`, `narrative_claim` |

### Infrastructure

```
┌─────────────────────────────────────────────────────────────────────┐
│                         MacBook M4 (m4)                            │
│                                                                     │
│  PostgreSQL 16 ──────── Event Store ──────── FastAPI :8765          │
│  (brew)                 (asyncpg)            (uvicorn)              │
│       │                       │                     │              │
│       │                  ┌────┴────┐           ┌────┴────┐        │
│       │                  │  CLI    │           │connect.sh│        │
│       │                  │(typer)  │           │(Linux)   │        │
│       │                  └─────────┘           └─────────┘        │
│       │                                                           │
│  Ollama ── llm contradiction detection                             │
│  (brew)   (llama3.2:3b, localhost:11434)                           │
│       │                                                           │
│  SearXNG ── web search (Docker or native)                          │
│  (:8888)                                                           │
└─────────────────────────────────────────────────────────────────────┘
```

---

## Data Flow

### Ingestion

```
SearXNG search  ──> HTTP fetch  ──> Readability normalize  ──> PostgreSQL store
   (query)          (raw HTML)       (extract: title, author,      (event + snapshot)
                                      date, meta, body text)
```

- **Dedup**: SHA-256 hash of raw text. Same content → skip.
- **Source metadata**: Author, publish date, language, canonical URL, all `<meta>` tags, JSON-LD structured data, RSS/Atom feed links.
- **Throttling**: Configurable `fetch_delay` (default 5s) between fetches.
- **Event sourcing**: Every ingest creates a `source_ingested` row and upserts `source_snapshot` via a trigger.

### Claim Extraction

```
Source snapshot ──> Sentence split ──> Entity regex ──> Claim + Entity store
                    (spaCy-style)     (capitalized       (observation
                    3-sentence window  phrase detection)   created too)
```

- Splits body text into sentences. Each sentence = one claim.
- Captures context (±3 sentences around the claim).
- Extracts entities: regex for capitalized phrases (names, orgs, locations).
- Each claim gets an observation: "seen at time T from source S."
- Entities upserted by name (unique index), linked via join table.

### Discovery Engine

```
Government entity DB  ──> Generate queries  ──> Priority queue  ──> Ingest
  (42 entities:          (42 × 4 AI terms     (scheduling by       (search → fetch
   countries, states,     = 168 queries)       priority +           → normalize
   cities, agencies)                            last_run)           → archive)
```

- Seed data: 12 countries, 6 international orgs, 7 states, 11 cities, 6 agencies.
- AI terms appended to entity names: "AI policy", "artificial intelligence", "AI regulation", "AI strategy".
- Query pool with priority (0-100, lower = higher priority) and interval (minutes between re-runs).

### Same-Claim Detection

```
Claims ──> sentence-transformers ──> pgvector HNSW index ──> Similarity search
           (all-MiniLM-L6-v2,       (384-dim embeddings,     (per-claim, top-k
            384-dim embeddings)      cosine distance)          neighbors > 0.88)
```

- Every claim gets a 384-dimensional embedding vector.
- HNSW index enables fast approximate nearest-neighbor search.
- For each claim, find top-100 similar claims above 0.88 cosine similarity.
- Creates `repeated_by` relationships → "claim A reports the same thing as claim B."

### Contradiction Detection

Three-tier approach (progressive):

| Stage | Method | Quality | Status |
|-------|--------|---------|--------|
| 1 | Rule-based: compare numbers, detect negation | Low (noisy) | ✅ built |
| 2 | Embedding pre-filter: compare only if high similarity | Medium | ✅ built |
| 3 | LLM verification: ask an LLM to judge | High | 🔜 building |

### Narrative Clusters

```
All claim texts ──> TF-IDF vectorize ──> LDA topic model ──> Assign claims to topics
                   (max 1000 features)   (k topics,           (weight > 0.1)
                    stop words removed)    sklearn)
```

- Unsupervised topic modeling via Latent Dirichlet Allocation.
- Each topic is labeled by its top 8 most important terms.
- Each claim assigned to the topic(s) where its weight exceeds 0.1.
- Topics are human-interpretable: "EU AI Act", "India AI governance", etc.

---

## CLI Commands

```bash
helioryn ingest url <url>              # Ingest a single URL
helioryn ingest file <path>            # Ingest from a local file
helioryn ingest stdin                  # Ingest from stdin
helioryn ingest run                    # Full pipeline: search → fetch → normalize → archive
helioryn ingest watch                  # Run on a schedule

helioryn extract source <uuid>         # Extract claims from one source
helioryn extract all                   # Extract claims from all unprocessed sources
helioryn extract list                  # List extracted claims

helioryn discover seed                 # Seed entities + generate queries
helioryn discover run                  # Run one discovery cycle
helioryn discover watch                # Run discovery on a schedule

helioryn rel embed                     # Generate embeddings for unembedded claims
helioryn rel detect                    # Detect same-claims (repeated_by)
helioryn rel contradictions            # Detect contradictions (rule + LLM)
helioryn rel list                      # List relationships
helioryn rel similar <claim_id>        # Find similar claims
helioryn rel narratives                # Detect narrative clusters (LDA)
helioryn rel narratives-list           # List narratives
helioryn rel clean-entities            # Remove noise entities
helioryn rel clean-html                # Remove HTML-boilerplate claims
helioryn rel clear-contradictions      # Clear old contradictory relationships

helioryn query list                    # List search queries
helioryn query add <text>              # Add a manual query

helioryn entity list                   # List government entities
helioryn entity search <query>         # Search extracted entities
helioryn entity list-all               # List all extracted entities (ranked)

helioryn stats                         # Database statistics
helioryn history                       # Recent ingest runs
helioryn status                        # Daemon status
helioryn dashboard                     # TUI dashboard (Textual)
helioryn serve                         # FastAPI server
```

---

## API Endpoints

All endpoints (except `/api/health`) require `X-API-Key` header.

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/health` | GET | Health check |
| `/api/stats` | GET | Full statistics (sources, claims, embeddings, relationships, rate) |
| `/api/sources` | GET | List sources (limit param) |
| `/api/sources/{id}` | GET | Source detail with metadata |
| `/api/search?q=` | GET | Search archived content by text |
| `/api/claims` | GET | List claims (optional `source_id` filter) |
| `/api/observations` | GET | List observations (optional `claim_id`/`source_id` filter) |
| `/api/entities` | GET | List government entities (optional `level` filter) |
| `/api/entities/search?q=` | GET | Search extracted claim entities |
| `/api/entities/top` | GET | Top entities by claim count |
| `/api/entities/claims` | GET | Entities for a specific claim |
| `/api/queries` | GET | List search queries |
| `/api/discover/run` | POST | Trigger a discovery cycle |
| `/api/relationships` | GET | List claim relationships |
| `/api/relationships/similar/{id}` | GET | Find similar claims to a claim |
| `/api/narratives` | GET | List narratives |
| `/api/narratives/{id}` | GET | Claims in a narrative |
| `/api/embeddings/count` | GET | Total embedding count |

---

## Database Schema

### Layer 1 — Source Archive

```
source_ingested (event log)
─────────────────────────────
  event_id         UUID     (PK)
  source_id        UUID     (logical source identity)
  source_url       TEXT     (URL at time of fetch)
  title            TEXT     (extracted title)
  author           TEXT     (extracted author)
  publish_date     TIMESTAMPTZ (extracted date)
  retrieved_at     TIMESTAMPTZ
  raw_text         TEXT     (extracted body text)
  raw_html         TEXT     (full original HTML)
  content_hash     TEXT     (SHA-256 for dedup)
  metadata         JSONB    (head meta, canonical, language, JSON-LD, etc.)
  retrieval_method TEXT
  ingested_at      TIMESTAMPTZ

source_snapshot (latest state, auto-updated by trigger)
──────────────────────────────
  (same columns, plus first_seen_at, last_updated_at)
```

### Layer 2 — Claims

```
claim
───────
  claim_id              UUID
  source_id             UUID    (FK → source_snapshot)
  source_url            TEXT
  extracted_at          TIMESTAMPTZ
  canonical_text        TEXT    (normalized form)
  original_text         TEXT    (verbatim)
  extraction_confidence REAL
  entities              JSONB   (extracted entity names)
  claim_type            TEXT    (fact, opinion, prediction, report)
  context_sentence      TEXT    (surrounding text)
  (GIN index on canonical_text for full-text search)
```

### Layer 3 — Relationships

```
entity
───────
  entity_id     UUID
  name          TEXT    (unique)
  entity_type   TEXT    (person, organization, location, event, concept)
  external_ids  JSONB

claim_entity (join table)
───────────────
  claim_id     UUID  (PK)
  entity_id    UUID  (PK)
  (unique index on name)

claim_embedding
─────────────────
  embedding_id  UUID
  claim_id      UUID  (FK → claim)
  embedding     vector(384) (pgvector, HNSW indexed)
  model_name    TEXT
  created_at    TIMESTAMPTZ

claim_relationship
─────────────────────
  relationship_id   UUID
  source_claim_id   UUID  (FK → claim)
  target_claim_id   UUID  (FK → claim)
  relationship_type TEXT  (supports, contradicts, derived_from, repeated_by, references, evolves_into)
  confidence        REAL
  detected_by       TEXT  (rule, embedding, llm, manual)
  detected_at       TIMESTAMPTZ
  evidence          TEXT
  (unique index on source_claim_id + target_claim_id + relationship_type)
```

### Layer 4 — Observations

```
claim_observation
────────────────────
  observation_id UUID
  claim_id       UUID
  source_id      UUID
  observed_at    TIMESTAMPTZ
  observer       TEXT    (helioryn-ingest, analyst-username)
  context        TEXT
  (append-only, never mutated)
```

### Layer 5 — Narratives

```
narrative
───────────
  narrative_id  UUID
  name          TEXT      (auto-generated from top LDA terms)
  description   TEXT
  top_terms     TEXT[]
  claim_count   INT
  created_at    TIMESTAMPTZ
  is_active     BOOLEAN

narrative_claim
─────────────────
  narrative_id  UUID  (FK → narrative)
  claim_id      UUID  (FK → claim)
  weight        REAL  (topic assignment strength)
```

### Discovery

```
search_query
───────────────
  query_id      UUID
  text          TEXT   (unique)
  language      TEXT
  source        TEXT   (seed, entity, term, human)
  parent_query  TEXT
  priority      INT    (0-100, lower = higher)
  interval_m    INT    (minutes between re-runs)
  last_run      TIMESTAMPTZ
  active        BOOLEAN

government_entity
────────────────────
  entity_id     UUID
  name          TEXT   (unique)
  level         TEXT   (country, state, city, international, agency)
  country       TEXT
  region        TEXT
  search_name   TEXT
  aliases       TEXT[]
  active        BOOLEAN
  discovered_by TEXT
  last_searched TIMESTAMPTZ
```

---

## Running the System

### Server (Mac M4)

```bash
./start.sh              # Start everything (PG check, migrations, SearXNG, discovery daemon, API)
./start.sh restart      # Restart
./start.sh stop         # Stop
./start.sh status       # Check status
./start.sh logs         # View logs
```

### Client (Linux)

```bash
./connect.sh stats       # Show statistics
./connect.sh sources     # List sources
./connect.sh show-source <id>  # Source detail
./connect.sh search <q>  # Search content
./connect.sh claims      # List claims
./connect.sh rels        # List relationships
./connect.sh observations # List observations
./connect.sh entities    # List government entities
./connect.sh queries     # List search queries
./connect.sh discover run # Trigger discovery
./connect.sh dashboard   # TUI dashboard (SSH)
```

---

## Key Design Decisions

1. **Start coarse, refine later.** Sentence-level claims are noisy. The graph works at any granularity. Split/merge as extraction improves.

2. **Event sourcing over mutation.** Source snapshots are materialized from the event log via a trigger. Observations are append-only. No data is ever deleted from the event log.

3. **Progressive intelligence.** Each layer improves over time:
   - Claims: sentence split → semantic chunking → LLM extraction
   - Entities: regex → spaCy NER → entity resolution
   - Contradictions: rule → embedding → LLM
   - Narratives: LDA → manual curation → supervised classification

4. **Confidence is emergent.** Claim confidence comes from extraction method and source agreement. Relationship confidence comes from detection method and supporting evidence. No single model score determines truth.

5. **No paid APIs, no cloud services.** All models run locally (sentence-transformers, Ollama). All data stays in PostgreSQL on the Mac.
