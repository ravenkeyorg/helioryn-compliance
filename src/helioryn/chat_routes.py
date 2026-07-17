# Copyright (c) 2026 Ravenkey LLC dba Helioryn. All rights reserved.
from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse

router = APIRouter(tags=["chat"])


def _require_user(request: Request) -> dict:
    from helioryn.server import _check_session
    user = _check_session(request)
    if not user:
        raise HTTPException(401, "Not authenticated")
    return user


def _get_store(request: Request):
    store = request.app.state.store
    if not store:
        raise HTTPException(503, "Store not available")
    return store


# ── Projects ────────────────────────────────────────────────


@router.get("/api/projects")
async def list_projects(request: Request):
    user = _require_user(request)
    store = _get_store(request)
    db_user = await store.get_user_by_username(user["username"])
    if not db_user:
        raise HTTPException(404, "User not found")
    projects = await store.list_projects(db_user["user_id"])
    return [
        {
            "project_id": str(p["project_id"]),
            "name": p["name"],
            "description": p.get("description", ""),
            "session_count": p.get("session_count", 0),
            "created_at": str(p["created_at"]),
            "updated_at": str(p["updated_at"]),
        }
        for p in projects
    ]


@router.post("/api/projects")
async def create_project(request: Request):
    user = _require_user(request)
    store = _get_store(request)
    body = await request.json()
    name = (body.get("name") or "").strip()
    if not name:
        raise HTTPException(400, "Project name is required")
    description = (body.get("description") or "").strip()
    db_user = await store.get_user_by_username(user["username"])
    if not db_user:
        raise HTTPException(404, "User not found")
    project = await store.create_project(db_user["user_id"], name, description)
    if not project:
        raise HTTPException(500, "Failed to create project")
    return {
        "project_id": str(project["project_id"]),
        "name": project["name"],
        "description": project.get("description", ""),
        "created_at": str(project["created_at"]),
        "updated_at": str(project["updated_at"]),
    }


@router.put("/api/projects/{project_id}")
async def update_project(project_id: str, request: Request):
    user = _require_user(request)
    store = _get_store(request)
    body = await request.json()
    name = (body.get("name") or "").strip()
    if not name:
        raise HTTPException(400, "Project name is required")
    description = body.get("description")
    db_user = await store.get_user_by_username(user["username"])
    if not db_user:
        raise HTTPException(404, "User not found")
    ok = await store.update_project(UUID(project_id), db_user["user_id"], name, description)
    if not ok:
        raise HTTPException(404, "Project not found")
    return {"status": "updated"}


@router.delete("/api/projects/{project_id}")
async def delete_project(project_id: str, request: Request):
    user = _require_user(request)
    store = _get_store(request)
    db_user = await store.get_user_by_username(user["username"])
    if not db_user:
        raise HTTPException(404, "User not found")
    ok = await store.delete_project(UUID(project_id), db_user["user_id"])
    if not ok:
        raise HTTPException(404, "Project not found")
    return {"status": "deleted"}


# ── Chat Sessions ──────────────────────────────────────────


@router.get("/api/sessions")
async def list_sessions(request: Request, project_id: str | None = None, limit: int = 50):
    user = _require_user(request)
    store = _get_store(request)
    db_user = await store.get_user_by_username(user["username"])
    if not db_user:
        raise HTTPException(404, "User not found")
    pid = UUID(project_id) if project_id else None
    sessions = await store.list_chat_sessions(db_user["user_id"], project_id=pid, limit=limit)
    return [
        {
            "session_id": str(s["session_id"]),
            "project_id": str(s["project_id"]) if s.get("project_id") else None,
            "title": s["title"],
            "mode": s["mode"],
            "created_at": str(s["created_at"]),
            "updated_at": str(s["updated_at"]),
        }
        for s in sessions
    ]


