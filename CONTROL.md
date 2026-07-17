Copyright (c) 2026 Ravenkey LLC dba Helioryn. All rights reserved.

# CONTROL.md — Session State

## Current Status (Jul 04, 2026)

**Project direction:** Grant oversight intelligence tool for OVC, VOCA, DOJ grant managers. Running locally for demo readiness. No GCP/cloud dependencies.

**Model:** `qwen2.5:7b` (Q4_K_M, ~4.5GB) on Ollama (localhost:11434). Switched from 14b for performance on M4 MacBook Air.

**Branch:** `main` at `33d2c5a`. GCP deploy branch deleted.

## What's Running

| Service | Status | Details |
|---------|--------|---------|
| Server | `uvicorn helioryn.server:app` on port 8765 | FastAPI + Jinja2 |
| PostgreSQL | Local at `/tmp` socket | `helioryn_dev` DB |
| Ollama | Port 11434 | `qwen2.5:7b` model loaded |
| Daemon | Background processes | pipeline, score, analyze, interpret, api-ingest, discover |

## DB Stats (from previous seeding)

- 92,997 sources
- 659 claims
- 658 embeddings
- 42 gov_seed sources (CFR, Financial Guide, OIG audits, OVC guidance)

## Key Changes This Session

1. **Landing page** (`/`): Branded hero with feature grid, animated glow, CTA buttons. Redirects to `/chat` if logged in.
2. **Auth pages redesigned**: Split-screen layout, gradient buttons, feature sidebar, unified `auth.css`.
3. **Error page**: Standalone polished error template (no longer extends base.html).
4. **Chat UI improvements**:
   - Preset question chips (6 domain-specific questions) in the empty state
   - Typing indicator with avatar + bouncing dots
   - Message fade-in animations
   - Removed old `typing-dot` CSS, replaced with `chat-typing` + `typing-bubbles`
5. **Unauthenticated redirect**: `/chat` now redirects to `/login?next=/chat` instead of rendering an empty page.
6. **Ollama warm-up**: `_warm_ollama()` sends a background generate request at startup (`keep_alive: 10m`) to avoid cold-start latency.
7. **Exception handlers**: 404 and 500 handlers now render the new `error.html`.
8. **CI workflow runs**: All 5 runs deleted from GitHub, no workflow files in repo.
9. **GCP deletion**: All cloud resources deleted (Cloud Run, Artifact Registry, Cloud SQL, Secrets). No GCP references remain.

## Known Issues
- Chat messages from API don't persist `messages` array in `PUT /api/sessions/{id}` — messages are stored but initial GET returns empty `[]`
- Cold-start on first chat request is ~63s even with warm-up (model loads fully on first generate)
- Only one admin user (`admin`/`admin`)
- FAC API DEMO_KEY rate-limited for bulk seeding

## Next Steps (Suggested)
- Fix session message persistence (messages not saving/loading properly)
- Add more OIG audit reports as they publish
- Investigate why `PUT /api/sessions/{id}` doesn't persist messages array
