# Copyright (c) 2026 Ravenkey LLC. All rights reserved.
#!/bin/bash
# start.sh — Bring up the Helioryn server on macOS (M4 MacBook Air)
#
# Usage:
#   ./start.sh              Start all services
#   ./start.sh status       Check if services are running
#   ./start.sh stop         Stop discovery daemon
#   ./start.sh restart      Stop + start
#   ./start.sh logs         Tail the startup log
#
# Environment:
#   HELIORYN_CONFIG   Config file (default: helioryn.toml)
#   DISCOVER_INTERVAL Discovery interval in minutes (default: 60)
#   HELIORYN_PATH     Path to helioryn project (default: dir of this script)

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
HELIORYN_PATH="${HELIORYN_PATH:-$SCRIPT_DIR/helioryn}"
CONFIG_FILE="${HELIORYN_CONFIG:-helioryn.toml}"
PID_FILE="/tmp/helioryn-discover.pid"
LOG_FILE="/tmp/helioryn-start.log"
DISCOVER_INTERVAL="${DISCOVER_INTERVAL:-60}"

# Ensure Homebrew tools are in PATH (macOS non-interactive shells)
export PATH="/opt/homebrew/bin:/opt/homebrew/sbin:/usr/local/bin:$PATH"

log() { echo "[$(date '+%H:%M:%S')] $*" | tee -a "$LOG_FILE"; }
err() { echo "[$(date '+%H:%M:%S')] ERROR: $*" | tee -a "$LOG_FILE" >&2; }

cd "$HELIORYN_PATH"

API_PID_FILE="/tmp/helioryn-api.pid"

status() {
    local code=0
    # PostgreSQL
    if pg_isready -q 2>/dev/null; then
        echo "PostgreSQL:    running"
    else
        echo "PostgreSQL:    STOPPED"
        code=1
    fi
    # SearXNG
    if docker ps --format '{{.Names}}' 2>/dev/null | grep -q '^helioryn-searxng$'; then
        echo "SearXNG:       running"
    else
        echo "SearXNG:       STOPPED"
        code=1
    fi
    # Discovery daemon
    if [ -f "$PID_FILE" ] && kill -0 "$(cat "$PID_FILE")" 2>/dev/null; then
        echo "Discovery:     running (PID $(cat "$PID_FILE"))"
    else
        echo "Discovery:     STOPPED"
        code=1
    fi
    # API server
    if [ -f "$API_PID_FILE" ] && kill -0 "$(cat "$API_PID_FILE")" 2>/dev/null; then
        echo "API server:    running (PID $(cat "$API_PID_FILE"))"
    else
        echo "API server:    STOPPED"
        code=1
    fi
    # Database
    if psql helioryn_dev -c "SELECT 1" 2>/dev/null >/dev/null; then
        echo "Database:      connected"
    else
        echo "Database:      NOT CONNECTED"
        code=1
    fi
    return $code
}

stop() {
    for pf in "$API_PID_FILE" "$PID_FILE"; do
        if [ -f "$pf" ]; then
            local pid
            pid="$(cat "$pf")"
            if kill -0 "$pid" 2>/dev/null; then
                log "Stopping process (PID $pid)..."
                kill "$pid" 2>/dev/null || true
                wait "$pid" 2>/dev/null || true
            fi
            rm -f "$pf"
        fi
    done
    log "All processes stopped."
}

case "${1:-start}" in
    start)
        # Ensure script is running on macOS
        if [ "$(uname)" != "Darwin" ]; then
            err "start.sh is designed for macOS (M4 MacBook Air). Current OS: $(uname)"
            err "Use connect.sh from a remote machine to connect to the Mac server."
            exit 1
        fi

        log "=== Helioryn System Start ==="

        # ---- 1. PostgreSQL ----
        log "Starting PostgreSQL..."
        brew services start postgresql@16 2>/dev/null || true
        for i in $(seq 1 30); do
            if pg_isready -q 2>/dev/null; then
                log "PostgreSQL is ready."
                break
            fi
            sleep 1
        done
        if ! pg_isready -q 2>/dev/null; then
            err "PostgreSQL failed to start. Check: brew services restart postgresql@16"
            exit 1
        fi

        # Ensure postgres role for current user
        if ! psql -c "SELECT 1" &>/dev/null; then
            log "Creating PostgreSQL role for $(whoami)..."
            createuser -s "$(whoami)" 2>/dev/null || true
        fi

        # Create database if missing
        psql -lqt 2>/dev/null | cut -d \| -f 1 | grep -qw helioryn_dev || createdb helioryn_dev
        log "Database helioryn_dev ready."

        # ---- 2. SearXNG ----
        log "Starting SearXNG..."
        if docker ps --format '{{.Names}}' 2>/dev/null | grep -q '^helioryn-searxng$'; then
            log "SearXNG container already running."
        elif docker ps -a --format '{{.Names}}' 2>/dev/null | grep -q '^helioryn-searxng$'; then
            docker start helioryn-searxng
            log "SearXNG container started from existing."
        else
            log "Creating SearXNG container..."
            docker run -d \
                --name helioryn-searxng \
                -p 8888:8080 \
                -v "$(pwd)/searxng-conf:/etc/searxng:rw" \
                searxng/searxng
            log "SearXNG container created and started."
        fi

        # Wait for SearXNG to serve JSON
        log "Waiting for SearXNG to respond..."
        for i in $(seq 1 30); do
            if curl -s -o /dev/null -w "%{http_code}" "http://localhost:8888/search?q=test&format=json" 2>/dev/null | grep -q 200; then
                log "SearXNG is ready."
                break
            fi
            sleep 2
        done

        # ---- 3. Database Migrations ----
        log "Running migrations..."
        for f in migrations/*.sql; do
            log "  Applying $(basename "$f")..."
            psql helioryn_dev -q -f "$f" 2>&1 | tee -a "$LOG_FILE" | tail -1 || true
        done
        log "Migrations complete."

        # ---- 4. Activate venv ----
        if [ ! -f venv/bin/activate ]; then
            err "Virtual environment not found at venv/. Run: python3 -m venv venv && pip install -e ."
            exit 1
        fi
        source venv/bin/activate

        # ---- 5. Seed Discovery Engine ----
        log "Seeding discovery engine..."
        helioryn discover seed -c "$CONFIG_FILE" 2>&1 | tee -a "$LOG_FILE"
        log "Seed complete."

        # ---- 6. Start Discovery Daemon ----
        log "Starting discovery daemon (every ${DISCOVER_INTERVAL}m)..."
        nohup helioryn discover watch --interval "$DISCOVER_INTERVAL" -c "$CONFIG_FILE" \
            >> "$LOG_FILE" 2>&1 &
        echo $! > "$PID_FILE"
        log "Discovery daemon started (PID $(cat "$PID_FILE"))."

        # ---- 7. Start API Server ----
        log "Starting API server..."
        nohup helioryn serve -c "$CONFIG_FILE" >> "$LOG_FILE" 2>&1 &
        echo $! > "$API_PID_FILE"
        log "API server started (PID $(cat "$API_PID_FILE"))."

        log "=== Helioryn system is running ==="
        log "Connect from another machine:  ./connect.sh"
        log "Check status:                  ./start.sh status"
        log "View logs:                     ./start.sh logs"
        ;;

    status)
        status
        ;;

    stop)
        stop
        ;;

    restart)
        stop
        sleep 2
        exec "$SCRIPT_DIR/start.sh" start
        ;;

    logs)
        tail -f "$LOG_FILE"
        ;;

    *)
        echo "Usage: $0 {start|stop|status|restart|logs}"
        exit 1
        ;;
esac
