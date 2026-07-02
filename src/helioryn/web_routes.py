# Copyright (c) 2026 Ravenkey LLC. All rights reserved.
from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

_TEMPLATE_DIR = Path(__file__).parent / "templates"


def register_web_routes(app: FastAPI):
    _templates = Jinja2Templates(directory=str(_TEMPLATE_DIR))

    @app.get("/")
    async def root():
        return RedirectResponse(url="/chat")

    @app.get("/folio")
    async def folio_redirect():
        return RedirectResponse(url="/chat")

    @app.get("/chat", response_class=HTMLResponse)
    async def chat_page(request: Request):
        from helioryn.server import _check_session as _check_sesh
        user = _check_sesh(request)
        return _templates.TemplateResponse(request, "chat.html", {
            "is_authenticated": user is not None,
            "is_admin": user is not None and user.get("role") == "admin",
            "user": user,
            "current_page": "chat",
            "chat_mode": request.query_params.get("mode", "public"),
        })

    @app.get("/admin", response_class=HTMLResponse)
    async def admin_page(request: Request):
        from helioryn.server import _check_session as _check_sesh
        user = _check_sesh(request)
        if not user or user.get("role") != "admin":
            return RedirectResponse(url="/login?next=/admin")

        store = request.app.state.store
        stats = {}
        db_ok = True
        try:
            async with store._pool.acquire() as conn:
                rows = await conn.fetch(
                    "SELECT retrieval_method, COUNT(*)::int as cnt FROM source_snapshot GROUP BY retrieval_method ORDER BY 2 DESC"
                )
                stats["sources_by_method"] = dict(rows)
                stats["source_total"] = sum(cnt for _, cnt in rows)

                stats["claim_count"] = (await conn.fetchval("SELECT COUNT(*) FROM claim")) or 0
                stats["embedding_count"] = (await conn.fetchval("SELECT COUNT(*) FROM claim_embedding")) or 0
                stats["canonical_count"] = (await conn.fetchval("SELECT COUNT(*) FROM canonical_claim")) or 0

                oig_rows = await conn.fetch("""
                    SELECT ss.source_id, ss.title, ss.source_url, ss.retrieved_at,
                           (SELECT COUNT(*) FROM claim c WHERE c.source_id = ss.source_id) as claim_count
                    FROM source_snapshot ss
                    WHERE ss.retrieval_method = 'gov_seed'
                    ORDER BY ss.title
                """)
                stats["oig_reports"] = [dict(r) for r in oig_rows]
        except Exception as exc:
            db_ok = False
            stats["db_error"] = str(exc)

        # Health check — test Ollama
        ollama_ok = False
        try:
            import httpx
            from helioryn.config import AppConfig
            cfg = AppConfig.load(None)
            r = await httpx.AsyncClient(timeout=5).get(f"{cfg.ollama.base_url}/api/tags")
            ollama_ok = r.status_code == 200
        except Exception:
            ollama_ok = False

        return _templates.TemplateResponse(request, "admin.html", {
            "is_authenticated": True,
            "is_admin": True,
            "user": user,
            "current_page": "admin",
            "stats": stats,
            "db_ok": db_ok,
            "ollama_ok": ollama_ok,
        })
