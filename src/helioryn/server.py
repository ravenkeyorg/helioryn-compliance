# Copyright (c) 2026 Ravenkey LLC. All rights reserved.
from __future__ import annotations

import hashlib
import hmac
import json
import os
import uuid
import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI, Form, Header, HTTPException, Query, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from helioryn.config import AppConfig
from helioryn.models import SourceSnapshot
from helioryn.store import EventStore

_CONFIG_PATH: str | None = None
_store: EventStore | None = None
_API_KEY: str | None = None
_SESSION_SECRET: str | None = None


def _get_config() -> AppConfig:
    cfg = AppConfig.load(_CONFIG_PATH)
    env_key = os.environ.get("HELIORYN_API_KEY", "")
    if env_key:
        cfg.auth.api_key = env_key
    return cfg


def _get_api_key() -> str:
    global _API_KEY
    if _API_KEY is None:
        cfg = _get_config()
        _API_KEY = cfg.auth.api_key or "helioryn-dev-key"
    return _API_KEY


def _get_session_secret() -> str:
    global _SESSION_SECRET
    if _SESSION_SECRET is None:
        cfg = _get_config()
        _SESSION_SECRET = cfg.auth.session_secret
    return _SESSION_SECRET


async def get_store() -> EventStore:
    global _store
    if _store is None:
        config = _get_config()
        _store = EventStore(config.database_url)
        await _store.connect()
    return _store


async def verify_key(x_api_key: str = Header(...)):
    if x_api_key != _get_api_key():
        raise HTTPException(403, "Invalid API key")


# ── Session Auth for Web UI ───────────────────────────────────────

_TEMPLATES_DIR = Path(__file__).parent / "templates"
_session_templates = Jinja2Templates(directory=str(_TEMPLATES_DIR))


def _make_session_token(username: str, role: str) -> str:
    payload = f"{username}|{role}"
    sig = hmac.new(
        _get_session_secret().encode(),
        payload.encode(),
        hashlib.sha256,
    ).hexdigest()
    return f"{username}|{role}|{sig}"


def _check_session(request: Request) -> dict | None:
    token = request.cookies.get("session")
    if not token:
        return None
    parts = token.split("|", 2)
    if len(parts) != 3:
        return None
    username, role, sig = parts
    expected = _make_session_token(username, role)
    if not hmac.compare_digest(sig, expected.split("|", 2)[2]):
        return None
    return {"username": username, "role": role}


async def require_admin(request: Request) -> dict | None:
    user = _check_session(request)
    if not user or user.get("role") != "admin":
        raise HTTPException(303, "Admin login required")
    return user


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _store
    config = AppConfig.load(_CONFIG_PATH)
    _store = EventStore(config.database_url)
    app.state.store = _store
    await _store.connect()
    try:
        await _store.ensure_schema()
    except Exception:
        pass
    try:
        await _store.bootstrap_admin(config.auth.admin_password or "admin")
    except Exception:
        pass
    yield
    await _store.close()
    app.state.store = None


app = FastAPI(lifespan=lifespan, title="Helioryn", version="0.1.0")

# Mount static files
_static_dir = Path(__file__).parent / "static"
_static_dir.mkdir(parents=True, exist_ok=True)
app.mount("/static", StaticFiles(directory=str(_static_dir)), name="static")

# Register web GUI routes
from helioryn.web_routes import register_web_routes
register_web_routes(app)

# Register chat & project API routes
from helioryn.chat_routes import router as chat_router
app.include_router(chat_router)

# ── Login routes (after app creation) ──────────────────────────────

@app.get("/login", response_class=HTMLResponse)
async def login_page(request: Request, next: str = ""):
    if _check_session(request):
        return RedirectResponse(url=next or "/chat", status_code=303)
    return _session_templates.TemplateResponse(
        request, "login.html", {"next": next, "error": None}
    )


@app.post("/login")
async def login(request: Request, username: str = Form(...), password: str = Form(...), next: str = Form("")):
    try:
        store = await get_store()
        user = await store.get_user_by_username(username)
        if user and store._verify_password(password, user["password_hash"]):
            token = _make_session_token(username, user["role"])
            resp = RedirectResponse(url=next or "/chat", status_code=303)
            resp.set_cookie(key="session", value=token, httponly=True, max_age=86400)
            try:
                ip = request.client.host if request.client else ""
                await store.log_admin_action("login", {"user": username, "role": user["role"]}, ip)
            except Exception:
                pass
            return resp
    except Exception:
        pass
    return _session_templates.TemplateResponse(
        request, "login.html", {"next": next, "error": "Invalid username or password"},
        status_code=401,
    )


@app.get("/logout")
async def logout(request: Request):
    try:
        store = await get_store()
        user = _check_session(request)
        ip = request.client.host if request.client else ""
        if user:
            await store.log_admin_action("logout", {"user": user.get("username")}, ip)
    except Exception:
        pass
    resp = RedirectResponse(url="/chat", status_code=303)
    resp.delete_cookie("session")
    return resp


app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/api/health")
async def health():
    from helioryn.housekeeping import disk_usage
    disk = disk_usage()
    return {"status": "ok", "disk": disk}


