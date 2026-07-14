# Bug Report: Helioryn Compliance

**Generated:** July 14, 2026
**Total:** 30 bugs — 4 P1, 6 P2, 10 P3, 8 P4, 2 P5

---

## P1 — Critical (Crashes / Data Loss)

### 1. Indentation error in config.py causes NameError
- **File:** `config.py:104-107`
- **Description:** Lines 104-107 are dedented to 4 spaces (class body level) instead of 12 spaces (inside the `if found:` block). They reference `ing_cfg` and `ingest_data`, which are only defined inside the `if found:` block. When no config file is found, this causes `NameError: name 'ing_cfg' is not defined` at class definition time.
- **Expected:** Lines 104-107 should be at 12 spaces indent, inside the `if path and os.path.exists(path):` block.

### 2. Missing JSONResponse import crashes /api/auth/signup
- **File:** `src/helioryn/server.py:468`
- **Description:** `JSONResponse` is used at line 468 but is not imported. The import block at lines 13-17 only imports `HTMLResponse` and `RedirectResponse` from `fastapi.responses`. Causes `NameError: name 'JSONResponse' is not defined` on signup.
- **Expected:** Add `JSONResponse` to the import: `from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse`.

### 3. Duplicate index name silently skips claim_embedding index
- **File:** `src/helioryn/store.py:224,239`
- **Description:** `CREATE INDEX IF NOT EXISTS idx_ce_claim_id ON claim_entity (claim_id)` (line 224) and `CREATE INDEX IF NOT EXISTS idx_ce_claim_id ON claim_embedding (claim_id)` (line 239) both use the name `idx_ce_claim_id`. In PostgreSQL, index names are unique per schema. The second CREATE INDEX silently succeeds but creates nothing because an index with that name already exists (on `claim_entity`), even though it is on a different table. The `claim_embedding` table never gets its claim_id index.
- **Expected:** Rename one of the indexes, e.g., `idx_ce_emb_claim_id` for the claim_embedding table.

### 4. CH_GROUNDING not defined in cache.py
- **File:** `cache.py` / `cli.py:844`
- **Description:** The root `cli.py` imports `CH_GROUNDING` from `helioryn.cache` (line 844), but `src/helioryn/cache.py` only defines `CH_PIPELINE`, `CH_SCORER`, `CH_ANALYZER`, `CH_INTERPRETER`, and `CH_DAEMON`. There is no `CH_GROUNDING`. Causes `ImportError: cannot import name 'CH_GROUNDING'` whenever `_process_watch` is called for the "ground" daemon.
- **Expected:** Add `CH_GROUNDING = "helioryn:grounding"` to `cache.py`.

---

## P2 — Major (Functionality Broken)

### 5. config.grounding attribute does not exist on AppConfig
- **File:** `cli.py:1618-1620`
- **Description:** `_ground_cycle` accesses `config.grounding.factcheck_api_key`, `config.grounding.factcheck_enabled`, and `config.grounding.wikidata_enabled`, but the `AppConfig` dataclass has no `grounding` attribute. Causes `AttributeError: 'AppConfig' object has no attribute 'grounding'` when the grounding cycle runs.
- **Expected:** Add a `GroundingConfig` dataclass to `AppConfig`.

### 6. Duplicate import os in server.py
- **File:** `src/helioryn/server.py:8-9`
- **Description:** `import os` appears twice. Dead code indicating copy-paste error.
- **Expected:** Remove one of the duplicate imports.

### 7. require_admin returns HTTP 303 for authentication error
- **File:** `src/helioryn/server.py:100`
- **Description:** `raise HTTPException(303, "Admin login required")` uses HTTP 303 (See Other / redirect), which is semantically wrong for an authentication failure. Clients receiving a 303 will attempt to redirect, not understand it as an error.
- **Expected:** Use HTTP 401 (Unauthorized) or 403 (Forbidden).

### 8. start.sh shebang on wrong line
- **File:** `start.sh:1-2`
- **Description:** The copyright comment is on line 1 and `#!/bin/bash` is on line 2. The shebang must be on the very first line. As-is, executing `./start.sh` will use the default shell (`sh`), which may not support bash-specific features.
- **Expected:** Move `#!/bin/bash` to line 1.

### 9. connect.sh shebang on wrong line
- **File:** `connect.sh:1-2`
- **Description:** Same issue as start.sh — shebang on line 2.
- **Expected:** Move `#!/bin/bash` to line 1.

### 10. connect.sh SSH venv activation path likely wrong
- **File:** `connect.sh:276`
- **Description:** `source ${HELIORYN_PKG}/venv/bin/activate` resolves to `helioryn/venv/bin/activate` relative to `HELIORYN_ROOT`. But `HELIORYN_PKG="helioryn"` (line 29) and `HELIORYN_ROOT` is `~/Projects/helioryn-design`, so the venv path becomes `~/Projects/helioryn-design/helioryn/venv/bin/activate`. The venv is more likely at the project root.
- **Expected:** Change to `source venv/bin/activate` or `${HELIORYN_ROOT}/venv/bin/activate`.

---

## P3 — Moderate

