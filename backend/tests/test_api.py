"""Integration tests for FastAPI routes using a file-based SQLite test database.

We avoid triggering the FastAPI lifespan (which connects to real Postgres) by
NOT using TestClient as a context manager.  Tables are created/dropped around
each test via a plain sync fixture that calls asyncio.run().
"""

import asyncio
import os

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.db import get_db
from app.models.orm import Base
from main import app

# ── File-based async SQLite — all sessions share the same DB ─────────────────

TEST_DB_FILE = "test_chemflow.db"
TEST_DB_URL = f"sqlite+aiosqlite:///{TEST_DB_FILE}"

test_engine = create_async_engine(TEST_DB_URL, echo=False)
TestingSessionLocal = async_sessionmaker(test_engine, expire_on_commit=False)


async def override_get_db():  # type: ignore[return]
    async with TestingSessionLocal() as session:
        yield session


async def _create_tables() -> None:
    async with test_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


async def _drop_tables() -> None:
    async with test_engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await test_engine.dispose()


@pytest.fixture(autouse=True)
def setup_db():
    """Create all tables before each test; drop them after."""
    asyncio.run(_create_tables())
    yield
    asyncio.run(_drop_tables())
    # Remove the file so the next test starts completely fresh
    if os.path.exists(TEST_DB_FILE):
        os.remove(TEST_DB_FILE)


@pytest.fixture()
def client():
    """
    TestClient WITHOUT a context manager so the FastAPI lifespan (which tries
    to connect to Postgres) is never triggered during tests.
    """
    app.dependency_overrides[get_db] = override_get_db
    yield TestClient(app, raise_server_exceptions=True)
    app.dependency_overrides.clear()


# ── Health ─────────────────────────────────────────────────────────────────────

class TestHealth:
    def test_health_ok(self, client):
        r = client.get("/api/health")
        assert r.status_code == 200
        assert r.json()["status"] == "ok"


# ── Component library ─────────────────────────────────────────────────────────

class TestComponents:
    def test_list_components(self, client):
        r = client.get("/api/components")
        assert r.status_code == 200
        data = r.json()
        assert len(data) == 8
        ids = {c["id"] for c in data}
        assert "benzene" in ids
        assert "water" in ids


# ── Quick simulations ─────────────────────────────────────────────────────────

class TestQuickSimulations:
    def test_flash_success(self, client):
        # 50/50 benzene/toluene bubble point ≈ 92 °C at 1 bar; use 95 °C.
        r = client.post("/api/simulate/flash", json={
            "components": ["benzene", "toluene"],
            "feed_flow": 100,
            "feed_composition": [0.5, 0.5],
            "temperature": 95,
            "pressure": 1.0,
        })
        assert r.status_code == 200
        body = r.json()
        assert body["converged"] is True
        assert 0 < body["vapor_fraction"] < 1

    def test_flash_unknown_component_422(self, client):
        r = client.post("/api/simulate/flash", json={
            "components": ["benzene", "unobtainium"],
            "feed_flow": 1,
            "feed_composition": [0.5, 0.5],
            "temperature": 80,
            "pressure": 1.0,
        })
        assert r.status_code == 422

    def test_cstr_success(self, client):
        r = client.post("/api/simulate/cstr", json={
            "feed_concentration": 2.0,
            "feed_flow": 1.0,
            "volume": 10.0,
            "temperature": 60,
            "pre_exponential": 1e6,
            "activation_energy": 50000,
            "reaction_order": 1.0,
        })
        assert r.status_code == 200
        body = r.json()
        assert body["converged"] is True
        assert 0 < body["conversion"] < 1

    def test_cstr_negative_concentration_422(self, client):
        r = client.post("/api/simulate/cstr", json={
            "feed_concentration": -1,
            "feed_flow": 1.0,
            "volume": 10.0,
            "temperature": 60,
            "pre_exponential": 1e6,
            "activation_energy": 50000,
        })
        assert r.status_code == 422

    def test_hex_success(self, client):
        r = client.post("/api/simulate/hex", json={
            "hot_inlet_temp": 150,
            "hot_outlet_temp": 90,
            "hot_flow": 2.0,
            "hot_Cp": 4200,
            "cold_inlet_temp": 25,
            "cold_flow": 3.0,
            "cold_Cp": 4200,
            "flow_arrangement": "counterflow",
        })
        assert r.status_code == 200
        body = r.json()
        assert body["converged"] is True
        assert body["heat_duty"] > 0

    def test_hex_invalid_arrangement_422(self, client):
        r = client.post("/api/simulate/hex", json={
            "hot_inlet_temp": 150,
            "hot_outlet_temp": 90,
            "hot_flow": 2.0,
            "hot_Cp": 4200,
            "cold_inlet_temp": 25,
            "cold_flow": 3.0,
            "cold_Cp": 4200,
            "flow_arrangement": "shellside",
        })
        assert r.status_code == 422


# ── Projects ──────────────────────────────────────────────────────────────────