@app.get("/api/daemon/activity")
async def daemon_activity():
    from helioryn.log import read_status
    import os as _os
    from pathlib import Path

    pid_file = Path.home() / ".helioryn" / "daemon.pid"
    running = False
    pid = None
    if pid_file.exists():
        try:
            p = int(pid_file.read_text().strip())
            _os.kill(p, 0)
            running = True
            pid = p
        except (OSError, ValueError):
            pass

    status = read_status()
    store = await get_store()
    s = await store.get_stats()
    return {
        "daemon": {"running": running, "pid": pid, "last_run": status.get("last_run"), "result": status.get("result")},
        "stats": {
            "sources": s["total_sources"],
            "claims": s.get("total_claims", 0),
            "embeddings": s.get("total_embeddings", 0),
            "relationships": s.get("total_relationships", 0),
            "contradictions": s.get("total_contradictions", 0),
            "observations": s.get("total_observations", 0),
            "rate_1h": s.get("rate_1h", 0),
        },
    }


@app.get("/api/stats", dependencies=[Depends(verify_key)])
async def stats():
    store = await get_store()
    s = await store.get_stats()
    claim_count = await store.get_claim_count()
    query_count = await store.get_query_count()
    entity_count = await store.get_entity_count()
    return {
        "total_sources": s["total_sources"],
        "total_events": s["total_events"],
        "total_claims": s.get("total_claims", 0),
        "total_observations": s.get("total_observations", 0),
        "search_queries": query_count,
        "gov_entities": entity_count,
        "total_entities": s.get("total_entities", 0),
        "total_embeddings": s.get("total_embeddings", 0),
        "total_relationships": s.get("total_relationships", 0),
        "total_repeated_by": s.get("total_repeated_by", 0),
        "total_contradictions": s.get("total_contradictions", 0),
        "total_narratives": s.get("total_narratives", 0),
        "rate_1h": s.get("rate_1h", 0),
        "rate_24h": s.get("rate_24h", 0),
        "updated_sources": s["updated_sources"],
        "oldest_source": str(s["oldest_source"] or ""),
        "newest_source": str(s["newest_source"] or ""),
        "sources_with_date": s.get("sources_with_date", 0),
        "sources_with_author": s.get("sources_with_author", 0),
        "sources_with_language": s.get("sources_with_language", 0),
    }


# ── User Authentication Routes ────────────────────────────────────

_HTML_TEMPLATES = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))


@app.post("/change-password")
async def change_password(
    request: Request,
    old_password: str = Form(...),
    new_password: str = Form(...),
    confirm_password: str = Form(...),
):
    user = _check_session(request)
    if not user:
        raise HTTPException(303, "Not authenticated")
    if new_password != confirm_password:
        return _HTML_TEMPLATES.TemplateResponse(
            request, "change_password.html",
            {"error": "New passwords do not match", "success": None},
            status_code=400,
        )
    if len(new_password) < 4:
        return _HTML_TEMPLATES.TemplateResponse(
            request, "change_password.html",
            {"error": "Password must be at least 4 characters", "success": None},
            status_code=400,
        )
    try:
        store = await get_store()
        db_user = await store.get_user_by_username(user["username"])
        if not db_user or not store._verify_password(old_password, db_user["password_hash"]):
            return _HTML_TEMPLATES.TemplateResponse(
                request, "change_password.html",
                {"error": "Current password is incorrect", "success": None},
                status_code=400,
            )
        await store.change_password(user["username"], new_password)
        ip = request.client.host if request.client else ""
        await store.log_admin_action("change_password", {"user": user["username"]}, ip)
        return _HTML_TEMPLATES.TemplateResponse(
            request, "change_password.html",
            {"error": None, "success": "Password changed successfully"},
        )
    except Exception as e:
        return _HTML_TEMPLATES.TemplateResponse(
            request, "change_password.html",
            {"error": f"Failed to change password: {e}", "success": None},
            status_code=500,
        )


# ── User Management API (admin only) ─────────────────────────────


@app.get("/api/users")
async def api_list_users(request: Request):
    user = _check_session(request)
    if not user or user.get("role") != "admin":
        raise HTTPException(403, "Admin access required")
    store = await get_store()
    users = await store.list_users()
    return [
        {"user_id": str(u["user_id"]), "username": u["username"],
         "role": u["role"], "created_at": str(u.get("created_at", ""))}
        for u in users
    ]


@app.post("/api/users")
async def api_create_user(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
    role: str = Form("viewer"),
):
    user = _check_session(request)
    if not user or user.get("role") != "admin":
        raise HTTPException(403, "Admin access required")
    if len(username) < 2 or len(password) < 4:
        raise HTTPException(400, "Username (min 2 chars) and password (min 4 chars) required")
    if role not in ("admin", "viewer"):
        role = "viewer"
    store = await get_store()
    result = await store.create_user(username, password, role)
    if not result:
        raise HTTPException(409, "Username already exists")
    ip = request.client.host if request.client else ""
    await store.log_admin_action("create_user", {"user": username, "role": role}, ip)
    return {"status": "created", "username": username, "role": role}


