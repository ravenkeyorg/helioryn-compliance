# Copyright (c) 2026 Ravenkey LLC. All rights reserved.
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

LOG_DIR = Path.home() / ".helioryn"
LOG_FILE = LOG_DIR / "run.log"
PID_FILE = LOG_DIR / "daemon.pid"
STATUS_FILE = LOG_DIR / "daemon.status"


def _ensure_dir():
    LOG_DIR.mkdir(parents=True, exist_ok=True)


class RunEvent:
    run_id: str
    ts: str
    event_type: str
    data: dict

    def __init__(self, event_type: str, run_id: str | None = None, **data):
        self.run_id = run_id or str(uuid4())[:8]
        self.ts = datetime.now(timezone.utc).isoformat()
        self.event_type = event_type
        self.data = data

    def to_line(self) -> str:
        obj = {
            "ts": self.ts,
            "run": self.run_id,
            "event": self.event_type,
            **self.data,
        }
        return json.dumps(obj, default=str)


def emit(event_type: str, run_id: str | None = None, **data):
    _ensure_dir()
    event = RunEvent(event_type, run_id=run_id, **data)
    with open(LOG_FILE, "a") as f:
        f.write(event.to_line() + "\n")


def read_log(n: int = 200) -> list[dict]:
    if not LOG_FILE.exists():
        return []

    with open(LOG_FILE) as f:
        lines = f.readlines()

    events = []
    for line in lines[-n:]:
        line = line.strip()
        if line:
            try:
                events.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return events


def get_runs(limit: int = 10) -> list[dict]:
    events = read_log(5000)
    runs: dict[str, dict] = {}
    for e in events:
        rid = e.get("run", "?")
        if rid not in runs:
            runs[rid] = {"run_id": rid, "events": [], "ts": e.get("ts", "")}
        runs[rid]["events"].append(e)
        if e["event"] == "run_started":
            runs[rid]["started"] = e.get("ts", "")
            runs[rid]["topic"] = e.get("topic", "")
        if e["event"] == "run_completed":
            runs[rid]["completed"] = e.get("ts", "")
            runs[rid]["ingested"] = e.get("ingested", 0)
            runs[rid]["skipped"] = e.get("skipped", 0)
            runs[rid]["errors"] = e.get("errors", 0)

    summaries = []
    for rid, run in runs.items():
        s = {
            "run_id": rid,
            "started": run.get("started", run.get("ts", "")),
            "completed": run.get("completed", ""),
            "topic": run.get("topic", ""),
            "ingested": run.get("ingested", 0),
            "skipped": run.get("skipped", 0),
            "errors": run.get("errors", 0),
            "total_events": len(run["events"]),
        }
        summaries.append(s)

    summaries.sort(key=lambda x: x.get("started", ""), reverse=True)
    return summaries[:limit]


def write_pid():
    _ensure_dir()
    if PID_FILE.exists():
        try:
            old_pid = int(PID_FILE.read_text().strip())
        except (ValueError, OSError):
            old_pid = None
        if old_pid is not None:
            try:
                os.kill(old_pid, 0)
                raise RuntimeError(f"Daemon already running (PID {old_pid})")
            except ProcessLookupError:
                pass  # stale PID, will be overwritten
    PID_FILE.write_text(str(os.getpid()))


def read_pid() -> int | None:
    if not PID_FILE.exists():
        return None
    try:
        return int(PID_FILE.read_text().strip())
    except (ValueError, OSError):
        return None


def clear_pid():
    if PID_FILE.exists():
        PID_FILE.unlink()


def write_status(ingested: int = 0, skipped: int = 0, errors: int = 0,
                 claims: int = 0, embeddings: int = 0,
                 relationships: str = "", source: str = "pipeline"):
    _ensure_dir()
    ts = datetime.now(timezone.utc).isoformat()
    payload = {
        "ts": ts,
        "ingested": ingested,
        "skipped": skipped,
        "errors": errors,
        "claims": claims,
        "embeddings": embeddings,
        "relationships": relationships,
    }
    data = json.dumps(payload, default=str)
    if source == "pipeline":
        STATUS_FILE.write_text(f"{data}\n")
    else:
        per_daemon_file = LOG_DIR / f"daemon.status.{source}"
        per_daemon_file.write_text(f"{data}\n")


def read_status() -> dict:
    if not STATUS_FILE.exists():
        return {"last_run": None, "result": None}
    raw = STATUS_FILE.read_text().strip()
    try:
        parsed = json.loads(raw)
        return {
            "last_run": parsed.get("ts"),
            "result": f"ingested={parsed.get('ingested',0)}, skipped={parsed.get('skipped',0)}, errors={parsed.get('errors',0)}",
        }
    except (json.JSONDecodeError, KeyError):
        parts = raw.split("\n", 1)
        return {
            "last_run": parts[0],
            "result": parts[1] if len(parts) > 1 else None,
        }