class TestProjects:
    def _create(self, client, name="Test Project", description=None):
        payload = {"name": name}
        if description:
            payload["description"] = description
        return client.post("/api/projects", json=payload)

    def test_create_project(self, client):
        r = self._create(client)
        assert r.status_code == 201
        body = r.json()
        assert body["name"] == "Test Project"
        assert "id" in body

    def test_list_projects_empty(self, client):
        r = client.get("/api/projects")
        assert r.status_code == 200
        assert r.json() == []

    def test_list_projects_after_create(self, client):
        self._create(client, "P1")
        self._create(client, "P2")
        r = client.get("/api/projects")
        assert r.status_code == 200
        assert len(r.json()) == 2

    def test_get_project(self, client):
        pid = self._create(client).json()["id"]
        r = client.get(f"/api/projects/{pid}")
        assert r.status_code == 200
        assert r.json()["id"] == pid

    def test_get_project_not_found(self, client):
        r = client.get("/api/projects/nonexistent-id")
        assert r.status_code == 404

    def test_delete_project(self, client):
        pid = self._create(client).json()["id"]
        r = client.delete(f"/api/projects/{pid}")
        assert r.status_code == 204
        assert client.get(f"/api/projects/{pid}").status_code == 404

    def test_delete_project_not_found(self, client):
        r = client.delete("/api/projects/nonexistent-id")
        assert r.status_code == 404

    def test_create_project_empty_name_422(self, client):
        r = client.post("/api/projects", json={"name": ""})
        assert r.status_code == 422


# ── Runs ──────────────────────────────────────────────────────────────────────

class TestRuns:
    def _project_id(self, client):
        return client.post("/api/projects", json={"name": "P"}).json()["id"]

    def test_create_flash_run(self, client):
        pid = self._project_id(client)
        r = client.post(f"/api/projects/{pid}/runs", json={
            "unit_type": "flash_drum",
            "inputs": {
                "components": ["benzene", "toluene"],
                "feed_flow": 100,
                "feed_composition": [0.5, 0.5],
                "temperature": 80,
                "pressure": 1.0,
            },
        })
        assert r.status_code == 201
        body = r.json()
        assert body["status"] == "success"
        assert body["outputs"]["converged"] is True

    def test_create_cstr_run(self, client):
        pid = self._project_id(client)
        r = client.post(f"/api/projects/{pid}/runs", json={
            "unit_type": "cstr",
            "inputs": {
                "feed_concentration": 2.0,
                "feed_flow": 1.0,
                "volume": 10.0,
                "temperature": 60,
                "pre_exponential": 1e6,
                "activation_energy": 50000,
                "reaction_order": 1.0,
            },
        })
        assert r.status_code == 201
        assert r.json()["status"] == "success"

    def test_create_hex_run(self, client):
        pid = self._project_id(client)
        r = client.post(f"/api/projects/{pid}/runs", json={
            "unit_type": "heat_exchanger",
            "inputs": {
                "hot_inlet_temp": 150, "hot_outlet_temp": 90,
                "hot_flow": 2.0, "hot_Cp": 4200,
                "cold_inlet_temp": 25, "cold_flow": 3.0, "cold_Cp": 4200,
                "flow_arrangement": "counterflow",
            },
        })
        assert r.status_code == 201
        assert r.json()["status"] == "success"

    def test_run_project_not_found(self, client):
        r = client.post("/api/projects/bad-id/runs", json={
            "unit_type": "cstr",
            "inputs": {"feed_concentration": 2.0, "feed_flow": 1.0,
                       "volume": 10.0, "temperature": 60,
                       "pre_exponential": 1e6, "activation_energy": 50000},
        })
        assert r.status_code == 404

    def test_run_unknown_unit_type_422(self, client):
        pid = self._project_id(client)
        r = client.post(f"/api/projects/{pid}/runs", json={
            "unit_type": "teleporter",
            "inputs": {},
        })
        assert r.status_code == 422

    def test_list_runs(self, client):
        pid = self._project_id(client)
        for _ in range(3):
            client.post(f"/api/projects/{pid}/runs", json={
                "unit_type": "cstr",
                "inputs": {
                    "feed_concentration": 2.0, "feed_flow": 1.0,
                    "volume": 10.0, "temperature": 60,
                    "pre_exponential": 1e6, "activation_energy": 50000,
                },
            })
        r = client.get(f"/api/projects/{pid}/runs")
        assert r.status_code == 200
        assert len(r.json()) == 3

    def test_run_bad_inputs_stored_as_failed(self, client):
        """A run with inputs that crash the solver should record status=failed."""
        pid = self._project_id(client)
        r = client.post(f"/api/projects/{pid}/runs", json={
            "unit_type": "flash_drum",
            "inputs": {
                "components": ["benzene", "does_not_exist"],
                "feed_flow": 1.0,
                "feed_composition": [0.5, 0.5],
                "temperature": 80,
                "pressure": 1.0,
            },
        })
        assert r.status_code == 201
        assert r.json()["status"] == "failed"
