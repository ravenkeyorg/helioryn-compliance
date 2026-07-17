# Copyright (c) 2026 Ravenkey LLC dba Helioryn. All rights reserved.
#!/bin/bash
# connect.sh — Connect to the Helioryn server (M4 MacBook Air)
#
# Usage:
#   ./connect.sh stats                  HTTP API (fast, default)
#   ./connect.sh dashboard              SSH TUI (needs terminal)
#   ./connect.sh discover run           HTTP API
#   ./connect.sh --ssh <cmd>            Force SSH mode
#   ./connect.sh                        Interactive SSH session
#
# Environment:
#   HELIORYN_HOST      Server hostname  (default: m4)
#   HELIORYN_USER      SSH username     (default: btaylor)
#   HELIORYN_API_KEY   API key for HTTP mode
#   HELIORYN_API_PORT  API server port  (default: 8765)

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

# --- Configuration ---
HELIORYN_HOST="${HELIORYN_HOST:-m4}"
HELIORYN_USER="${HELIORYN_USER:-btaylor}"
HELIORYN_API_PORT="${HELIORYN_API_PORT:-8765}"
# Project root on the server (parent of helioryn/ package dir)
HELIORYN_ROOT="${HELIORYN_ROOT:-~/Projects/helioryn-design}"
# Python package dir (relative to root)
HELIORYN_PKG="helioryn"

# Commands that need SSH (TTY-based, not available via API)
SSH_ONLY_CMDS=" dashboard topic contradictions "

usage() {
    cat <<EOF
Usage: ./connect.sh [options] [command]

Connect to the Helioryn server. Uses HTTP API by default, SSH for TUI commands.

Commands:
  stats           Show database statistics
  sources         List archived sources
  show-source ID  Show source detail
  search QUERY    Search archived content
  queries         List search queries
  entities        List government entities
  claims          List extracted claims
  relationships   List claim relationships (same-claim, contradictions)
  discover run    Run one discovery cycle
  dashboard       Launch TUI dashboard (SSH only)
  topic           Manage narratives (SSH only: list, show, rename, delete)
  rel             Relationship commands (SSH only: contradictions, detect, embed)
  daemon install  One-time: deploy LaunchAgent for 24/7 operation
  daemon status   Check daemon health and last cycle
  daemon logs     Tail the daemon log
  daemon stop     Stop the daemon gracefully

Options:
  --ssh <cmd>     Force SSH mode for any command
  --help          Show this help

Examples:
  ./connect.sh stats                    # HTTP API (instant)
  ./connect.sh discover run             # HTTP API
  ./connect.sh dashboard                # SSH TUI
  ./connect.sh topic list               # SSH: list narratives
  ./connect.sh --ssh sources            # Force SSH for sources
  ./connect.sh                          # Interactive SSH

EOF
    exit 0
}

# --- Parse ---
ssh_mode=false
cmd_args=()

for arg in "$@"; do
    if [ "$arg" = "--help" ]; then
        usage
    elif [ "$arg" = "--ssh" ]; then
        ssh_mode=true
    else
        cmd_args+=("$arg")
    fi
done

SSH_HOST="${HELIORYN_USER}@${HELIORYN_HOST}"
API_URL="http://${HELIORYN_HOST}:${HELIORYN_API_PORT}"
API_KEY="${HELIORYN_API_KEY:-}"

# Load API key: from env, or fetch from server via SSH, or local fallback
if [ -z "$API_KEY" ]; then
    API_KEY=$(ssh -o ConnectTimeout=3 "$SSH_HOST" "cat ~/.helioryn/api.key 2>/dev/null" 2>/dev/null || echo "")
fi
if [ -z "$API_KEY" ] && [ -f "$HOME/.helioryn/api.key" ]; then
    API_KEY=$(cat "$HOME/.helioryn/api.key")
fi

