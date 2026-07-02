Copyright (c) 2026 Ravenkey LLC. All rights reserved.

# AGENTS.md

Read by AI coding agents on startup. Re-read this + CONTROL.md every 5–10 minutes.

## Project Identity

Helioryn is an **evidence-based intelligence infrastructure** platform. NOT a chatbot, generic AI assistant, analytics dashboard, or news aggregator. Core value: provenance, traceability, contradiction visibility, temporal evolution, and structured evidence relationships.

See `design-doc.md` for the full architecture vision. Architecture docs (1- through 7-) are authoritative for build order; the priority table in `4-from-ingest-to-intelligence.md` governs all decisions.

## Commands

```bash
# install (editable)
pip install -e .

# make helioryn available on PATH from any directory:
ln -sf ~/Projects/helioryn-design/helioryn/venv/bin/helioryn ~/.local/bin/helioryn
# (only needed once; works from anywhere after that)

# test (pytest, asyncio_mode=auto, pythonpath=src)
pytest tests/                           # all unit tests (no external deps needed)
pytest tests/test_hasher.py             # single file
python tests/verify_pipeline.py         # e2e test — requires live PostgreSQL + helioryn schema

# lint
ruff check src/ tests/
ruff format --check src/ tests/

# version info
helioryn stats                          # quick smoke test — requires DB + config

# CLI commands — must run on M4 (PostgreSQL lives there)
ssh btaylor@m4 'source ~/Projects/helioryn-design/helioryn/venv/bin/activate && helioryn stats'
# or via connect.sh:
./connect.sh stats                      # HTTP API mode (fast)
./connect.sh --ssh stats                # SSH mode (runs CLI on M4)
```

## Package & Build

- **Build system**: hatchling (`[build-system]` in pyproject.toml)
- **Package**: `src/helioryn/` — the real Python source. `helioryn/src/helioryn/` is a symlink to `src/helioryn/` — always in sync.
- **Entrypoints**: `helioryn.cli:app` (Typer CLI), `helioryn.server:app` (FastAPI, serves on port 8765)
- **Python**: 3.12+, target `py312`
- **Ruff**: line-length=100, select=E,F,I,N,W
- **spaCy**: uses `spacy.blank("en")` with sentencizer only — no model download needed

## Config

Precedence: `-c <path>` flag → `HELIORYN_DATABASE_URL` env var → `helioryn.toml` in CWD.

Key config files:
- `helioryn.toml` (Linux defaults)
- `helioryn.toml.mac` (macOS — /tmp socket, auto_gen queries disabled)
- `searxng-conf/settings.yml`

## Context & Memory

**Each conversation starts fresh.** No cross-session memory. State tracked in `CONTROL.md`:
- Read at start and periodically (every 5–10 min)
- Update after each meaningful stop point
- Do not ask for info already recorded there
- CONTROL.md may contain live deployment state (daemon PIDs, server IPs, DB stats)

## Infrastructure

- **This host (localhost/pop-os)**: client workstation only — code, test, chat. Never deploy services, databases, or containers here.
- **M4** (`ssh btaylor@m4`): master deployment server — PostgreSQL (5432), Redis (6379), helioryn serve, web GUI, discovery daemon, all services.
- **Homeserver** (`ssh://btaylor@homeserver`): Git server only — never deploy code here. Remote: `ssh://btaylor@homeserver:/opt/git-repos/helioryn-design.git`
- **Deployment fallback**: scp directly to M4 if homeserver unreachable.
- **Redis**: launchd-managed on M4 (`com.helioryn.redis`). Optional — degrades gracefully.
- **No cloud/paid APIs** — all models run locally (sentence-transformers, Ollama).

## Git Workflow

- Create a new branch before making changes (e.g. `fix/config-parsing`, `feat/layer3-graph`).
- Commit and push by default after every change. Commits must be concise and descriptive.
- Every new source file includes: `Copyright (c) 2026 Ravenkey LLC. All rights reserved.`
- **Never push to GitHub.** Homeserver is the only remote.
- Pause and confirm before merging into main.

## Review Guidelines

Flag: exposed secrets, missing auth, SQL injection, XSS, N+1 queries, missing indexes, no pagination, blocking I/O in async paths, uncaught exceptions, swallowed errors, missing input validation, copy-pasta, over-engineering.

## Testing Quirks

- `asyncio_mode = "auto"` — test functions can be `async def` directly
- `pythonpath = ["src"]` — tests import from `src/` without install
- Unit tests (e.g. `test_hasher.py`) have no external deps
- `verify_pipeline.py` inserts synthetic data into a live DB — destructive, run only against dev
- Migration order: `001_initial.sql` → `002_claims.sql` → ... → `012_admin_audit.sql` (12 files)
- System has 3 long-running modes: `helioryn serve` (API), `helioryn daemon` (continuous loop), `helioryn discover watch` (schedule)
