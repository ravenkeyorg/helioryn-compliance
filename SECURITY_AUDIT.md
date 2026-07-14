# Security Audit: Helioryn Compliance

**Generated:** July 14, 2026
**Total:** 29 findings — 3 Critical, 12 High, 10 Medium, 4 Low

---

## CRITICAL

### 1. Password Salt Derived from Password (Deterministic Salt)
- **CWE:** CWE-760 — Use of a One-Way Hash with a Predictable Salt
- **File:** `src/helioryn/store.py:3914-3916`
- **Description:** `_hash_password()` derives the salt from the password itself:
  ```python
  salt = hashlib.sha256(password.encode()).hexdigest()[:16]
  ```
  Every user with the same password gets the identical salt and hash. The salt provides zero protection against rainbow tables or precomputation attacks.
- **Impact:** An attacker with database access can trivially identify users sharing the same password.
- **Fix:** Generate a random salt per user with `os.urandom(16).hex()` and store it alongside the hash.

### 2. Hardcoded Secrets in Plaintext Config
- **CWE:** CWE-798 — Use of Hard-coded Credentials
- **File:** `helioryn.toml:12-14`
- **Description:** API key (`hc-compliance-key`), admin password (`admin`), and session secret (`hc-compliance-secret`) are stored in plaintext in the TOML config file committed to the repository.
- **Impact:** Anyone with filesystem or repo access can read these credentials.
- **Fix:** Use environment variables or a secrets manager. Remove secrets from version control.

### 3. CORS Wildcard Allows Any Origin
- **CWE:** CWE-942 — Permissive Cross-domain Policy with Untrusted Domains
- **File:** `src/helioryn/server.py:188-193`
- **Description:** `allow_origins=["*"]` permits any website to make cross-origin requests to the API, including credentialed requests. The session cookie auth is vulnerable to CSRF-style attacks via CORS.
- **Impact:** An attacker's website can use a victim's browser to perform actions against the API if the victim is logged in.
- **Fix:** Restrict `allow_origins` to specific trusted domains.

---

## HIGH

### 4. Session Tokens Lack Nonce and Revocability
- **CWE:** CWE-384 — Session Fixation
- **File:** `src/helioryn/server.py:73-80`
- **Description:** Session tokens are built as `username|role|HMAC(username|role, secret)`. No unique session ID, nonce, or creation timestamp. Tokens cannot be individually revoked — rotating the session secret invalidates ALL sessions.
- **Impact:** Stolen tokens grant persistent access with no per-session revocation.
- **Fix:** Include a random session ID (UUID) in the payload, stored server-side with expiry.

### 5. Session Cookie Missing Secure and SameSite Flags
- **CWE:** CWE-614 — Sensitive Cookie in HTTPS Session Without 'Secure' Attribute
- **File:** `src/helioryn/server.py:158,442,469`
- **Description:** `resp.set_cookie(key="session", value=token, httponly=True, max_age=86400)` is missing `secure=True` and `samesite`.
- **Impact:** Cookie can be transmitted over unencrypted HTTP (MITM interception) and lacks CSRF protection via SameSite.
- **Fix:** Add `secure=True, samesite="lax"` to all `set_cookie` calls.

### 6. Open Redirect via next Parameter
- **CWE:** CWE-601 — URL Redirection to Untrusted Site
- **File:** `src/helioryn/server.py:142-147,157,411-416,441`
- **Description:** The `next` query parameter is used directly in `RedirectResponse(url=next)` with no validation of the target URL. An attacker can supply `?next=https://evil.com` to redirect users after login.
- **Impact:** Phishing attacks — users authenticated then redirected to attacker-controlled site.
- **Fix:** Validate that `next` is a relative path or belongs to an allowed origin list.

### 7. No Authentication on /api/chat Endpoint
- **CWE:** CWE-306 — Missing Authentication for Critical Function
- **File:** `src/helioryn/server.py:852-879`
- **Description:** The `/api/chat` POST endpoint has no authentication — no `Depends(verify_key)` and no session check. Anyone can query the LLM and all ingested documents.
- **Impact:** Unauthenticated access to LLM, ingested document content, and potential quota exhaustion.
- **Fix:** Add `Depends(verify_key)` or require session authentication.

### 8. No Authentication on /api/daemon/activity
- **CWE:** CWE-306 — Missing Authentication for Critical Function
- **File:** `src/helioryn/server.py:203-235`
- **Description:** Returns daemon PID, running status, last run results, and database stats — no auth required.
- **Impact:** Information disclosure — PID, runtime behavior, database size metrics exposed.
- **Fix:** Add `Depends(verify_key)` or require session auth.

