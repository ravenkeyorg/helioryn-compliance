Copyright (c) 2026 Ravenkey LLC dba Helioryn. All rights reserved.

# Deferred Architecture Decisions

This file captures design ideas from `chatgpt-prompt.md` and other sources that were evaluated and deferred. Revisit when there's a concrete reason to change from the current approach.

---

## 1. Vector Store: Qdrant

**Proposal (from chatgpt-prompt.md):** Store embeddings in Qdrant instead of pgvector.

**Current:** pgvector (PostgreSQL). Works now. Single database to manage. No extra service to deploy, configure, back up, or monitor.

**When to revisit:**
- If the embedding count exceeds 500K and query latency becomes a problem (pgvector HNSW handles ~1M vectors fine on M4)
- If we need hybrid search with BM25 + dense vectors (pgvector has limited hybrid search support)
- If we need advanced filtering (payload fields, geo queries, etc.)

**Verdict:** Stick with pgvector for now. Operational simplicity matters more than theoretical performance at our scale.

---

## 2. Embedding Model: BGE-M3

**Proposal (from chatgpt-prompt.md):** Use BGE-M3 or nomic-embed-text.

**Current:** `all-MiniLM-L6-v2` (384-dim, ~80MB). Fast, well-tested, works for English-only text.

**nomic-embed-text:** ~274MB, 768-dim, better for domain-specific text. Worth considering if we upgrade.

**BGE-M3:** ~2.2GB, 1024-dim, multilingual. OVC/VOCA is 99% English. The 2.2GB footprint competes with qwen2.5:14b's ~8.5GB on 16GB RAM. Not worth the memory tradeoff.

**When to revisit:**
- If we encounter cases where all-MiniLM fails to find relevant documents (semantic gaps)
- If we need multilingual support for Spanish/indigenous language VOCA materials
- After a hardware upgrade (32GB+)

**Verdict:** Stick with all-MiniLM-L6-v2. Nomic-embed-text is a reasonable next step if semantic search quality needs improvement.

---

## 3. Fine-Tuning Priority

**Proposal (from chatgpt-prompt.md):** Prefer RAG over fine-tuning.

**Current:** RAG-first, fine-tuning deferred (matching the prompt's recommendation despite earlier discussion).

**When to revisit:**
- If the model consistently hallucinates despite clean RAG results
- If the model fails to follow the OVC/VOCA-specific citation format reliably
- If we need the model to "know" compliance patterns without depending on retrieval
- After we have 1000+ high-quality Q&A pairs for training

**Verdict:** RAG-first. Fine-tuning is a Phase 2+ optimization, not a Phase 1 requirement.

---

## 4. Chunk Strategy

**Proposal (from chatgpt-prompt.md):** 800–1200 tokens with overlap.

**Current:** The existing seed script (`seed_gov_data.py`) stores up to 20K chars per source and extracts claims as sentence-level sections. The RAG pipeline (`rag.py`) sends up to 15 chunks of context to the LLM. No explicit chunking strategy exists beyond the claim extraction pipeline.

**When to revisit:**
- When adding a dedicated document chunker (for PDF/HTML ingestion)
- If context quality degrades from documents being too large

**Verdict:** Accept current approach for now. Implement proper chunking when we add a dedicated document ingestor beyond the seed script.

---

## 5. State VOCA Manuals

**Proposal (from chatgpt-prompt.md):** Ingest state VOCA manuals (ND, OR, MO, OK, VA, etc.)

**Current:** Not ingested. Each state manual requires manual discovery, format-specific parsing (many are PDF), and per-state maintenance.

**When to revisit:**
- After federal sources (CFR, Financial Guide, OIG, FAC) are fully seeded
- If a specific state manual is requested by a user (Navaa)
- If the model's answers consistently miss state-level policy nuance

**Verdict:** Lowest priority for Phase A. Federal sources cover the core compliance knowledge.

---

## 6. Citation Format for Regulations

**Proposal (from chatgpt-prompt.md):** Cite exact document, section, and effective date.

**Current:** OIG reports cite recommendation numbers. For eCFR content (to be added), citation will include title, part, section, and effective date.

**When to revisit:** When regulation content is ingested and the citation format can be validated against real user queries.

**Verdict:** Accepted. Will implement for eCFR ingestion.

---

## 7. Regulation vs Guidance Distinction

**Proposal (from chatgpt-prompt.md):** System prompt should distinguish binding regulations from non-binding guidance.

**Current:** The system prompt in `rag.py` does not explicitly distinguish regulation vs guidance. This is a prompt improvement that costs nothing to implement.

**When to revisit:** Immediately — this is a prompt-level change, not architectural.

**Verdict:** Should be added to the system prompt in `rag.py` when regulation content is seeded. No need to defer.
