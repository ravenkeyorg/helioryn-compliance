# Copyright (c) 2026 Ravenkey LLC. All rights reserved.
import json
import os
import tempfile

import pytest

from helioryn import log


@pytest.fixture(autouse=True)
def _patch_log_path(monkeypatch, tmp_path):
    monkeypatch.setattr(log, "LOG_DIR", tmp_path)
    monkeypatch.setattr(log, "LOG_FILE", tmp_path / "run.log")
    monkeypatch.setattr(log, "PID_FILE", tmp_path / "daemon.pid")


def test_emit_creates_log_file():
    log.emit("test_event", key="value")
    assert log.LOG_FILE.exists()


def test_emit_writes_json_line():
    log.emit("test_event", key="value")
    lines = log.LOG_FILE.read_text().strip().split("\n")
    assert len(lines) == 1
    data = json.loads(lines[0])
    assert data["event"] == "test_event"
    assert data["key"] == "value"


def test_read_log_returns_events():
    log.emit("event_a", x=1)
    log.emit("event_b", y=2)
    events = log.read_log(10)
    assert len(events) == 2
    assert events[0]["event"] == "event_a"
    assert events[1]["event"] == "event_b"


def test_read_log_respects_limit():
    for i in range(10):
        log.emit("ev", n=i)
    events = log.read_log(3)
    assert len(events) == 3


def test_get_runs_groups_by_run_id():
    log.emit("run_started", run="run1", topic="test")
    log.emit("source_ingested", run="run1", url="https://a.com")
    log.emit("run_completed", run="run1", ingested=1, skipped=0, errors=0)
    log.emit("run_started", run="run2", topic="test2")

    runs = log.get_runs(10)
    assert len(runs) == 2
    run1 = [r for r in runs if r["run_id"] == "run1"]
    assert run1[0]["ingested"] == 1


def test_pid_round_trip():
    log.write_pid()
    assert log.read_pid() == os.getpid()


def test_clear_pid():
    log.write_pid()
    log.clear_pid()
    assert log.read_pid() is None