### 9. Full API Keys Exposed in Credentials Endpoint
- **CWE:** CWE-200 — Exposure of Sensitive Information
- **File:** `src/helioryn/server.py:793-802`
- **Description:** The `list_credentials()` endpoint returns the full `api_credential` dict including the raw `api_key`. The code adds a masked version but does NOT remove the original.
- **Impact:** Admin users see full plaintext API keys in HTTP responses.
- **Fix:** Remove the raw `api_key` field from the response; only return `api_key_masked`.

### 10. Error Detail Disclosure (Stack Traces & Internals)
- **CWE:** CWE-209 — Generation of Error Message Containing Sensitive Information
- **File:** `src/helioryn/server.py:312-316,874-879`, `web_routes.py:67-68`
- **Description:** Exception messages returned verbatim: `f"Failed to change password: {e}"`, `"error": str(exc)`, `stats["db_error"] = str(exc)`. Leaks internal paths, SQL errors, LLM config.
- **Impact:** Attackers can probe the system to learn internal stack, DB structure, and configuration.
- **Fix:** Log errors server-side; return generic error messages to the client.

### 11. Hardcoded Fallback API Key "helioryn-dev-key"
- **CWE:** CWE-798 — Use of Hard-coded Credentials
- **File:** `src/helioryn/server.py:41`
- **Description:** `_API_KEY = cfg.auth.api_key or "helioryn-dev-key"` — if no API key configured, falls back to a well-known hardcoded key in source code.
- **Impact:** Anyone who knows this key can authenticate as an API user.
- **Fix:** Fail at startup if no API key is configured. Remove the fallback.

### 12. Hardcoded Fallback Admin Password "admin"
- **CWE:** CWE-798 — Use of Hard-coded Credentials
- **File:** `src/helioryn/server.py:116`
- **Description:** `config.auth.admin_password or "admin"` — if no admin password configured, defaults to `"admin"`.
- **Impact:** Trivially guessable default admin password.
- **Fix:** Require a strong admin password to be configured; fail startup if missing.

### 13. No CSRF Protection on Form Endpoints
- **CWE:** CWE-352 — Cross-Site Request Forgery
- **File:** `src/helioryn/server.py:150-170,274-317,419-443`
- **Description:** Login, change-password, and signup forms accept `Form(...)` data with no CSRF token validation.
- **Impact:** An attacker's website can submit these forms on behalf of an authenticated user, e.g., changing their password.
- **Fix:** Implement CSRF tokens (FastAPI CSRF middleware or hidden tokens in Jinja2).

### 14. No Rate Limiting on Authentication Endpoints
- **CWE:** CWE-307 — Improper Restriction of Excessive Authentication Attempts
- **File:** `src/helioryn/server.py:150-170,274-317,419-443`
- **Description:** Login, signup, and change-password endpoints have no rate limiting, account lockout, or throttling.
- **Impact:** Brute-force password attacks, especially against the "admin" account with weak default password.
- **Fix:** Add rate limiting (e.g., slowapi or custom middleware) on auth endpoints.