@app.put("/api/users/{username}")
async def api_update_user_role(
    request: Request, username: str, role: str = Form("viewer"),
):
    user = _check_session(request)
    if not user or user.get("role") != "admin":
        raise HTTPException(403, "Admin access required")
    if role not in ("admin", "viewer"):
        raise HTTPException(400, "Role must be 'admin' or 'viewer'")
    store = await get_store()
    ok = await store.change_user_role(username, role)
    if not ok:
        raise HTTPException(404, "User not found")
    ip = request.client.host if request.client else ""
    await store.log_admin_action("update_user_role", {"user": username, "role": role}, ip)
    return {"status": "updated", "username": username, "role": role}


@app.delete("/api/users/{username}")
async def api_delete_user(request: Request, username: str):
    user = _check_session(request)
    if not user or user.get("role") != "admin":
        raise HTTPException(403, "Admin access required")
    if username == user["username"]:
        raise HTTPException(400, "Cannot delete yourself")
    store = await get_store()
    ok = await store.delete_user(username)
    if not ok:
        raise HTTPException(404, "User not found")
    ip = request.client.host if request.client else ""
    await store.log_admin_action("delete_user", {"user": username}, ip)
    return {"status": "deleted", "username": username}


@app.get("/api/users/me")
async def api_current_user(request: Request):
    user = _check_session(request)
    if not user:
        raise HTTPException(401, "Not authenticated")
    return {"username": user["username"], "role": user["role"]}


@app.get("/api/auth/status")
async def auth_status(request: Request):
    user = _check_session(request)
    if not user:
        return {"authenticated": False}
    return {"authenticated": True, "username": user["username"], "role": user["role"]}


@app.get("/signup", response_class=HTMLResponse)
async def signup_page(request: Request, next: str = ""):
    if _check_session(request):
        return RedirectResponse(url=next or "/chat", status_code=303)
    return _session_templates.TemplateResponse(
        request, "signup.html", {"next": next, "error": None}
    )


@app.post("/signup")
async def signup(request: Request, username: str = Form(...), password: str = Form(...), next: str = Form("")):
    if len(username) < 2:
        return _session_templates.TemplateResponse(
            request, "signup.html", {"next": next, "error": "Username must be at least 2 characters"},
            status_code=400,
        )
    if len(password) < 4:
        return _session_templates.TemplateResponse(
            request, "signup.html", {"next": next, "error": "Password must be at least 4 characters"},
            status_code=400,
        )
    store = await get_store()
    result = await store.create_user(username, password, "viewer")
    if not result:
        return _session_templates.TemplateResponse(
            request, "signup.html", {"next": next, "error": "Username already exists"},
            status_code=409,
        )
    ip = request.client.host if request.client else ""
    await store.log_admin_action("signup", {"user": username}, ip)
    token = _make_session_token(username, "viewer")
    resp = RedirectResponse(url=next or "/chat", status_code=303)
    resp.set_cookie(key="session", value=token, httponly=True, max_age=86400)
    return resp


@app.post("/api/auth/signup")
async def api_signup(request: Request):
    body = await request.json()
    username = (body.get("username") or "").strip()
    password = body.get("password", "")

    if len(username) < 2:
        raise HTTPException(400, "Username must be at least 2 characters")
    if len(password) < 4:
        raise HTTPException(400, "Password must be at least 4 characters")

    store = await get_store()
    result = await store.create_user(username, password, "viewer")
    if not result:
        raise HTTPException(409, "Username already exists")

    ip = request.client.host if request.client else ""
    await store.log_admin_action("signup", {"user": username}, ip)

    # Auto-login after signup
    from helioryn.server import _make_session_token
    token = _make_session_token(username, "viewer")
    resp = JSONResponse({"status": "created", "username": username, "role": "viewer"})
    resp.set_cookie(key="session", value=token, httponly=True, max_age=86400)
    return resp


@app.get("/api/sources", dependencies=[Depends(verify_key)])
async def list_sources(limit: int = Query(20, ge=1, le=200)):
    store = await get_store()
    snapshots = await store.list_snapshots(limit=limit)
    return [
        {
            "source_id": str(s.source_id),
            "url": s.source_url,
            "title": s.title,
            "author": s.author,
            "publish_date": str(s.publish_date) if s.publish_date else None,
            "language": (s.metadata or {}).get("language"),
            "updated": str(s.last_updated_at),
        }
        for s in snapshots
    ]


@app.get("/api/sources/{source_id}", dependencies=[Depends(verify_key)])
async def get_source(source_id: str):
    from uuid import UUID

    store = await get_store()
    try:
        sid = UUID(source_id)
    except ValueError:
        raise HTTPException(400, "Invalid source ID")
    snapshot = await store.get_snapshot(sid)
    if not snapshot:
        raise HTTPException(404, "Source not found")
    events = await store.get_events(sid)
    head = (snapshot.metadata or {}).get("head_meta", {})
    return {
        "source_id": str(snapshot.source_id),
        "url": snapshot.source_url,
        "title": snapshot.title,
        "author": snapshot.author,
        "publish_date": str(snapshot.publish_date) if snapshot.publish_date else None,
        "first_seen": str(snapshot.first_seen_at),
        "last_updated": str(snapshot.last_updated_at),
        "content_hash": snapshot.content_hash,
        "method": snapshot.retrieval_method,
        "versions": len(events),
        "language": head.get("language"),
        "canonical_url": (snapshot.metadata or {}).get("canonical_url"),
        "meta_tags": len(head),
        "text_preview": snapshot.raw_text[:500],
    }