# --- HTTP API call with error handling ---
api_call() {
    local method="$1"
    local path="$2"
    shift 2
    local response
    if [ "$method" = "GET" ]; then
        response=$(curl -s -w "\n%{http_code}" -H "X-API-Key: $API_KEY" "${API_URL}${path}" "$@" 2>&1)
    else
        response=$(curl -s -X "$method" -w "\n%{http_code}" -H "X-API-Key: $API_KEY" "${API_URL}${path}" "$@" 2>&1)
    fi
    local http_code
    http_code=$(echo "$response" | tail -1)
    local body
    body=$(echo "$response" | sed '$d')

    if [ "$http_code" = "000" ] || [ -z "$http_code" ]; then
        echo "Error: Cannot reach Helioryn API at ${API_URL}${path}"
        echo "  Is the server running?  ssh ${SSH_HOST} ${HELIORYN_ROOT}/start.sh status"
        echo "  Try starting it:       ssh ${SSH_HOST} ${HELIORYN_ROOT}/start.sh start"
        exit 1
    elif [ "$http_code" = "403" ]; then
        echo "Error: Invalid API key."
        echo "  Get the key from the server: ssh ${SSH_HOST} cat ~/.helioryn/api.key"
        echo "  Then set: export HELIORYN_API_KEY=<key>"
        exit 1
    elif [ "${http_code:0:1}" != "2" ]; then
        echo "Error: API returned HTTP ${http_code}"
        echo "$body"
        exit 1
    fi
    echo "$body"
}

