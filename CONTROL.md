Copyright (c) 2026 Ravenkey LLC. All rights reserved.

# CONTROL.md — Session State

## Current Status (Jun 30, 2026 — Session 2)

**Project direction:** Grant oversight intelligence tool for OVC, Navaa, DOJ grant managers (NOT grantees). Standalone project (not Ravenkey MSP). Phase 1 = read-only expert with deep citations. Phase 2 = document editing tools.

**Model:** `qwen2.5:14b` (Q4_K_M, ~8.5GB) on Ollama. Previous models (`llama3.2:3b`, `qwen2.5:7b`) removed.

**Branch:** Working directory (uncommitted)

## Phase A Data Seeded (today)

| Source | Items | Method |
|--------|-------|--------|
| DOJ OIG Reports | 12 (5 original + 7 new) | PDF download via `scripts/seed_gov_data.py` |
| 2 CFR Part 200 (Uniform Guidance) | 1 full part | Cornell LII HTML via `seed_gov_data.py` |
| 28 CFR Part 94 (VOCA Regs) | 22 sections | Cornell LII HTML via `seed_gov_data.py` |
| DOJ Grants Financial Guide | 6 pages | OJP.gov HTML via `seed_gov_data.py` |
| FAC Findings | 1 (rate-limited, DEMO_KEY) | FAC.gov API |

**DB:** 159 total sources, 42 gov_seed (was 122 / 35 before)

## What Was Built This Session

1. **`VISION.md`** — Project vision document: grant oversight intelligence tool, target users, training strategy, model roadmap, design principles
2. **`DEFERRED_DECISIONS.md`** — Deferred architecture decisions from `chatgpt-prompt.md` (Qdrant, BGE-M3, fine-tuning, state manuals, chunk strategy)
3. **`llm.py`** — Provider abstraction: `OllamaProvider` + `OpenCodeGoProvider` + `create_llm()` factory
4. **`config.py`** — Updated: `LLMConfig`, `OpenCodeConfig`, TOML parsing for `[llm]` and `[opencode]`
5. **`helioryn.toml`** — Updated: `[llm]` section with provider + model, `[opencode]` section
6. **`rag.py`** — Rewritten: uses LLM provider, OVC/VOCA system prompt with regulation vs guidance distinction, context cap 15 (was 10), abstention min_avg_score 0.40
7. **`store.py`** — Fixed keyword search: `_extract_search_terms()` extracts proper nouns + keywords instead of ILIKE on full question
8. **`verify_rag.py`** — Thresholds: verified ≥ 0.65, plausible ≥ 0.40
9. **`seed_gov_data.py`** — Extended with CFR + Financial Guide seed functions, more OIG report URLs
10. **Model upgrade**: `ollama pull qwen2.5:14b`, removed `llama3.2:3b` + `qwen2.5:7b`

## Infrastructure

- **Local server**: `uvicorn helioryn.server:app` on port 8765 (start with `source .venv/bin/activate && uvicorn helioryn.server:app --host 0.0.0.0 --port 8765`)
- **PostgreSQL**: Local at `/tmp`, database `helioryn_dev`
- **Ollama**: Port 11434 with `qwen2.5:14b` only
- **Embedding model**: `all-MiniLM-L6-v2` (384-dim, loaded on first use)

## How to Start

```bash
cd ~/Projects/helioryn-compliance
source .venv/bin/activate
uvicorn helioryn.server:app --host 0.0.0.0 --port 8765
```

## Key Files Changed This Session