@app.get("/api/search", dependencies=[Depends(verify_key)])
async def search(q: str = Query(..., min_length=1), limit: int = Query(20, ge=1, le=100)):
    store = await get_store()
    results = await store.search_content(q, limit=limit)
    return [
        {
            "source_id": str(s.source_id),
            "url": s.source_url,
            "title": s.title,
            "updated": str(s.last_updated_at),
        }
        for s in results
    ]


@app.get("/api/queries", dependencies=[Depends(verify_key)])
async def list_queries(limit: int = Query(50, ge=1, le=500)):
    store = await get_store()
    queries = await store.list_queries(limit=limit)
    return [
        {
            "query_id": str(q.get("query_id", "")),
            "text": q.get("text", ""),
            "priority": q.get("priority", 50),
            "last_run": str(q.get("last_run") or ""),
            "active": q.get("active", True),
        }
        for q in queries
    ]


@app.get("/api/entities", dependencies=[Depends(verify_key)])
async def list_entities(level: str | None = None, limit: int = Query(50, ge=1, le=500)):
    store = await get_store()
    entities = await store.list_entities(level=level, limit=limit)
    return [
        {
            "entity_id": str(e.get("entity_id", "")),
            "name": e.get("name", ""),
            "level": e.get("level", ""),
            "country": e.get("country", ""),
        }
        for e in entities
    ]


@app.get("/api/claims", dependencies=[Depends(verify_key)])
async def list_claims(source_id: str | None = None, limit: int = Query(20, ge=1, le=200)):
    from uuid import UUID

    store = await get_store()
    if source_id:
        try:
            sid = UUID(source_id)
        except ValueError:
            raise HTTPException(400, "Invalid source ID")
        claims = await store.get_claims_for_source(sid, limit=limit)
    else:
        async with store._pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT claim_id, source_id, canonical_text, extraction_confidence, claim_type "
                "FROM claim ORDER BY extracted_at DESC LIMIT $1",
                limit,
            )
            claims = [dict(r) for r in rows]
    return [
        {
            "claim_id": str(c.get("claim_id", "")),
            "source_id": str(c.get("source_id", ""))[:8],
            "text": c.get("canonical_text", "")[:200],
            "confidence": c.get("extraction_confidence", 0),
            "type": c.get("claim_type", ""),
        }
        for c in claims
    ]


@app.get("/api/claims/{claim_id}/versions", dependencies=[Depends(verify_key)])
async def claim_versions(claim_id: str):
    from uuid import UUID

    store = await get_store()
    try:
        cid = UUID(claim_id)
    except ValueError:
        raise HTTPException(400, "Invalid claim ID")
    versions = await store.get_claim_versions(cid)
    claim = await store.get_claim(cid)
    if not claim and not versions:
        raise HTTPException(404, "Claim not found")
    return {
        "claim_id": claim_id,
        "current_version": claim.get("current_version", 1) if claim else 0,
        "versions": [
            {
                "version_id": str(v["version_id"]),
                "version": v["version"],
                "canonical_text": v["canonical_text"][:200],
                "extracted_at": str(v["extracted_at"]),
            }
            for v in versions
        ],
    }


@app.get("/api/timeline", dependencies=[Depends(verify_key)])
async def timeline(
    from_date: str = Query(default=None, alias="from"),
    to_date: str = Query(default=None, alias="to"),
    limit: int = Query(200, ge=1, le=1000),
):
    store = await get_store()

    # Build time window filter
    conditions: list[str] = []
    params: list[str | int] = []
    idx = 1
    if from_date:
        conditions.append(f"extracted_at >= ${idx}")
        params.append(from_date)
        idx += 1
    if to_date:
        conditions.append(f"extracted_at <= ${idx}")
        params.append(to_date)
        idx += 1

    where = " AND ".join(conditions) if conditions else "TRUE"
    params.append(limit)

    sql = f"""
    SELECT claim_id, source_id, canonical_text, extracted_at, current_version
    FROM claim
    WHERE {where}
    ORDER BY extracted_at DESC
    LIMIT ${idx}
    """
    async with store._pool.acquire() as conn:
        rows = await conn.fetch(sql, *params)
    return [
        {
            "claim_id": str(r["claim_id"]),
            "source_id": str(r["source_id"])[:8],
            "text": r["canonical_text"][:200],
            "extracted_at": str(r["extracted_at"]),
            "version": r["current_version"],
        }
        for r in rows
    ]


