# Copyright (c) 2026 Ravenkey LLC dba Helioryn. All rights reserved.
"""Tests for /folio query-param web routes.

Uses Starlette TestClient. Store is lazily created inside a lifespan context
so the asyncpg pool is bound to the same event loop TestClient uses.
"""

from contextlib import asynccontextmanager

import pytest
from fastapi import FastAPI
from starlette.testclient import TestClient

from helioryn.config import AppConfig
from helioryn.store import EventStore
from helioryn.web_routes import register_web_routes


_CONFIG_PATH = "helioryn/helioryn.toml"


@pytest.fixture(scope="module")
def app():
    _store_ref = {"store": None}

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        config = AppConfig.load(_CONFIG_PATH)
        store = EventStore(config.database_url)
        await store.connect()
        app.state.store = store
        _store_ref["store"] = store
        yield
        await store.close()

    app = FastAPI(lifespan=lifespan)
    register_web_routes(app)
    return app


@pytest.fixture(scope="module")
def client(app):
    with TestClient(app, raise_server_exceptions=False) as c:
        yield c


class TestRootRedirect:
    def test_root_redirects_to_folio(self, client):
        resp = client.get("/", follow_redirects=False)
        assert resp.status_code in (307, 302)
        assert "/folio" in resp.headers.get("location", "")

    def test_folio_returns_200(self, client):
        resp = client.get("/folio")
        assert resp.status_code == 200


class TestPageDispatch:
    def test_dashboard_page(self, client):
        resp = client.get("/folio?page=dashboard")
        assert resp.status_code == 200

    def test_claims_page(self, client):
        resp = client.get("/folio?page=claims")
        assert resp.status_code == 200

    def test_sources_page(self, client):
        resp = client.get("/folio?page=sources")
        assert resp.status_code == 200

    def test_narratives_page(self, client):
        resp = client.get("/folio?page=narratives")
        assert resp.status_code == 200

    def test_admin_page_redirects_when_unauthenticated(self, client):
        """Admin page requires login, redirects to /login."""
        resp = client.get("/folio?page=admin", follow_redirects=False)
        assert resp.status_code == 303
        assert "/login" in resp.headers.get("location", "")

    def test_invalid_page_renders_index(self, client):
        """Unknown page values fall through to the index template."""
        resp = client.get("/folio?page=nonexistent")
        assert resp.status_code == 200


class TestTopicFiltering:
    def test_ai_topic_filter(self, client):
        resp = client.get("/folio?page=claims&topic=ai")
        assert resp.status_code == 200

    def test_invalid_topic_renders_global_view(self, client):
        """Unknown topic values fall through to the default handler."""
        resp = client.get("/folio?page=claims&topic=nonexistent")
        assert resp.status_code == 200


class TestClaimDetail:
    def test_invalid_claim_id_returns_404(self, client):
        resp = client.get("/folio?claim=00000000-0000-0000-0000-000000000000")
        assert resp.status_code == 404

    def test_malformed_claim_id_returns_400(self, client):
        resp = client.get("/folio?claim=not-a-uuid")
        assert resp.status_code == 400


class TestSourceDetail:
    def test_invalid_source_id_returns_404(self, client):
        resp = client.get("/folio?source=00000000-0000-0000-0000-000000000000")
        assert resp.status_code == 404


class TestNarrativeDetail:
    def test_invalid_narrative_id_returns_404(self, client):
        resp = client.get("/folio?narrative=00000000-0000-0000-0000-000000000000")
        assert resp.status_code == 404


class TestExports:
    def test_export_json_invalid_claim_returns_404(self, client):
        resp = client.get("/folio?claim=00000000-0000-0000-0000-000000000000&export=json")
        assert resp.status_code == 404

    def test_export_invalid_format_returns_400(self, client):
        resp = client.get("/folio?claim=00000000-0000-0000-0000-000000000000&export=invalid")
        assert resp.status_code == 400


class TestSearch:
    def test_search_returns_200(self, client):
        resp = client.get("/search?q=test")
        assert resp.status_code == 200

    def test_search_empty_query_returns_200(self, client):
        resp = client.get("/search?q=")
        assert resp.status_code == 200


class TestPagination:
    def test_claims_page_one(self, client):
        resp = client.get("/folio?page=claims&p=1")
        assert resp.status_code == 200

    def test_negative_page_number_errors(self, client):
        """Negative page numbers cause a server error (no validation)."""
        resp = client.get("/folio?page=claims&p=-1")
        assert resp.status_code == 500
