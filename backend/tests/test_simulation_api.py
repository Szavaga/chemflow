"""
Async integration tests for the auth-protected simulation API.

Stack
-----
- httpx.AsyncClient + ASGITransport (no real HTTP server needed)
- File-based aiosqlite (avoids in-memory connection-isolation issues)
- init_db() is patched to a no-op so the FastAPI lifespan never tries Postgres
- get_db() dependency is overridden to use the test SQLite session
- Full auth flow: register → JWT → authenticated calls (no dependency shortcuts)
"""

from __future__ import annotations

import asyncio
import os
from unittest.mock import AsyncMock, patch

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.db import get_db
from app.models.orm import Base
from main import app

# ── Test database ─────────────────────────────────────────────────────────────

TEST_DB_FILE = "test_sims_api.db"
TEST_DB_URL  = f"sqlite+aiosqlite:///{TEST_DB_FILE}"

_engine       = create_async_engine(TEST_DB_URL, echo=False)
_SessionLocal = async_sessionmaker(_engine, expire_on_commit=False)


async def _override_get_db():   # type: ignore[return]
    async with _SessionLocal() as session:
        yield session


async def _create_tables() -> None:
    async with _engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


async def _drop_tables() -> None:
    async with _engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await _engine.dispose()


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def setup_db():
    """Create tables before each test, drop and delete the file after."""
    asyncio.run(_create_tables())
    yield
    asyncio.run(_drop_tables())
    if os.path.exists(TEST_DB_FILE):
        os.remove(TEST_DB_FILE)


pytestmark = pytest.mark.asyncio


@pytest_asyncio.fixture()
async def ac():
    """AsyncClient wired to the FastAPI ASGI app.

    * get_db is overridden to use the SQLite test database.
    * init_db is patched to a no-op so the lifespan never connects to Postgres.
    """
    app.dependency_overrides[get_db] = _override_get_db
    with patch("main.init_db", new_callable=AsyncMock):
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            yield client
    app.dependency_overrides.clear()


# ── Auth helpers ──────────────────────────────────────────────────────────────

_DEFAULT_EMAIL    = "user@example.com"
_DEFAULT_PASSWORD = "strongpassword"


async def _register(ac: AsyncClient, email=_DEFAULT_EMAIL, password=_DEFAULT_PASSWORD):
    return await ac.post(
        "/api/auth/register", json={"email": email, "password": password}
    )


async def _login(ac: AsyncClient, email=_DEFAULT_EMAIL, password=_DEFAULT_PASSWORD):
    return await ac.post(
        "/api/auth/login", json={"email": email, "password": password}
    )


@pytest_asyncio.fixture()
async def token(ac: AsyncClient) -> str:
    resp = await _register(ac)
    assert resp.status_code == 201, resp.text
    return resp.json()["access_token"]


@pytest_asyncio.fixture()
async def auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