### 11. Hardcoded credentials in helioryn.toml
- **File:** `helioryn.toml:12-14`
- **Description:** `api_key = "hc-compliance-key"`, `admin_password = "admin"`, `session_secret = "hc-compliance-secret"` hardcoded in repo.
- **Expected:** Use environment variables or a `.env` file.

### 12. CORS allows all origins
- **File:** `src/helioryn/server.py:188-193`
- **Description:** `allow_origins=["*"]` permits any website to make cross-origin requests.
- **Expected:** Restrict to specific allowed origins.

### 13. ingest_watch doesn't clear PID on exit
- **File:** `cli.py:315-322`
- **Description:** The `finally` block of `ingest_watch` contains only `pass`. When the daemon stops, the PID file is never cleaned up.
- **Expected:** Call `log.clear_pid()` in the `finally` block like `discover_watch` does.

### 14. _process_watch Redis connection leak on exception
- **File:** `cli.py:876-903`
- **Description:** If `cache.get_message()` raises an exception other than `TimeoutError`, the Redis connection is never closed.
- **Expected:** Use `try/finally` to ensure `cache.close()` is always called.

### 15. All check scripts hardcode macOS socket path
- **File:** `check_daemon.py:7`, `check_db.py:6`, `check_postproc.py:8`, `check_state.py:6`, `sample_contradictions.py:7`, `verify_live.py:9`
- **Description:** All hardcode `postgresql:///helioryn_dev?host=/tmp`, which is macOS-only. Fails on Linux.
- **Expected:** Use database URL from config or environment variable.

### 16. _make_log_fn uses hardcoded /tmp/ path
- **File:** `cli.py:819/822`
- **Description:** `log_path = Path(f"/tmp/helioryn-{name}.log")` — Unix-specific, crashes on Windows.
- **Expected:** Use `tempfile.gettempdir()` for cross-platform compatibility.

### 17. Dual schema source: ensure_schema() and migrations diverge
- **File:** `src/helioryn/store.py:42-575` and `migrations/`
- **Description:** `ensure_schema()` creates all tables via inline SQL, duplicating migration files. Two sources of truth for schema.
- **Expected:** Use a single schema management approach.

### 18. _extract_all fetches all rows without pagination
- **File:** `cli.py:1962-1969`
- **Description:** Fetches ALL rows without LIMIT — memory risk on large databases.
- **Expected:** Process in batches or use the limit parameter effectively.

### 19. wait on non-child processes is a no-op in start.sh
- **File:** `start.sh:84`
- **Description:** `wait "$pid"` only works for child processes of the current shell. For processes from a prior invocation, it always fails — race condition leaves orphaned processes.
- **Expected:** Use a polling loop with `kill -0` and timeout.

### 20. No duplicate-instance guard in start.sh
- **File:** `start.sh:93-192`
- **Description:** Running `start.sh start` twice launches duplicate daemons. Second invocation overwrites PID files, orphaning first set.
- **Expected:** Add guard checking if process is already running.

---

## P4 — Minor

### 21. Unused import in src/helioryn/cli.py
- **File:** `src/helioryn/cli.py:14`
- **Description:** `from helioryn.hasher import content_hash` imported but never used.

### 22. test_discovery.py hardcodes AI track names not in config
- **File:** `test_discovery.py:52-55`
- **Description:** Test checks `known_tracks` that don't match actual config — tests always fail or are meaningless.

### 23. _is_boilerplate called but may not be defined
- **File:** `src/helioryn/store.py:917`
- **Description:** `insert_claim` calls `self._is_boilerplate(...)` — verify this method exists in the class.

### 24. Two divergent copies of cli.py
- **File:** `cli.py` (root) vs `src/helioryn/cli.py`
- **Description:** Root version has additional features (grounding, API ingest) not in src version. Which is authoritative?

### 25. OllamaProvider.generate has model param not in Protocol
- **File:** `src/helioryn/llm.py:44-53`
- **Description:** `LLMProvider` Protocol defines `generate` without `model` parameter, but implementations accept it. Type-safety gap.

### 26. Topics config structure mismatch
- **File:** `helioryn.toml` + `config.py:161`
- **Description:** Parser expects `[topics] items = [...]` but TOML doesn't have this structure — topics always empty.

### 27. Session cookie missing secure and samesite
- **File:** `src/helioryn/server.py:158`
- **Description:** `set_cookie` has `httponly=True` but not `secure=True` or `samesite`.

### 28. append_ledger may not be defined — silent failure
- **File:** `src/helioryn/store.py` (multiple)
- **Description:** Methods call `self.append_ledger(...)` wrapped in `try/except`. If not defined, failure is silently swallowed.

---

## P5 — Cosmetic

### 29. Copyright year 2026 — verify intentional
- **File:** All files
- **Description:** Copyright headers say "Copyright (c) 2026 Ravenkey LLC."

### 30. asyncio_mode = auto makes pytest decorators redundant
- **File:** `pyproject.toml:42` + test files
- **Description:** With `asyncio_mode = "auto"`, `@pytest.mark.asyncio` decorators are unnecessary.