| File | Change |
|---|---|
| `VISION.md` | **NEW** — Project vision document |
| `DEFERRED_DECISIONS.md` | **NEW** — Deferred architecture decisions |
| `src/helioryn/llm.py` | **NEW** — Provider abstraction (Ollama + OpenCode Go) |
| `src/helioryn/config.py` | Added `LLMConfig`, `OpenCodeConfig`; model default `qwen2.5:14b` |
| `src/helioryn/rag.py` | Use LLM provider; regulation vs guidance in system prompt |
| `src/helioryn/store.py` | `_extract_search_terms()` for keyword search fix |
| `src/helioryn/server.py` | Pass `config=config` to `answer_question()` |
| `src/helioryn/verify_rag.py` | Lowered thresholds (verified 0.65, plausible 0.40) |
| `scripts/seed_gov_data.py` | Added CFR + Financial Guide seed functions; more OIG URLs |
| `helioryn.toml` | Model `qwen2.5:7b` → `qwen2.5:14b`; added `[llm]` + `[opencode]` sections |

## Known Issues
- FAC API DEMO_KEY rate-limited — need registered api.data.gov key for production seeding
- OpenCode Go API (`api.opencode.ai`) returns "Not Found" — provider is wired but non-functional
- System prompt's regulation vs guidance distinction added but not validated against real CFR queries yet
- Verification thresholds lowered (0.65/0.40) — may produce false positives with larger models

## Next Steps
- Register api.data.gov key for FAC API
- Validate 28 CFR 94 answers (question: "What are VOCA allowable costs?")
- Add more OIG reports as they publish (current: 13 OIG sources)
- Build continuous ingestion via API source pattern
- Add document editing tools (Phase 2)

## Session 2 Changes (Jun 30, 2026)

### Data Quality Fix (OIG Reports)
**Root cause:** 4 reports had wrong PDF URLs — titles described OVC/VOCA reports but PDFs were about unrelated topics (BOP FISMA, OCDETF, USAx Axon, Misconduct). 2 more URLs returned 404. 3 titles mislabeled content.

**Fixed:**
- **Removed 6 bad entries** from `OIG_URLS` in `seed_gov_data.py`:
  - `25-033.pdf` (USAx Axon → was labeled "Performance Progress Report")
  - `25-077.pdf` (Misconduct → was labeled "Compensation Program Grants")
  - `26-032.pdf` (BOP FISMA → was labeled "Compensation Program Audit")
  - `26-018.pdf` (OCDETF → was labeled "Performance Measurement Audit")
  - `25-102.pdf` (404 Not Found)
  - `25-127.pdf` (404 Not Found)
- **Fixed 3 misleading titles** in DB + seed script:
  - `24-055.pdf`: "DOJ Grants Financial Guide Compliance" → "Arizona DPS — OJP Victim Assistance Grant Audit"
  - `22-047.pdf`: "OVC Grantee Monitoring" → "Red Wind Consulting — OVW Cooperative Agreement Audit"
  - `21-069.pdf`: "VOCA Grant Administrative Costs" → "JustGrants Transition Impact Issue Alert"
- **Added 3 real OVC/VOCA OIG reports**:
  - `26-038.pdf`: "Puerto Rico DOJ — OVC Victim Compensation Grant Audit" (victim comp grantee audit)
  - `26-047.pdf`: "Virginia DSS — VOCA Subaward Administration Audit" (subrecipient monitoring, time/effort)
  - `26-048.pdf`: "Nebraska — OJP Victim Assistance Subrecipient Monitoring Risk Assessment"
- **Total:** 16 → 13 OIG sources (all correctly title-matched)

### Frontend Crash Fix (chat.js/chat.html)
**Root cause:** If Alpine.js crashed during `sendMessage()`, the form's `@submit.prevent` stopped working. Next button click or Enter key caused native form submission → page reload → messages lost, but loading stayed true from the crashed promise.

**Fixed:**
- Changed `<form>` to `<div>` in `chat.html` — no native submission possible
- Changed submit button from `type="submit"` to `type="button"` with explicit `@click="sendMessage()"`
- Added `try/finally` in `sendMessage()` to always reset `this.loading = false`
- Added `Array.isArray(this.messages)` guards before `push()` in catch blocks
- Added 5-minute loading timeout (`_loadingTimer`) as safety net

## License & Copyright

All code is **Copyright (c) 2026 Ravenkey LLC. All rights reserved.** — proprietary, All Rights Reserved.