### 15. Weak Password Hashing (100k PBKDF2 Iterations)
- **CWE:** CWE-916 — Use of Password Hash With Insufficient Computational Effort
- **File:** `src/helioryn/store.py:3914-3923`
- **Description:** PBKDF2-SHA256 with only 100,000 iterations. NIST recommends at least 600,000 for SHA-256. Combined with the deterministic salt (CRITICAL #1), hashes are significantly weaker than industry standard.
- **Impact:** Password hashes can be cracked faster than with modern recommendations.
- **Fix:** Increase iterations to 600,000+ or migrate to Argon2id.

---

## MEDIUM

### 16. Arbitrary File Read in CLI Ingest
- **CWE:** CWE-22 — Path Traversal
- **File:** `cli.py:133,147-153`
- **Description:** `ingest_file` takes a user-supplied file path and reads it with no directory restriction. An attacker can read any file on the system.
- **Impact:** Arbitrary file read from the server filesystem.
- **Fix:** Restrict file path resolution to an allowed directory.

### 17. Path Traversal in OIG Reports Download
- **CWE:** CWE-22 — Path Traversal
- **File:** `src/helioryn/ingest/api_source/oig_reports.py:57`
- **Description:** Filename derived from URL path component without sanitization. A URL like `https://evil.com/../../etc/passwd` could write outside the intended directory.
- **Impact:** Arbitrary file write.
- **Fix:** Sanitize the filename — strip path traversal characters.

### 18. No File Content Validation on Document Upload
- **CWE:** CWE-434 — Unrestricted Upload of File with Dangerous Type
- **File:** `src/helioryn/server.py:885-910`
- **Description:** `/api/documents/upload` accepts any file without validating content type, magic bytes, or extension whitelist.
- **Impact:** An attacker could upload malicious files (HTML with JS, executables).
- **Fix:** Validate content-type, check magic bytes, restrict allowed extensions.

### 19. SSRF via HTTP Fetcher
- **CWE:** CWE-918 — Server-Side Request Forgery
- **File:** `src/helioryn/ingest/fetcher/http.py:17-33`, `cli.py:90-125`
- **Description:** `HttpFetcher.fetch(url)` takes an arbitrary URL with no allowlist. Can be used to target internal services (`localhost`, cloud metadata endpoints).
- **Impact:** Access to internal services and cloud instance metadata.
- **Fix:** Implement URL allowlist/denylist; block private IP ranges and cloud metadata endpoints.

### 20. Settings Endpoint Lacks Input Validation
- **CWE:** CWE-20 — Improper Input Validation
- **File:** `src/helioryn/server.py:778-788`
- **Description:** `/api/settings` accepts arbitrary key-value pairs and stores them directly without validation.
- **Impact:** Admin could set unexpected keys that interfere with other system components.
- **Fix:** Validate setting keys against an allowlist.

### 21. Sessions Not Invalidated on Password Change
- **CWE:** CWE-613 — Insufficient Session Expiration
- **File:** `src/helioryn/server.py:274-317`, `src/helioryn/store.py:3961-3965`
- **Description:** When a user changes their password, existing session tokens remain valid (they're HMAC-signed with the session secret, not the password hash).
- **Impact:** If password is changed due to compromise, attacker's existing session tokens remain valid.
- **Fix:** Include a token version counter in the HMAC payload that increments on password change.

### 22. Failed Logins Not Logged
- **CWE:** CWE-778 — Insufficient Logging
- **File:** `src/helioryn/server.py:150-170`
- **Description:** Login endpoint only logs successful logins. Failed attempts fall through with no audit trail.
- **Impact:** Brute-force attacks go undetected.
- **Fix:** Log failed login attempts with username and IP address.

### 23. Race Condition in bootstrap_admin
- **CWE:** CWE-362 — Concurrent Execution with Shared Resource
- **File:** `src/helioryn/store.py:3925-3935`
- **Description:** Reads `SELECT COUNT(*) FROM app_user` then conditionally inserts. Between read and insert, another instance could also insert. `ON CONFLICT DO NOTHING` partially mitigates.
- **Impact:** At worst, a redundant insert is silently skipped.
- **Fix:** Use `INSERT ... ON CONFLICT DO NOTHING` directly without the prior count check.

### 24. Logs in World-Readable /tmp
- **CWE:** CWE-276 — Incorrect Default Permissions
- **File:** `cli.py:819-826`
- **Description:** `log_path = Path(f"/tmp/helioryn-{name}.log")` — logs stored in world-readable `/tmp`.
- **Impact:** Local users can read daemon logs containing operational data.
- **Fix:** Store logs in a directory with restricted permissions.

### 25. No TLS for Redis Connection
- **CWE:** CWE-319 — Cleartext Transmission of Sensitive Information
- **File:** `src/helioryn/cache.py:14,36`
- **Description:** `DEFAULT_REDIS_URL = "redis://localhost:6379/0"` — plain Redis protocol with no TLS.
- **Impact:** Credentials and cached data flow in plaintext if Redis is not on localhost.
- **Fix:** Use `rediss://` (TLS) when Redis is not local.

### 26. LLM Config Leaked in Error Messages
- **CWE:** CWE-209 — Information Exposure Through Error Messages
- **File:** `src/helioryn/rag.py:241-248`
- **Description:** LLM error response includes provider name and model: `f"Please check the LLM configuration (provider: {cfg.llm.provider}, model: {cfg.llm.model})"`.
- **Impact:** Reveals LLM backend and model in use.
- **Fix:** Log detailed errors server-side; return generic error message.

### 27. Unauthenticated Signup Creates Accounts
- **CWE:** CWE-306 — Missing Authentication for Critical Function
- **File:** `src/helioryn/server.py:419-443,446-470`
- **Description:** Both `/signup` (form) and `/api/auth/signup` (JSON) allow anyone to create `viewer` accounts without authentication, CAPTCHA, or email verification.
- **Impact:** Attackers can create unlimited accounts — resource exhaustion and LLM abuse.
- **Fix:** Require invite code, email verification, or admin approval for signup.

---

## LOW

### 28. No Auth Barrier on Staging Review Endpoint
- **CWE:** CWE-285 — Improper Authorization
- **File:** `src/helioryn/server.py:1287-1300`
- **Description:** Staging review uses `Depends(verify_key)` (API key) rather than admin session check. API key holders get broader privileges than necessary.
- **Impact:** Anyone with the API key can approve/reject staging items.
- **Fix:** Change to `Depends(require_admin)`.

### 29. Weak Password Policy (Minimum 4 Characters)
- **CWE:** CWE-521 — Weak Password Requirements
- **File:** `src/helioryn/server.py:290-295,420-430`
- **Description:** Password validation only checks minimum length of 4 characters. No requirement for mixed case, digits, or special characters.
- **Impact:** Weak passwords like "abcd" or "1234" are accepted.
- **Fix:** Enforce minimum 8 characters, mixed case, digits, special chars.