# --- Pretty-print helpers ---
pp_stats() {
    python3 -c "
import sys, json
from datetime import datetime, timezone

def local(ts_str):
    if not ts_str:
        return '-'
    try:
        dt = datetime.fromisoformat(ts_str)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        local = dt.astimezone()
        return local.strftime('%m-%d %I:%M %p')
    except:
        return ts_str[:16]

d = json.load(sys.stdin)
print(f\"Sources:       {d['total_sources']}\")
print(f\"Events:        {d['total_events']}\")
print(f\"Claims:        {d['total_claims']}\")
print(f\"Observations:  {d.get('total_observations', 0)}\")
print(f\"Embeddings:    {d.get('total_embeddings', 0)}\")
print(f\"Relationships: {d.get('total_relationships', 0)} ({d.get('total_repeated_by', 0)} same, {d.get('total_contradictions', 0)} conflicts)\")
print(f\"Queries:       {d['search_queries']}\")
print(f\"Gov entities:  {d['gov_entities']}\")
print(f\"Entities:      {d.get('total_entities', 0)}\")
print(f\"Narratives:    {d.get('total_narratives', 0)}\")
print(f\"Updated:       {d['updated_sources']}\")
print(f\"Ingest rate:   {d.get('rate_1h', 0)}/h  {d.get('rate_24h', 0)}/24h  \")
print(f\"Oldest:        {local(d['oldest_source'])}\")
print(f\"Newest:        {local(d['newest_source'])}\")
"
}

pp_sources() {
    python3 -c "
import sys, json
items = json.load(sys.stdin)
if not items:
    print('(no sources in database)')
    sys.exit(0)
for s in items:
    author = f\" by {s['author'][:20]}\" if s.get('author') else ''
    print(f\"{s['source_id'][:8]}  {s['updated'][:10]}{author}  {s['title'] or '(no title)'}\")
"
}

pp_entities() {
    python3 -c "
import sys, json
items = json.load(sys.stdin)
if not items:
    print('(no entities)')
    sys.exit(0)
for e in items:
    print(f\"[{e['level']:<5}] {e['name']:<40} {e['country']}\")
"
}

pp_queries() {
    python3 -c "
import sys, json
items = json.load(sys.stdin)
if not items:
    print('(no queries)')
    sys.exit(0)
for q in items:
    last = q['last_run'][:13] if q['last_run'] else 'never'
    print(f\"[p{q['priority']:02d}] {last}  {q['text']}\")
"
}

pp_claims() {
    python3 -c "
import sys, json
items = json.load(sys.stdin)
if not items:
    print('(no claims)')
    sys.exit(0)
for c in items:
    print(f\"  [{c['source_id']}] ({c['confidence']}) {c['text'][:80]}\")
"
}

pp_discover_run() {
    python3 -c "
import sys, json
d = json.load(sys.stdin)
if 'detail' in d:
    print(f'Error: {d[\"detail\"]}')
    sys.exit(1)
print(f\"Discovery cycle: {d['ingested']} ingested, {d['skipped']} skipped, {d['errors']} errors\")
"
}

pp_search() {
    python3 -c "
import sys, json
items = json.load(sys.stdin)
if not items:
    print('(no results)')
    sys.exit(0)
for s in items:
    author = f\" by {s['author'][:20]}\" if s.get('author') else ''
    print(f\"{s['source_id'][:8]}  {s['updated'][:10]}{author}  {s['title'] or '(no title)'}\")
"
}

pp_source_detail() {
    python3 -c "
import sys, json
d = json.load(sys.stdin)
print(f\"Source ID:  {d['source_id']}\")
print(f\"URL:        {d['url']}\")
print(f\"Title:      {d['title'] or '(none)'}\")
author = d.get('author') or '(unknown)'
print(f\"Author:     {author}\")
pub = d.get('publish_date') or '(unknown)'
print(f\"Published:  {pub}\")
lang = d.get('language') or '(unknown)'
print(f\"Language:   {lang}\")
canonical = d.get('canonical_url') or '(none)'
print(f\"Canonical:  {canonical}\")
print(f\"First seen: {d['first_seen']}\")
print(f\"Updated:    {d['last_updated']}\")
print(f\"Versions:   {d['versions']}\")
print(f\"Hash:       {d['content_hash']}\")
print(f\"Method:     {d['method']}\")
meta_count = d.get('meta_tags', 0)
print(f\"Meta tags:  {meta_count}\")
"
}

# --- Determine mode ---
cmd="${cmd_args[0]:-}"

# Interactive SSH if no command — land in project root, activate venv
if [ -z "$cmd" ]; then
    echo "Connecting to Helioryn server at ${SSH_HOST}..."
    exec ssh -t "$SSH_HOST" \
        "cd ${HELIORYN_ROOT} && source ${HELIORYN_PKG}/venv/bin/activate && exec \$SHELL"
fi

# Force SSH mode for TUI-only commands (check main cmd and subcmd)
ssh_subcmd="${cmd_args[1]:-}"
if [[ "$SSH_ONLY_CMDS" == *" $cmd "* ]] || { [ -n "$ssh_subcmd" ] && [[ "$SSH_ONLY_CMDS" == *" $ssh_subcmd "* ]]; }; then
    ssh_mode=true
fi

# --- SSH mode ---
if [ "$ssh_mode" = true ]; then
    args=""
    for a in "${cmd_args[@]}"; do
        a_escaped="${a//\'/\'\\\'\'}"
        args="$args '$a_escaped'"
    done
    exec ssh -t "$SSH_HOST" \
        "cd ${HELIORYN_ROOT} && source ${HELIORYN_PKG}/venv/bin/activate && helioryn ${args}"
fi

# --- HTTP API mode ---
case "$cmd" in
    stats)
        api_call GET "/api/stats" | pp_stats
        ;;
    sources|list-sources)
        limit="${2:-20}"
        api_call GET "/api/sources?limit=$limit" | pp_sources
        ;;
    show-source)
        sid="${2:-}"
        if [ -z "$sid" ]; then
            echo "Usage: ./connect.sh show-source <source-id>"
            exit 1
        fi
        api_call GET "/api/sources/$sid" | pp_source_detail
        ;;
    search)
        query="${2:-}"
        if [ -z "$query" ]; then
            echo "Usage: ./connect.sh search <query>"
            exit 1
        fi
        limit="${3:-20}"
        api_call GET "/api/search?q=$query&limit=$limit" | pp_search
        ;;
    queries|query)
        limit="${2:-30}"
        api_call GET "/api/queries?limit=$limit" | pp_queries
        ;;
    entities|entity)
        level="${2:-}"
        limit="${3:-30}"
        if [ -n "$level" ]; then
            api_call GET "/api/entities?level=$level&limit=$limit" | pp_entities
        else
            api_call GET "/api/entities?limit=$limit" | pp_entities
        fi
        ;;
    claims|extract)
        limit="${2:-20}"
        api_call GET "/api/claims?limit=$limit" | pp_claims
        ;;
    relationships|rels|rel)
        limit="${2:-20}"
        api_call GET "/api/relationships?limit=$limit" | python3 -c "
import sys, json
items = json.load(sys.stdin)
if not items:
    print('(no relationships)')
    sys.exit(0)
for r in items:
    src = r['source_claim_id'][:8]
    tgt = r['target_claim_id'][:8]
    print(f\"  [{r['type']}] {src} -> {tgt} (conf={r['confidence']:.3f}) ({r['detected_by']})\")
    print(f\"    src: {r['source_text'][:60]}\")
    print(f\"    tgt: {r['target_text'][:60]}\")
"
        ;;
    observations|obs)
        limit="${2:-20}"
        api_call GET "/api/observations?limit=$limit" | python3 -c "
import sys, json
items = json.load(sys.stdin)
if not items:
    print('(no observations)')
    sys.exit(0)
for o in items:
    ts = o['observed_at'][11:19]
    print(f\"  {ts}  claim={o['claim_id']}  source={o['source_id']}  observer={o['observer']}\")
"
        ;;
    discover)
        sub="${2:-}"
        if [ "$sub" = "run" ]; then
            api_call POST "/api/discover/run" | pp_discover_run
        else
            echo "Usage: ./connect.sh discover run"
            exit 1
        fi
        ;;
    history)
        limit="${2:-10}"
        api_call GET "/api/stats" | python3 -c "
import sys, json
d = json.load(sys.stdin)
print(f\"Sources: {d['total_sources']} total\")
print(f\"Events:  {d['total_events']} total\")
"
        ;;
    daemon)
        sub="${2:-status}"
        HELIORYN_CMD="cd ${HELIORYN_ROOT} && source ${HELIORYN_PKG}/venv/bin/activate && helioryn"
        case "$sub" in
            install)
                echo "Installing Helioryn LaunchAgent..."
                plist="com.helioryn.daemon.plist"
                ssh "$SSH_HOST" "mkdir -p ~/Library/LaunchAgents"
                scp "$SCRIPT_DIR/helioryn/$plist" "$SSH_HOST:~/Library/LaunchAgents/"
                ssh "$SSH_HOST" "launchctl load ~/Library/LaunchAgents/$plist"
                echo "LaunchAgent loaded. Daemon will start at login and run continuously."
                ;;
            status)
                ssh "$SSH_HOST" "${HELIORYN_CMD} status"
                ;;
            stop)
                plist="com.helioryn.daemon.plist"
                ssh "$SSH_HOST" "launchctl unload ~/Library/LaunchAgents/$plist; echo 'Daemon stop signal sent.'"
                ;;
            logs)
                ssh "$SSH_HOST" "tail -f ~/.helioryn/daemon-stdout.log 2>/dev/null || echo 'No daemon log yet'"
                ;;
            *)
                echo "Usage: ./connect.sh daemon {install|status|stop|logs}"
                exit 1
                ;;
        esac
        ;;
    *)
        echo "Unknown command: $cmd"
        echo "Run ./connect.sh --help for usage."
        echo "SSH-only commands (use --ssh): topic, contradictions, daemon"
        exit 1
        ;;
esac