@pytest_asyncio.fixture()
async def project_id(ac: AsyncClient, auth: dict) -> str:
    resp = await ac.post(
        "/api/my/projects",
        json={"name": "Test Project"},
        headers=auth,
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


@pytest_asyncio.fixture()
async def sim_id(ac: AsyncClient, auth: dict, project_id: str) -> str:
    resp = await ac.post(
        "/api/simulations/",
        json={"name": "Test Sim", "project_id": project_id},
        headers=auth,
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


# A minimal valid flowsheet: feed → flash_drum → two product sinks
_FLASH_FLOWSHEET = {
    "nodes": [
        {
            "id": "N1", "type": "feed", "label": "Feed",
            "data": {
                "composition": {"benzene": 0.5, "toluene": 0.5},
                "temperature_C": 95.0, "pressure_bar": 1.0, "flow_mol_s": 1.0,
            },
            "position": {"x": 0, "y": 0},
        },
        {"id": "N2", "type": "flash_drum", "label": "Flash",
         "data": {}, "position": {"x": 200, "y": 0}},
        {"id": "N3", "type": "product", "label": "Liquid",
         "data": {}, "position": {"x": 400, "y": 100}},
        {"id": "N4", "type": "product", "label": "Vapor",
         "data": {}, "position": {"x": 400, "y": -100}},
    ],
    "edges": [
        {"id": "E1", "source": "N1", "target": "N2"},
        {"id": "E2", "source": "N2", "target": "N3", "source_handle": "0"},
        {"id": "E3", "source": "N2", "target": "N4", "source_handle": "1"},
    ],
}


# ══════════════════════════════════════════════════════════════════════════════
# Auth endpoints
# ══════════════════════════════════════════════════════════════════════════════

class TestRegister:
    async def test_register_returns_201_and_token(self, ac):
        resp = await _register(ac)
        assert resp.status_code == 201
        body = resp.json()
        assert "access_token" in body
        assert body["token_type"] == "bearer"
        assert body["user"]["email"] == _DEFAULT_EMAIL

    async def test_register_duplicate_email_409(self, ac):
        await _register(ac)
        resp = await _register(ac)
        assert resp.status_code == 409

    async def test_register_invalid_email_422(self, ac):
        resp = await ac.post(
            "/api/auth/register", json={"email": "not-an-email", "password": "goodpassword"}
        )
        assert resp.status_code == 422
        errors = resp.json()["detail"]
        assert any(e["loc"][-1] == "email" for e in errors)

    async def test_register_short_password_422(self, ac):
        resp = await ac.post(
            "/api/auth/register", json={"email": "a@b.com", "password": "short"}
        )
        assert resp.status_code == 422

    async def test_register_blank_password_422(self, ac):
        resp = await ac.post(
            "/api/auth/register", json={"email": "a@b.com", "password": "        "}
        )
        assert resp.status_code == 422


class TestLogin:
    async def test_login_correct_credentials(self, ac):
        await _register(ac)
        resp = await _login(ac)
        assert resp.status_code == 200
        assert "access_token" in resp.json()

    async def test_login_wrong_password_401(self, ac):
        await _register(ac)
        resp = await _login(ac, password="wrongpassword")
        assert resp.status_code == 401

    async def test_login_unknown_email_401(self, ac):
        resp = await _login(ac, email="nobody@example.com")
        assert resp.status_code == 401


class TestMe:
    async def test_me_returns_user(self, ac, auth):
        resp = await ac.get("/api/auth/me", headers=auth)
        assert resp.status_code == 200
        assert resp.json()["email"] == _DEFAULT_EMAIL

    async def test_me_no_token_401(self, ac):
        # HTTPBearer returns 401 when no credentials are present (Starlette 1.0+)
        resp = await ac.get("/api/auth/me")
        assert resp.status_code == 401

    async def test_me_invalid_token_401(self, ac):
        resp = await ac.get("/api/auth/me", headers={"Authorization": "Bearer garbage"})
        assert resp.status_code == 401


# ══════════════════════════════════════════════════════════════════════════════
# Project helpers
# ══════════════════════════════════════════════════════════════════════════════

class TestMyProjects:
    async def test_create_project(self, ac, auth):
        resp = await ac.post(
            "/api/my/projects", json={"name": "Distillation Column"}, headers=auth
        )
        assert resp.status_code == 201
        body = resp.json()
        assert body["name"] == "Distillation Column"
        assert "id" in body

    async def test_list_projects_empty(self, ac, auth):
        resp = await ac.get("/api/my/projects", headers=auth)
        assert resp.status_code == 200
        assert resp.json() == []

    async def test_list_projects_after_create(self, ac, auth):
        await ac.post("/api/my/projects", json={"name": "P1"}, headers=auth)
        await ac.post("/api/my/projects", json={"name": "P2"}, headers=auth)
        resp = await ac.get("/api/my/projects", headers=auth)
        assert len(resp.json()) == 2

    async def test_project_isolated_between_users(self, ac):
        # Two separate users should not see each other's projects
        r1 = await _register(ac, email="user1@example.com")
        r2 = await _register(ac, email="user2@example.com")
        h1 = {"Authorization": f"Bearer {r1.json()['access_token']}"}
        h2 = {"Authorization": f"Bearer {r2.json()['access_token']}"}

        await ac.post("/api/my/projects", json={"name": "User1 Project"}, headers=h1)
        resp = await ac.get("/api/my/projects", headers=h2)
        assert resp.json() == []

    async def test_create_project_requires_auth(self, ac):
        resp = await ac.post("/api/my/projects", json={"name": "P"})
        assert resp.status_code == 401


# ══════════════════════════════════════════════════════════════════════════════
# POST /api/simulations/
# ══════════════════════════════════════════════════════════════════════════════

class TestCreateSimulation:
    async def test_create_returns_201(self, ac, auth, project_id):
        resp = await ac.post(
            "/api/simulations/",
            json={"name": "Run A", "project_id": project_id},
            headers=auth,
        )
        assert resp.status_code == 201
        body = resp.json()
        assert body["name"] == "Run A"
        assert body["project_id"] == project_id
        assert body["status"] == "draft"

    async def test_create_missing_name_422(self, ac, auth, project_id):
        resp = await ac.post(
            "/api/simulations/",
            json={"project_id": project_id},
            headers=auth,
        )
        assert resp.status_code == 422
        errors = resp.json()["detail"]
        assert any(e["loc"][-1] == "name" for e in errors)

    async def test_create_wrong_project_404(self, ac, auth):
        resp = await ac.post(
            "/api/simulations/",
            json={"name": "S", "project_id": "00000000-0000-0000-0000-000000000000"},
            headers=auth,
        )
        assert resp.status_code == 404

    async def test_create_requires_auth(self, ac, project_id):
        resp = await ac.post(
            "/api/simulations/", json={"name": "S", "project_id": project_id}
        )
        assert resp.status_code == 401

    async def test_cannot_create_in_other_users_project(self, ac):
        r1 = await _register(ac, email="owner@example.com")
        r2 = await _register(ac, email="thief@example.com")
        h1 = {"Authorization": f"Bearer {r1.json()['access_token']}"}
        h2 = {"Authorization": f"Bearer {r2.json()['access_token']}"}

        proj = await ac.post("/api/my/projects", json={"name": "P"}, headers=h1)
        pid = proj.json()["id"]

        resp = await ac.post(
            "/api/simulations/",
            json={"name": "Stolen", "project_id": pid},
            headers=h2,
        )
        assert resp.status_code == 404


# ══════════════════════════════════════════════════════════════════════════════
# GET /api/simulations/{id}
# ══════════════════════════════════════════════════════════════════════════════

class TestGetSimulation:
    async def test_get_returns_simulation(self, ac, auth, sim_id):
        resp = await ac.get(f"/api/simulations/{sim_id}", headers=auth)
        assert resp.status_code == 200
        body = resp.json()
        assert body["id"] == sim_id
        assert body["flowsheet"] is None
        assert body["result"] is None

    async def test_get_not_found_404(self, ac, auth):
        resp = await ac.get("/api/simulations/no-such-id", headers=auth)
        assert resp.status_code == 404

    async def test_get_other_users_sim_404(self, ac, sim_id):
        r2 = await _register(ac, email="other@example.com")
        h2 = {"Authorization": f"Bearer {r2.json()['access_token']}"}
        resp = await ac.get(f"/api/simulations/{sim_id}", headers=h2)
        assert resp.status_code == 404


# ══════════════════════════════════════════════════════════════════════════════
# PUT /api/simulations/{id}/flowsheet
# ══════════════════════════════════════════════════════════════════════════════

class TestSaveFlowsheet:
    async def test_save_creates_flowsheet(self, ac, auth, sim_id):
        resp = await ac.put(
            f"/api/simulations/{sim_id}/flowsheet",
            json=_FLASH_FLOWSHEET,
            headers=auth,
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["simulation_id"] == sim_id
        assert len(body["nodes"]) == 4
        assert len(body["edges"]) == 3

    async def test_save_replaces_existing_flowsheet(self, ac, auth, sim_id):
        # First save
        await ac.put(
            f"/api/simulations/{sim_id}/flowsheet",
            json=_FLASH_FLOWSHEET,
            headers=auth,
        )
        # Second save with different node count
        minimal = {"nodes": [
            {"id": "N1", "type": "feed", "label": "F",
             "data": {"composition": {"water": 1.0}, "temperature_C": 25.0,
                      "pressure_bar": 1.0, "flow_mol_s": 1.0},
             "position": {"x": 0, "y": 0}},
            {"id": "N2", "type": "product", "label": "P",
             "data": {}, "position": {"x": 100, "y": 0}},
        ], "edges": [{"id": "E1", "source": "N1", "target": "N2"}]}
        resp = await ac.put(
            f"/api/simulations/{sim_id}/flowsheet", json=minimal, headers=auth
        )
        assert resp.status_code == 200
        assert len(resp.json()["nodes"]) == 2

    async def test_save_empty_flowsheet_valid(self, ac, auth, sim_id):
        resp = await ac.put(
            f"/api/simulations/{sim_id}/flowsheet",
            json={"nodes": [], "edges": []},
            headers=auth,
        )
        assert resp.status_code == 200

    async def test_save_duplicate_node_ids_422(self, ac, auth, sim_id):
        bad = {
            "nodes": [
                {"id": "N1", "type": "feed", "label": "F",
                 "data": {}, "position": {"x": 0, "y": 0}},
                {"id": "N1", "type": "product", "label": "P",
                 "data": {}, "position": {"x": 100, "y": 0}},
            ],
            "edges": [],
        }
        resp = await ac.put(
            f"/api/simulations/{sim_id}/flowsheet", json=bad, headers=auth
        )
        assert resp.status_code == 422

    async def test_save_flowsheet_requires_auth(self, ac, sim_id):
        resp = await ac.put(
            f"/api/simulations/{sim_id}/flowsheet", json=_FLASH_FLOWSHEET
        )
        assert resp.status_code == 401

    async def test_get_reflects_saved_flowsheet(self, ac, auth, sim_id):
        await ac.put(
            f"/api/simulations/{sim_id}/flowsheet",
            json=_FLASH_FLOWSHEET,
            headers=auth,
        )
        resp = await ac.get(f"/api/simulations/{sim_id}", headers=auth)
        assert resp.json()["flowsheet"] is not None
        assert len(resp.json()["flowsheet"]["nodes"]) == 4


# ══════════════════════════════════════════════════════════════════════════════
# POST /api/simulations/{id}/run
# ══════════════════════════════════════════════════════════════════════════════

class TestRunSimulation:
    async def test_run_returns_result(self, ac, auth, sim_id):
        await ac.put(
            f"/api/simulations/{sim_id}/flowsheet",
            json=_FLASH_FLOWSHEET,
            headers=auth,
        )
        resp = await ac.post(f"/api/simulations/{sim_id}/run", headers=auth)
        assert resp.status_code == 200
        body = resp.json()
        assert body["simulation_id"] == sim_id
        assert "streams" in body
        assert "energy_balance" in body
        assert "warnings" in body

    async def test_run_sets_status_complete(self, ac, auth, sim_id):
        await ac.put(
            f"/api/simulations/{sim_id}/flowsheet",
            json=_FLASH_FLOWSHEET,
            headers=auth,
        )
        await ac.post(f"/api/simulations/{sim_id}/run", headers=auth)
        get_resp = await ac.get(f"/api/simulations/{sim_id}", headers=auth)
        assert get_resp.json()["status"] == "complete"

    async def test_run_streams_contain_edges(self, ac, auth, sim_id):
        await ac.put(
            f"/api/simulations/{sim_id}/flowsheet",
            json=_FLASH_FLOWSHEET,
            headers=auth,
        )
        resp = await ac.post(f"/api/simulations/{sim_id}/run", headers=auth)
        streams = resp.json()["streams"]
        # Three edges → three stream entries
        assert len(streams) == 3
        assert "E1" in streams
        assert "E2" in streams
        assert "E3" in streams

    async def test_run_flash_conserves_mass(self, ac, auth, sim_id):
        await ac.put(
            f"/api/simulations/{sim_id}/flowsheet",
            json=_FLASH_FLOWSHEET,
            headers=auth,
        )
        resp = await ac.post(f"/api/simulations/{sim_id}/run", headers=auth)
        streams = resp.json()["streams"]
        # E1 is the feed; E2+E3 are liquid+vapor products
        feed_flow = streams["E1"]["flow"]
        liq_flow  = streams["E2"]["flow"]
        vap_flow  = streams["E3"]["flow"]
        assert abs(liq_flow + vap_flow - feed_flow) < 1e-4

    async def test_run_without_flowsheet_422(self, ac, auth, sim_id):
        resp = await ac.post(f"/api/simulations/{sim_id}/run", headers=auth)
        assert resp.status_code == 422
        assert "flowsheet" in resp.json()["detail"].lower()

    async def test_run_cycle_in_flowsheet_422(self, ac, auth, sim_id):
        # Feed → mixer A → mixer B → mixer A (cycle)
        cyclic = {
            "nodes": [
                {"id": "N1", "type": "feed", "label": "F",
                 "data": {"composition": {"water": 1.0},
                          "temperature_C": 25.0, "pressure_bar": 1.0, "flow_mol_s": 1.0},
                 "position": {"x": 0, "y": 0}},
                {"id": "N2", "type": "mixer", "label": "M1",
                 "data": {}, "position": {"x": 100, "y": 0}},
                {"id": "N3", "type": "mixer", "label": "M2",
                 "data": {}, "position": {"x": 200, "y": 0}},
            ],
            "edges": [
                {"id": "E1", "source": "N1", "target": "N2"},
                {"id": "E2", "source": "N2", "target": "N3"},
                {"id": "E3", "source": "N3", "target": "N2"},  # cycle
            ],
        }
        await ac.put(
            f"/api/simulations/{sim_id}/flowsheet", json=cyclic, headers=auth
        )
        resp = await ac.post(f"/api/simulations/{sim_id}/run", headers=auth)
        assert resp.status_code == 422
        detail = resp.json()["detail"]
        assert "cycle" in detail["message"].lower()

    async def test_run_idempotent_overwrites_result(self, ac, auth, sim_id):
        await ac.put(
            f"/api/simulations/{sim_id}/flowsheet",
            json=_FLASH_FLOWSHEET,
            headers=auth,
        )
        # Run twice; second run should overwrite first
        r1 = await ac.post(f"/api/simulations/{sim_id}/run", headers=auth)
        r2 = await ac.post(f"/api/simulations/{sim_id}/run", headers=auth)
        assert r1.status_code == 200
        assert r2.status_code == 200
        # Both return same sim result id
        assert r1.json()["simulation_id"] == r2.json()["simulation_id"]

    async def test_run_requires_auth(self, ac, sim_id):
        resp = await ac.post(f"/api/simulations/{sim_id}/run")
        assert resp.status_code == 401


# ══════════════════════════════════════════════════════════════════════════════
# GET /api/simulations/{id}/results
# ══════════════════════════════════════════════════════════════════════════════

class TestGetResults:
    async def test_results_empty_before_run(self, ac, auth, sim_id):
        resp = await ac.get(f"/api/simulations/{sim_id}/results", headers=auth)
        assert resp.status_code == 200
        assert resp.json() == []

    async def test_results_one_entry_after_run(self, ac, auth, sim_id):
        await ac.put(
            f"/api/simulations/{sim_id}/flowsheet",
            json=_FLASH_FLOWSHEET,
            headers=auth,
        )
        await ac.post(f"/api/simulations/{sim_id}/run", headers=auth)
        resp = await ac.get(f"/api/simulations/{sim_id}/results", headers=auth)
        assert resp.status_code == 200
        results = resp.json()
        assert len(results) == 1
        assert results[0]["simulation_id"] == sim_id

    async def test_results_not_found_404(self, ac, auth):
        resp = await ac.get("/api/simulations/no-such-id/results", headers=auth)
        assert resp.status_code == 404

    async def test_results_requires_auth(self, ac, sim_id):
        resp = await ac.get(f"/api/simulations/{sim_id}/results")
        assert resp.status_code == 401


# ══════════════════════════════════════════════════════════════════════════════
# DELETE /api/simulations/{id}
# ══════════════════════════════════════════════════════════════════════════════

class TestDeleteSimulation:
    async def test_delete_returns_204(self, ac, auth, sim_id):
        resp = await ac.delete(f"/api/simulations/{sim_id}", headers=auth)
        assert resp.status_code == 204

    async def test_delete_then_get_404(self, ac, auth, sim_id):
        await ac.delete(f"/api/simulations/{sim_id}", headers=auth)
        resp = await ac.get(f"/api/simulations/{sim_id}", headers=auth)
        assert resp.status_code == 404

    async def test_delete_not_found_404(self, ac, auth):
        resp = await ac.delete("/api/simulations/no-such-id", headers=auth)
        assert resp.status_code == 404

    async def test_delete_other_users_sim_404(self, ac, sim_id):
        r2 = await _register(ac, email="other@example.com")
        h2 = {"Authorization": f"Bearer {r2.json()['access_token']}"}
        resp = await ac.delete(f"/api/simulations/{sim_id}", headers=h2)
        assert resp.status_code == 404

    async def test_delete_requires_auth(self, ac, sim_id):
        resp = await ac.delete(f"/api/simulations/{sim_id}")
        assert resp.status_code == 401


# ══════════════════════════════════════════════════════════════════════════════
# Trace ID / error headers
# ══════════════════════════════════════════════════════════════════════════════

class TestTraceID:
    async def test_trace_id_header_on_success(self, ac, auth):
        resp = await ac.get("/api/auth/me", headers=auth)
        assert "x-trace-id" in resp.headers

    async def test_trace_id_header_on_404(self, ac, auth):
        resp = await ac.get("/api/simulations/no-such-id", headers=auth)
        assert "x-trace-id" in resp.headers

    async def test_trace_id_header_on_422(self, ac):
        resp = await ac.post("/api/auth/register", json={"email": "bad"})
        assert "x-trace-id" in resp.headers