@router.post("/api/sessions")
async def create_session(request: Request):
    user = _require_user(request)
    store = _get_store(request)
    body = await request.json()
    project_id_str = body.get("project_id")
    mode = body.get("mode", "public")
    title = body.get("title", "New Chat")
    db_user = await store.get_user_by_username(user["username"])
    if not db_user:
        raise HTTPException(404, "User not found")
    pid = UUID(project_id_str) if project_id_str else None
    session = await store.create_chat_session(db_user["user_id"], project_id=pid, mode=mode, title=title)
    if not session:
        raise HTTPException(500, "Failed to create session")
    return {
        "session_id": str(session["session_id"]),
        "project_id": str(session["project_id"]) if session.get("project_id") else None,
        "title": session["title"],
        "mode": session["mode"],
        "messages": [],
        "created_at": str(session["created_at"]),
        "updated_at": str(session["updated_at"]),
    }


@router.get("/api/sessions/{session_id}")
async def get_session(session_id: str, request: Request):
    user = _require_user(request)
    store = _get_store(request)
    db_user = await store.get_user_by_username(user["username"])
    if not db_user:
        raise HTTPException(404, "User not found")
    session = await store.get_chat_session(UUID(session_id), db_user["user_id"])
    if not session:
        raise HTTPException(404, "Session not found")
    return {
        "session_id": str(session["session_id"]),
        "project_id": str(session["project_id"]) if session.get("project_id") else None,
        "title": session["title"],
        "mode": session["mode"],
        "messages": session.get("messages", []),
        "created_at": str(session["created_at"]),
        "updated_at": str(session["updated_at"]),
    }


@router.put("/api/sessions/{session_id}")
async def update_session(session_id: str, request: Request):
    user = _require_user(request)
    store = _get_store(request)
    body = await request.json()
    db_user = await store.get_user_by_username(user["username"])
    if not db_user:
        raise HTTPException(404, "User not found")

    messages = body.get("messages")
    title = body.get("title")

    # Auto-generate title from first user message if none provided
    if title is None and messages and len(messages) > 0:
        for msg in messages:
            if msg.get("role") == "user":
                text = msg.get("content", "")
                # Strip HTML for title
                import re as _re
                clean = _re.sub(r"<[^>]+>", "", text)
                title = clean[:60].strip() or "New Chat"
                break

    ok = await store.update_chat_session(
        UUID(session_id), db_user["user_id"],
        messages=messages, title=title,
    )
    if not ok:
        raise HTTPException(404, "Session not found")
    return {"status": "updated"}


@router.put("/api/sessions/{session_id}/title")
async def rename_session(session_id: str, request: Request):
    user = _require_user(request)
    store = _get_store(request)
    body = await request.json()
    title = (body.get("title") or "").strip()
    if not title:
        raise HTTPException(400, "Title is required")
    db_user = await store.get_user_by_username(user["username"])
    if not db_user:
        raise HTTPException(404, "User not found")
    ok = await store.update_chat_session(UUID(session_id), db_user["user_id"], title=title)
    if not ok:
        raise HTTPException(404, "Session not found")
    return {"status": "updated"}


@router.delete("/api/sessions/{session_id}")
async def delete_session(session_id: str, request: Request):
    user = _require_user(request)
    store = _get_store(request)
    db_user = await store.get_user_by_username(user["username"])
    if not db_user:
        raise HTTPException(404, "User not found")
    ok = await store.delete_chat_session(UUID(session_id), db_user["user_id"])
    if not ok:
        raise HTTPException(404, "Session not found")
    return {"status": "deleted"}


@router.patch("/api/sessions/{session_id}/project")
async def move_session_to_project(session_id: str, request: Request):
    user = _require_user(request)
    store = _get_store(request)
    body = await request.json()
    project_id = body.get("project_id")
    db_user = await store.get_user_by_username(user["username"])
    if not db_user:
        raise HTTPException(404, "User not found")
    ok = await store.update_chat_session(
        UUID(session_id), db_user["user_id"], project_id=project_id or "",
    )
    if not ok:
        raise HTTPException(404, "Session not found")
    return {"status": "moved", "project_id": project_id}