@app.get("/api/observations", dependencies=[Depends(verify_key)])
async def list_observations(
    claim_id: str | None = None,
    source_id: str | None = None,
    limit: int = Query(20, ge=1, le=200),
):
    from uuid import UUID

    store = await get_store()
    if claim_id:
        try:
            cid = UUID(claim_id)
        except ValueError:
            raise HTTPException(400, "Invalid claim ID")
        obs = await store.get_observations_for_claim(cid, limit=limit)
    elif source_id:
        try:
            sid = UUID(source_id)
        except ValueError:
            raise HTTPException(400, "Invalid source ID")
        obs = await store.get_observations_for_source(sid, limit=limit)
    else:
        async with store._pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT observation_id, claim_id, source_id, observed_at, observer "
                "FROM claim_observation ORDER BY observed_at DESC LIMIT $1", limit
            )
            obs = [dict(r) for r in rows]
    return [
        {
            "observation_id": str(o.get("observation_id", "")),
            "claim_id": str(o.get("claim_id", ""))[:8],
            "source_id": str(o.get("source_id", ""))[:8],
            "observed_at": str(o.get("observed_at", "")),
            "observer": o.get("observer", ""),
        }
        for o in obs
    ]


@app.get("/api/entities/search", dependencies=[Depends(verify_key)])
async def search_entities(q: str = Query(..., min_length=1), limit: int = Query(20, ge=1, le=200)):
    store = await get_store()
    entities = await store.search_claim_entities(q, limit=limit)
    return [
        {
            "entity_id": str(e.get("entity_id", "")),
            "name": e.get("name", ""),
            "type": e.get("entity_type", ""),
            "claim_count": e.get("claim_count", 0),
        }
        for e in entities
    ]


@app.get("/api/entities/claims", dependencies=[Depends(verify_key)])
async def claim_entities(claim_id: str):
    from uuid import UUID
    store = await get_store()
    try:
        cid = UUID(claim_id)
    except ValueError:
        raise HTTPException(400, "Invalid claim ID")
    entities = await store.get_entities_for_claim(cid)
    return [
        {
            "entity_id": str(e.get("entity_id", "")),
            "name": e.get("name", ""),
            "type": e.get("entity_type", ""),
        }
        for e in entities
    ]


@app.get("/api/entities/top", dependencies=[Depends(verify_key)])
async def top_entities(limit: int = Query(30, ge=1, le=500)):
    store = await get_store()
    entities = await store.list_claim_entities(limit=limit)
    return [
        {
            "entity_id": str(e.get("entity_id", "")),
            "name": e.get("name", ""),
            "type": e.get("entity_type", ""),
            "claim_count": e.get("claim_count", 0),
        }
        for e in entities
    ]


@app.post("/api/discover/run", dependencies=[Depends(require_admin)])
async def discover_run(request: Request):
    from helioryn.discovery import run_discovery_cycle

    config = AppConfig.load(_CONFIG_PATH)
    store = await get_store()
    ingested, skipped, errors = await run_discovery_cycle(config, store, max_queries=10, results_per_query=3)
    return {"ingested": ingested, "skipped": skipped, "errors": errors}


@app.get("/api/settings", dependencies=[Depends(require_admin)])
async def get_settings():
    store = await get_store()
    return await store.get_all_settings()


@app.post("/api/settings", dependencies=[Depends(require_admin)])
async def update_settings(request: Request):
    body = await request.json()
    store = await get_store()
    if not isinstance(body, dict):
        raise HTTPException(400, "Expected JSON object with key: value pairs")
    for key, value in body.items():
        await store.set_setting(key, str(value))
    await store.log_admin_action("settings_update", {"settings": list(body.keys())},
                                 request.client.host if request.client else "")
    return {"status": "ok", "updated": list(body.keys())}


# ── Credentials API (admin) ──────────────────────────────────────

@app.get("/api/credentials", dependencies=[Depends(require_admin)])
async def list_credentials():
    store = await get_store()
    creds = await store.list_credentials()
    for c in creds:
        if c.get("api_key"):
            c["api_key_masked"] = c["api_key"][:4] + "*" * (len(c["api_key"]) - 8) + c["api_key"][-4:] if len(c["api_key"]) > 10 else "***"
        else:
            c["api_key_masked"] = ""
    return creds


@app.post("/api/credentials", dependencies=[Depends(require_admin)])
async def create_credential(request: Request):
    body = await request.json()
    store = await get_store()
    cid = await store.create_credential(
        body.get("service_name", ""),
        body.get("api_key", ""),
        body.get("base_url", ""),
        body.get("description", ""),
    )
    await store.log_admin_action("credential_create", {"service_name": body.get("service_name")},
                                 request.client.host if request.client else "")
    return {"status": "ok", "credential_id": cid}


@app.put("/api/credentials/{credential_id}", dependencies=[Depends(require_admin)])
async def update_credential(credential_id: str, request: Request):
    body = await request.json()
    store = await get_store()
    ok = await store.update_credential(
        credential_id,
        api_key=body.get("api_key"),
        base_url=body.get("base_url"),
        description=body.get("description"),
        is_active=body.get("is_active"),
    )
    if not ok:
        raise HTTPException(404, "Credential not found")
    await store.log_admin_action("credential_update", {"credential_id": credential_id},
                                 request.client.host if request.client else "")
    return {"status": "ok"}


