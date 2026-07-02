Copyright (c) 2026 Ravenkey LLC. All rights reserved.

# Helioryn — Operational Setup

## Prerequisites

- macOS (Apple Silicon / M-series)
- [Homebrew](https://brew.sh/)
- Python 3.12+
- PostgreSQL 16+
- [Orbstack](https://orbstack.dev/) (optional — lightweight Docker Desktop replacement for SearXNG search)

---

## Quick Start

Open Terminal and run the following:

```bash
# 1. Install dependencies
brew install python@3.12 postgresql@16 orbstack

# 2. Start PostgreSQL
brew services start postgresql@16

# 3. Create the database
createdb helioryn_dev

# 4. Set up Python environment
cd helioryn
python3.12 -m venv venv
source venv/bin/activate
pip install -e .

# 5. Run database migrations
psql helioryn_dev < migrations/001_initial.sql
psql helioryn_dev < migrations/002_claims.sql

# 6. Verify it works
helioryn stats

# 7. Seed discovery engine (governments + AI queries)
helioryn discover seed

# 8. Run one discovery cycle
helioryn discover run
```

If everything is installed correctly, you should see:

```
Total sources:  0
Total events:   0
Total claims:   0
Search queries: 168
Gov entities:   42
Updated:        0
Oldest source:  -
Newest source:  -
```

---

## Configuration

The system looks for a config file at `helioryn.toml` in the current directory.
A Mac-optimized template is provided at `helioryn.toml.mac`.

```toml
[database]
# macOS (Homebrew PostgreSQL listens on /tmp socket):
url = "postgresql://nomadic@/helioryn_dev?host=/tmp"

# Linux (PostgreSQL listens on /var/run/postgresql):
# url = "postgresql://nomadic@/helioryn_dev?host=/var/run/postgresql"

[ingest]
fetcher_timeout = 30.0
user_agent = "Helioryn/0.1"

[ingest.searcher]
type = "searxng"
base_url = "http://localhost:8888"
categories = "general,news"
language = "en"

[ingest.topics]
items = [
    { query = "Arctic infrastructure", interval_minutes = 360, language = "en" }
]
```

### Environment Variables

The database URL can also be set via `HELIORYN_DATABASE_URL`:

```bash
export HELIORYN_DATABASE_URL="postgresql://nomadic@/helioryn_dev?host=/tmp"
```

---

## SearXNG Setup (Optional)

SearXNG provides automated web search for the ingest pipeline. Without it, you can still manually ingest URLs and files.

Orbstack is the recommended container runtime on Mac — it's lighter, faster, and free for personal use. Install it first:

```bash
brew install orbstack
```

Then start SearXNG:

```bash
docker run -d \
  --name helioryn-searxng \
  -p 8888:8080 \
  -v "$(pwd)/searxng-conf:/etc/searxng:rw" \
  searxng/searxng

# Verify it's running:
curl "http://localhost:8888/search?q=test&format=json"
```

On first run, SearXNG generates its config. The JSON API and GET method
are enabled automatically by the template in `searxng-conf/settings.yml`.
If you start from scratch with a fresh container, you may need to add
`json` to the formats list and change `method` to `"GET"` in the
generated `/etc/searxng/settings.yml` inside the container.

---

## Daily Use

### Ingest Content

```bash
# Ingest a single URL
helioryn ingest url "https://example.com/article"

# Ingest from a local file
helioryn ingest file article.txt --url "https://example.com/article"

# Ingest from stdin
cat article.txt | helioryn ingest stdin

# Run the full pipeline (search → fetch → normalize → archive)
helioryn ingest run --topic "Arctic infrastructure" -c helioryn.toml

# Run in daemon mode
helioryn ingest watch --topic "Arctic infrastructure" --interval 360
```

### Extract Claims

```bash
# Extract claims from a specific source
helioryn extract source <source-uuid>

# Extract from all sources without claims
helioryn extract all --limit 100

# List extracted claims
helioryn extract list --limit 10
helioryn extract list --source <source-uuid>
```

### Discovery (Automated Search)

```bash
# Seed government entities and generate queries (one-time)
helioryn discover seed

# View the query pool
helioryn query list --limit 10

# View government entities
helioryn entity list --level country

# Run one discovery cycle (searches 30 queries)
helioryn discover run

# Run discovery continuously (daemon)
helioryn discover watch --interval 60

# Add a manual query
helioryn query add "AI safety" --priority 10
```

### View Data

```bash
# List archived sources
helioryn list-sources --limit 10

# Show full source detail
helioryn show-source <source-uuid>

# Search archived content
helioryn search "permafrost"

# Database statistics
helioryn stats

# Recent ingest runs
helioryn history

# Daemon status
helioryn status
```

### Dashboard

```bash
helioryn dashboard
```

The TUI shows three panels:
- **Totals** — sources, events, claims, oldest/newest dates
- **Activity Log** — color-coded pipeline events (green = ingested, yellow = skipped, red = failed)
- **Recent Sources** — last 20 sources with title, date, URL

Auto-refreshes every 5 seconds.

| Key | Action |
|-----|--------|
| `q` | Quit |
| `r` | Force refresh |

---

## Example Session

```bash
# 1. Ingest a real article
helioryn ingest url "https://en.wikipedia.org/wiki/Arctic"

# 2. Check it was stored
helioryn list-sources

# 3. Extract claims from it
SOURCE_ID=$(helioryn list-sources -l 1 | head -1 | awk '{print $1}')
helioryn extract source "$SOURCE_ID"

# 4. View extracted claims
helioryn extract list --limit 5

# 5. Run the full pipeline
helioryn ingest run --topic "Arctic infrastructure" -c helioryn.toml

# 6. Launch the dashboard
helioryn dashboard
```

---

## Troubleshooting

### PostgreSQL won't start

```bash
brew services restart postgresql@16
# Or check logs:
tail -f /opt/homebrew/var/log/postgresql@16.log
```

### `createdb: command not found`

```bash
brew link --overwrite postgresql@16
export PATH="/opt/homebrew/opt/postgresql@16/bin:$PATH"
```

### `role "nomadic" does not exist`

```bash
# If your macOS username doesn't match the PostgreSQL role:
/opt/homebrew/opt/postgresql@16/bin/createuser -s $(whoami)
createdb helioryn_dev
```

### Database connection failed

```bash
# Test the connection directly:
psql helioryn_dev

# If that works but the CLI doesn't, check the socket path:
ls /tmp/.s.PGSQL.5432
# If it's somewhere else, update helioryn.toml with the correct host=
```

### SearXNG returns 403

The JSON API must be enabled. On a fresh SearXNG container:

```bash
docker exec -it helioryn-searxng sed -i 's/  formats:\n    - html/  formats:\n    - html\n    - json/' /etc/searxng/settings.yml
docker exec -it helioryn-searxng sed -i 's/method: "POST"/method: "GET"/' /etc/searxng/settings.yml
docker restart helioryn-searxng
```

Or use the provided `searxng-conf/settings.yml` from this repo.

### Python module not found

```bash
source venv/bin/activate
pip install -e .
```

### Command not found: helioryn

```bash
source venv/bin/activate
which helioryn
# Should show: .../helioryn/venv/bin/helioryn
```

---

## Project Layout

```
helioryn/
├── helioryn.toml          # Config file (create from .mac or .linux template)
├── helioryn.toml.mac      # macOS-optimized config template
├── README.md              # This file
├── pyproject.toml         # Python package metadata
├── migrations/            # Database migrations
│   ├── 001_initial.sql
│   └── 002_claims.sql
├── src/helioryn/          # Python package
│   ├── cli.py             # CLI commands (typer)
│   ├── store.py           # Database access (asyncpg)
│   ├── models.py          # Pydantic data models
│   ├── hasher.py          # SHA-256 content hashing
│   ├── config.py          # TOML configuration loader
│   ├── log.py             # Structured run logger
│   ├── dashboard.py       # Textual TUI
│   ├── extract/           # Layer 2 claim extraction
│   └── ingest/            # Layer 1 ingest pipeline
│       ├── base.py        # Abstract interfaces
│       ├── searcher/      # Search providers (SearXNG)
│       ├── fetcher/       # HTTP fetcher
│       ├── normalizer/    # Content extraction (trafilatura)
│       └── ingestor/      # Database ingestor
├── tests/                 # Test suite (pytest)
└── venv/                  # Python virtual environment (created by setup)
```