@app.delete("/api/credentials/{credential_id}", dependencies=[Depends(require_admin)])
async def delete_credential(credential_id: str, request: Request):
    store = await get_store()
    ok = await store.delete_credential(credential_id)
    if not ok:
        raise HTTPException(404, "Credential not found")
    await store.log_admin_action("credential_delete", {"credential_id": credential_id},
                                 request.client.host if request.client else "")
    return {"status": "ok"}


# ── Chat API (public) ─────────────────────────────────────────────


@app.post("/api/chat")
async def chat_api(request: Request):
    from helioryn.rag import answer_question
    from helioryn.config import AppConfig

    body = await request.json()
    question = body.get("question", "").strip()
    mode = body.get("mode", "public")
    gov_search = body.get("gov_search", False)

    if not question:
        raise HTTPException(400, "Question is required")

    try:
        config = AppConfig.load(_CONFIG_PATH)
        store = await get_store()
        result = await answer_question(
            question, mode, store,
            config=config,
            gov_search=gov_search,
        )
        return result
    except Exception as exc:
        return {
            "answer": "I encountered an error processing your question. The system may be temporarily unavailable.",
            "sources": [],
            "error": str(exc),
        }


# ── Document Upload API (admin only) ─────────────────────────────


@app.post("/api/documents/upload", dependencies=[Depends(require_admin)])
async def document_upload(request: Request):
    import tempfile
    from helioryn.ingest.documents.ingest import ingest_document

    store = await get_store()
    form = await request.form()
    file = form.get("file")
    if not file or not hasattr(file, "filename") or not file.filename:
        raise HTTPException(400, "No file provided")

    suffix = Path(file.filename).suffix if hasattr(Path, "suffix") else ""
    with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
        content = await file.read()
        tmp.write(content)
        tmp_path = tmp.name

    try:
        result = await ingest_document(store, tmp_path, title=file.filename)
    finally:
        try:
            os.unlink(tmp_path)
        except Exception:
            pass

    return result


@app.get("/api/documents", dependencies=[Depends(require_admin)])
async def list_documents(limit: int = Query(50, ge=1, le=500), offset: int = Query(0, ge=0)):
    store = await get_store()
    docs = await store.list_documents(limit=limit, offset=offset)
    return [
        {
            "source_id": str(d["source_id"]),
            "title": d.get("title", ""),
            "url": d.get("source_url", ""),
            "method": d.get("retrieval_method", ""),
            "uploaded_at": str(d.get("first_seen_at", "")),
        }
        for d in docs
    ]


@app.get("/api/relationships", dependencies=[Depends(verify_key)])
async def list_relationships(claim_id: str | None = None, limit: int = Query(30, ge=1, le=500)):
    from uuid import UUID

    store = await get_store()
    if claim_id:
        try:
            cid = UUID(claim_id)
        except ValueError:
            raise HTTPException(400, "Invalid claim ID")
        rels = await store.get_relationships_for_claim(cid, limit=limit)
    else:
        async with store._pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT r.*, src.canonical_text AS source_text, "
                "tgt.canonical_text AS target_text "
                "FROM claim_relationship r "
                "JOIN claim src ON src.claim_id = r.source_claim_id "
                "JOIN claim tgt ON tgt.claim_id = r.target_claim_id "
                "ORDER BY r.confidence DESC LIMIT $1", limit
            )
            rels = [dict(r) for r in rows]
    return [
        {
            "relationship_id": str(r.get("relationship_id", "")),
            "source_claim_id": str(r.get("source_claim_id", "")),
            "target_claim_id": str(r.get("target_claim_id", "")),
            "type": r.get("relationship_type", ""),
            "confidence": r.get("confidence", 0),
            "detected_by": r.get("detected_by", ""),
            "detected_at": str(r.get("detected_at", "")),
            "source_text": (r.get("source_text") or "")[:200],
            "target_text": (r.get("target_text") or "")[:200],
        }
        for r in rels
    ]


@app.get("/api/relationships/similar/{claim_id}", dependencies=[Depends(verify_key)])
async def similar_claims(claim_id: str, threshold: float = Query(0.85), limit: int = Query(10, ge=1, le=100)):
    from uuid import UUID

    store = await get_store()
    try:
        cid = UUID(claim_id)
    except ValueError:
        raise HTTPException(400, "Invalid claim ID")
    similar = await store.find_similar_claims(cid, threshold=threshold, limit=limit)
    return [
        {
            "claim_id": str(s.get("claim_id", "")),
            "text": (s.get("canonical_text") or "")[:200],
            "similarity": s.get("similarity", 0),
        }
        for s in similar
    ]


@app.get("/api/narratives/overlaps", dependencies=[Depends(verify_key)])
async def narrative_overlaps(limit: int = Query(50, ge=1, le=500)):
    store = await get_store()
    async with store._pool.acquire() as conn:
        rows = await conn.fetch("""
            SELECT o.*, na.name AS narrative_a_name, nb.name AS narrative_b_name
            FROM narrative_overlap o
            JOIN narrative na ON na.narrative_id = o.narrative_a_id
            JOIN narrative nb ON nb.narrative_id = o.narrative_b_id
            ORDER BY o.overlap_score DESC LIMIT $1
        """, limit)
    return [
        {
            "narrative_a_id": str(r["narrative_a_id"]),
            "narrative_b_id": str(r["narrative_b_id"]),
            "narrative_a_name": r["narrative_a_name"],
            "narrative_b_name": r["narrative_b_name"],
            "overlap_score": r["overlap_score"],
            "shared_entities": r.get("shared_entities", []),
        }
        for r in rows
    ]


@app.get("/api/narratives/evidence-density", dependencies=[Depends(verify_key)])
async def evidence_density_api(
    narrative_id: str | None = None,
    limit: int = Query(20, ge=1, le=200),
):
    store = await get_store()
    if narrative_id:
        from uuid import UUID
        try:
            nid = UUID(narrative_id)
        except ValueError:
            raise HTTPException(400, "Invalid narrative ID")
        async with store._pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT * FROM evidence_density WHERE narrative_id = $1", nid
            )
    else:
        async with store._pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT * FROM evidence_density ORDER BY claim_count DESC LIMIT $1", limit
            )
    return [dict(r) for r in rows]


@app.get("/api/narratives", dependencies=[Depends(verify_key)])
async def list_narratives(limit: int = Query(20, ge=1, le=200)):
    store = await get_store()
    narratives = await store.list_narratives(limit=limit)
    return [
        {
            "narrative_id": str(n.get("narrative_id", "")),
            "name": n.get("name", ""),
            "description": n.get("description", ""),
            "top_terms": n.get("top_terms", []),
            "claim_count": n.get("claim_count", 0),
            "created_at": str(n.get("created_at", "")),
        }
        for n in narratives
    ]


@app.get("/api/narratives/{narrative_id}", dependencies=[Depends(verify_key)])
async def get_narrative_claims(narrative_id: str, limit: int = Query(50, ge=1, le=200)):
    from uuid import UUID

    store = await get_store()
    try:
        nid = UUID(narrative_id)
    except ValueError:
        raise HTTPException(400, "Invalid narrative ID")
    claims = await store.get_narrative_claims(nid, limit=limit)
    return [
        {
            "claim_id": str(c.get("claim_id", "")),
            "text": (c.get("canonical_text") or "")[:200],
            "weight": c.get("weight", 0),
        }
        for c in claims
    ]


@app.get("/api/embeddings/count", dependencies=[Depends(verify_key)])
async def embedding_count():
    store = await get_store()
    return {"total_embeddings": await store.get_embedding_count()}


# --- Phase 2 Area 3: Claim Confidence ---


@app.get("/api/claims/{claim_id}/confidence", dependencies=[Depends(verify_key)])
async def claim_confidence(claim_id: str):
    from uuid import UUID
    store = await get_store()
    try:
        cid = UUID(claim_id)
    except ValueError:
        raise HTTPException(400, "Invalid claim ID")
    claim = await store.get_claim(cid)
    if not claim:
        raise HTTPException(404, "Claim not found")
    factors = await store.get_confidence_factors("claim", cid)
    result = await store.compute_claim_confidence(cid)
    composite = result["composite"]
    return {
        "claim_id": claim_id,
        "composite": composite,
        "factors": [
            {
                "factor_id": str(f["factor_id"]),
                "factor_type": f["factor_type"],
                "value": f["value"],
                "weight": f["weight"],
                "explanation": f["explanation"],
                "computed_at": str(f["computed_at"]),
            }
            for f in factors
        ],
    }


# --- Phase 2 Area 4: Source Behavior ---


@app.get("/api/sources/{source_id}/behavior", dependencies=[Depends(verify_key)])
async def source_behavior_api(source_id: str):
    from uuid import UUID
    store = await get_store()
    try:
        sid = UUID(source_id)
    except ValueError:
        raise HTTPException(400, "Invalid source ID")
    behavior = await store.get_source_behavior(sid)
    events = await store.get_source_behavior_events(sid, limit=20)
    if not behavior:
        raise HTTPException(404, "No behavior data for source")
    return {
        "source_id": source_id,
        "n_claims": behavior["n_claims"],
        "n_contradictions": behavior["n_contradictions"],
        "n_corrections": behavior["n_corrections"],
        "contradiction_rate": behavior["contradiction_rate"],
        "originality_ratio": behavior["originality_ratio"],
        "reliability_score": behavior["reliability_score"],
        "first_seen": str(behavior["first_seen"]),
        "last_seen": str(behavior["last_seen"]),
        "events": [
            {
                "event_id": str(e["event_id"]),
                "event_type": e["event_type"],
                "observed_at": str(e["observed_at"]),
                "details": e.get("details"),
            }
            for e in events
        ],
    }


# --- Phase 3 Area 8: Narrative Stability ---


@app.get("/api/narratives/{narrative_id}/stability", dependencies=[Depends(verify_key)])
async def narrative_stability_api(narrative_id: str):
    from uuid import UUID
    store = await get_store()
    try:
        nid = UUID(narrative_id)
    except ValueError:
        raise HTTPException(400, "Invalid narrative ID")
    async with store._pool.acquire() as conn:
        row = await conn.fetchrow("""
            SELECT narrative_id, name, stability_score, stability_label,
                   contradiction_density, source_count, source_diversity,
                   momentum, velocity, divergence
            FROM narrative WHERE narrative_id = $1
        """, nid)
    if not row:
        raise HTTPException(404, "Narrative not found")
    return dict(row)


# --- Phase 3 Area 6: Claim Mutations ---


@app.get("/api/claims/{claim_id}/mutations", dependencies=[Depends(verify_key)])
async def claim_mutations(claim_id: str):
    from uuid import UUID
    store = await get_store()
    try:
        cid = UUID(claim_id)
    except ValueError:
        raise HTTPException(400, "Invalid claim ID")
    async with store._pool.acquire() as conn:
        rows = await conn.fetch("""
            SELECT m.*, src.canonical_text AS source_text, tgt.canonical_text AS target_text
            FROM claim_mutation m
            JOIN claim src ON src.claim_id = m.source_claim_id
            JOIN claim tgt ON tgt.claim_id = m.target_claim_id
            WHERE m.source_claim_id = $1 OR m.target_claim_id = $1
            ORDER BY m.edit_distance ASC
        """, cid)
    return [
        {
            "mutation_id": str(r["mutation_id"]),
            "source_claim_id": str(r["source_claim_id"]),
            "target_claim_id": str(r["target_claim_id"]),
            "canonical_id": str(r["canonical_id"]),
            "mutation_type": r["mutation_type"],
            "edit_distance": r["edit_distance"],
            "embedding_similarity": r["embedding_similarity"],
            "detected_by": r["detected_by"],
            "detected_at": str(r["detected_at"]),
            "source_text": r.get("source_text", "")[:200],
            "target_text": r.get("target_text", "")[:200],
        }
        for r in rows
    ]


# --- Phase 5 Area 10: Annotations ---


@app.get("/api/annotations", dependencies=[Depends(verify_key)])
async def list_annotations(
    target_type: str = Query("claim"),
    target_id: str = Query(...),
    limit: int = Query(50, ge=1, le=500),
):
    from uuid import UUID
    store = await get_store()
    try:
        tid = UUID(target_id)
    except ValueError:
        raise HTTPException(400, "Invalid target ID")
    async with store._pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT * FROM annotation WHERE target_type = $1 AND target_id = $2 ORDER BY created_at DESC LIMIT $3",
            target_type, tid, limit,
        )
    return [dict(r) for r in rows]


@app.post("/api/annotations", dependencies=[Depends(verify_key)])
async def create_annotation(
    target_type: str = "claim",
    target_id: str = ...,
    author: str = "api",
    body: str = ...,
    tags: list[str] | None = None,
):
    from uuid import UUID, uuid4
    store = await get_store()
    try:
        tid = UUID(target_id)
    except ValueError:
        raise HTTPException(400, "Invalid target ID")
    aid = uuid4()
    async with store._pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO annotation (annotation_id, target_type, target_id, author, body, tags) "
            "VALUES ($1, $2, $3, $4, $5, $6)",
            aid, target_type, tid, author, body, tags or [],
        )
    return {"annotation_id": str(aid), "status": "created"}


# --- Phase 5 Area 10: Investigation ---


@app.get("/api/investigations", dependencies=[Depends(verify_key)])
async def list_investigations(limit: int = Query(20, ge=1, le=200)):
    store = await get_store()
    async with store._pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT * FROM investigation ORDER BY created_at DESC LIMIT $1", limit
        )
    return [dict(r) for r in rows]


# --- Phase 5 Area 10: Staging Queue ---


@app.get("/api/staging", dependencies=[Depends(verify_key)])
async def staging_queue(limit: int = Query(50, ge=1, le=500)):
    store = await get_store()
    async with store._pool.acquire() as conn:
        rows = await conn.fetch("""
            SELECT sq.*, c.canonical_text
            FROM staging_queue sq
            LEFT JOIN claim c ON c.claim_id = sq.target_id AND sq.target_type = 'claim'
            WHERE sq.status = 'pending' OR sq.status = 'flagged'
            ORDER BY sq.submitted_at ASC LIMIT $1
        """, limit)
    return [dict(r) for r in rows]


@app.post("/api/staging/{queue_id}/review", dependencies=[Depends(verify_key)])
async def review_staging_item(queue_id: str, status: str = "approved"):
    from uuid import UUID
    store = await get_store()
    try:
        qid = UUID(queue_id)
    except ValueError:
        raise HTTPException(400, "Invalid queue ID")
    async with store._pool.acquire() as conn:
        await conn.execute(
            "UPDATE staging_queue SET status = $1, reviewed_at = now(), reviewed_by = 'api' WHERE queue_id = $2",
            status, qid,
        )
    return {"queue_id": queue_id, "status": status}
