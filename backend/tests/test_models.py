"""
Tests for the new ORM models and Pydantic schemas:
  User, Project, Simulation, Flowsheet, SimulationResult
"""

import asyncio

import pytest
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.security import hash_password, verify_password
from app.models.orm import (
    Base,
    Flowsheet,
    Project,
    Simulation,
    SimulationResult,
    SimulationStatus,
    User,
    UserPlan,
)
from app.models.schemas import (
    EnergyBalance,
    FlowsheetCreate,
    FlowsheetUpdate,
    ProjectCreate,
    ProjectUpdate,
    SimulationCreate,
    SimulationResultCreate,
    SimulationUpdate,
    StreamState,
    UserCreate,
    UserUpdate,
)

# ── Shared async SQLite test engine ──────────────────────────────────────────

TEST_DB_FILE = "test_models.db"
TEST_DB_URL = f"sqlite+aiosqlite:///{TEST_DB_FILE}"

test_engine = create_async_engine(TEST_DB_URL, echo=False)
TestSession = async_sessionmaker(test_engine, expire_on_commit=False)


async def _create() -> None:
    async with test_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


async def _drop() -> None:
    async with test_engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await test_engine.dispose()


@pytest.fixture(autouse=True)
def db_tables():
    import os
    asyncio.run(_create())
    yield
    asyncio.run(_drop())
    if os.path.exists(TEST_DB_FILE):
        os.remove(TEST_DB_FILE)


# ══════════════════════════════════════════════════════════════════════════════
# Security helpers
# ══════════════════════════════════════════════════════════════════════════════

class TestSecurity:
    def test_hash_is_not_plaintext(self):
        assert hash_password("secret") != "secret"

    def test_verify_correct_password(self):
        h = hash_password("mypassword")
        assert verify_password("mypassword", h) is True

    def test_verify_wrong_password(self):
        h = hash_password("mypassword")
        assert verify_password("wrong", h) is False

    def test_two_hashes_differ(self):
        """bcrypt uses per-hash salts."""
        assert hash_password("abc") != hash_password("abc")


# ══════════════════════════════════════════════════════════════════════════════
# Pydantic schema validation
# ══════════════════════════════════════════════════════════════════════════════

class TestUserSchemas:
    def test_create_valid(self):
        u = UserCreate(email="a@b.com", password="longpassword")
        assert u.email == "a@b.com"

    def test_create_invalid_email(self):
        with pytest.raises(ValidationError):
            UserCreate(email="not-an-email", password="longpassword")

    def test_create_password_too_short(self):
        with pytest.raises(ValidationError):
            UserCreate(email="a@b.com", password="short")

    def test_create_blank_password(self):
        with pytest.raises(ValidationError):
            UserCreate(email="a@b.com", password="        ")

    def test_update_plan_valid(self):
        u = UserUpdate(plan=UserPlan.PRO)
        assert u.plan == UserPlan.PRO

    def test_update_all_optional(self):
        u = UserUpdate()
        assert u.plan is None


class TestProjectSchemas:
    def test_create_valid(self):
        p = ProjectCreate(name="My Project")
        assert p.name == "My Project"
        assert p.description is None

    def test_create_empty_name_invalid(self):
        with pytest.raises(ValidationError):
            ProjectCreate(name="")

    def test_update_all_optional(self):
        p = ProjectUpdate()
        assert p.name is None
        assert p.description is None

    def test_update_partial(self):
        p = ProjectUpdate(name="New Name")
        assert p.name == "New Name"


class TestSimulationSchemas:
    def test_create_valid(self):
        s = SimulationCreate(name="Run 1")
        assert s.name == "Run 1"

    def test_update_status_valid(self):
        s = SimulationUpdate(status=SimulationStatus.COMPLETE)
        assert s.status == SimulationStatus.COMPLETE

    def test_update_status_invalid(self):
        with pytest.raises(ValidationError):
            SimulationUpdate(status="exploded")


class TestFlowsheetSchemas:
    def _valid_nodes(self):
        return [
            {"id": "N1", "type": "feed", "label": "Feed",
             "data": {}, "position": {"x": 0, "y": 0}},
            {"id": "N2", "type": "flash_drum", "label": "Flash",
             "data": {}, "position": {"x": 200, "y": 0}},
        ]

    def _valid_edges(self):
        return [{"id": "E1", "source": "N1", "target": "N2"}]

    def test_create_valid(self):
        fs = FlowsheetCreate(nodes=self._valid_nodes(), edges=self._valid_edges())
        assert len(fs.nodes) == 2
        assert len(fs.edges) == 1

    def test_create_empty_is_valid(self):
        fs = FlowsheetCreate()
        assert fs.nodes == []
        assert fs.edges == []

    def test_duplicate_node_ids_rejected(self):
        nodes = self._valid_nodes()
        nodes[1]["id"] = "N1"   # duplicate
        with pytest.raises(ValidationError, match="unique"):
            FlowsheetCreate(nodes=nodes)

    def test_duplicate_edge_ids_rejected(self):
        edges = [
            {"id": "E1", "source": "N1", "target": "N2"},
            {"id": "E1", "source": "N2", "target": "N1"},
        ]
        with pytest.raises(ValidationError, match="unique"):
            FlowsheetCreate(nodes=self._valid_nodes(), edges=edges)

    def test_update_all_optional(self):
        fu = FlowsheetUpdate()
        assert fu.nodes is None
        assert fu.edges is None


class TestSimulationResultSchemas:
    def test_stream_state_valid(self):
        s = StreamState(
            flow=100, temperature=95, pressure=1.0, vapor_fraction=0.5,
            composition={"benzene": 0.5, "toluene": 0.5},
        )
        assert s.vapor_fraction == 0.5

    def test_stream_composition_not_summing_to_one(self):
        with pytest.raises(ValidationError, match="sum to 1"):
            StreamState(
                flow=1, temperature=20, pressure=1,
                composition={"a": 0.3, "b": 0.3},
            )

    def test_stream_empty_composition_allowed(self):
        s = StreamState(flow=1, temperature=20, pressure=1)
        assert s.composition == {}

    def test_result_create_valid(self):
        r = SimulationResultCreate(
            streams={"feed": StreamState(flow=1, temperature=20, pressure=1)},
            energy_balance=EnergyBalance(heat_duty_kW=10.0),
            warnings=["test warning"],
        )
        assert r.warnings == ["test warning"]
        assert r.energy_balance.heat_duty_kW == 10.0

    def test_result_create_defaults(self):
        r = SimulationResultCreate()
        assert r.streams == {}
        assert r.warnings == []


# ══════════════════════════════════════════════════════════════════════════════
# ORM round-trip tests (write to SQLite, read back)
# ══════════════════════════════════════════════════════════════════════════════

class TestOrmRoundTrip:
    """Verify that all new ORM models persist and relate correctly."""

    async def _seed(self):
        """Insert a complete User→Project→Simulation→Flowsheet+Result chain."""
        async with TestSession() as session:
            user = User(
                email="orm@test.dev",
                hashed_password=hash_password("testpass1"),
                plan=UserPlan.PRO.value,
            )
            session.add(user)
            await session.flush()

            project = Project(
                user_id=user.id,
                name="ORM Test Project",
                description="desc",
            )
            session.add(project)
            await session.flush()

            sim = Simulation(
                project_id=project.id,
                name="ORM Test Sim",
                status=SimulationStatus.COMPLETE.value,
            )
            session.add(sim)
            await session.flush()

            fs = Flowsheet(
                simulation_id=sim.id,
                nodes=[{"id": "N1", "type": "feed", "label": "Feed",
                        "data": {}, "position": {"x": 0, "y": 0}}],
                edges=[],
            )
            session.add(fs)

            result = SimulationResult(
                simulation_id=sim.id,
                streams={"feed": {"flow": 1, "temperature": 20, "pressure": 1,
                                  "vapor_fraction": None, "composition": {}}},
                energy_balance={"heat_duty_kW": 0},
                warnings=["no warnings"],
            )
            session.add(result)
            await session.commit()

            return user.id, project.id, sim.id

    def test_user_persisted(self):
        from sqlalchemy import select

        async def _run():
            uid, _, _ = await self._seed()
            async with TestSession() as session:
                user = await session.get(User, uid)
                assert user is not None
                assert user.email == "orm@test.dev"
                assert user.plan == UserPlan.PRO.value
                assert user.hashed_password != "testpass1"

        asyncio.run(_run())

    def test_project_linked_to_user(self):
        from sqlalchemy import select
        from sqlalchemy.orm import selectinload

        async def _run():
            uid, pid, _ = await self._seed()
            async with TestSession() as session:
                result = await session.execute(
                    select(Project)
                    .where(Project.id == pid)
                    .options(selectinload(Project.user))
                )
                project = result.scalar_one()
                assert project.user_id == uid
                assert project.user.email == "orm@test.dev"

        asyncio.run(_run())

    def test_simulation_status_enum(self):
        async def _run():
            _, _, sid = await self._seed()
            async with TestSession() as session:
                sim = await session.get(Simulation, sid)
                assert sim.status == SimulationStatus.COMPLETE.value

        asyncio.run(_run())

    def test_flowsheet_one_to_one(self):
        from sqlalchemy import select
        from sqlalchemy.orm import selectinload

        async def _run():
            _, _, sid = await self._seed()
            async with TestSession() as session:
                result = await session.execute(
                    select(Simulation)
                    .where(Simulation.id == sid)
                    .options(
                        selectinload(Simulation.flowsheet),
                        selectinload(Simulation.result),
                    )
                )
                sim = result.scalar_one()
                assert sim.flowsheet is not None
                assert len(sim.flowsheet.nodes) == 1
                assert sim.result is not None
                assert sim.result.warnings == ["no warnings"]

        asyncio.run(_run())

    def test_cascade_delete_user_removes_project(self):
        from sqlalchemy import select

        async def _run():
            uid, pid, _ = await self._seed()
            async with TestSession() as session:
                user = await session.get(User, uid)
                await session.delete(user)
                await session.commit()
                project = await session.get(Project, pid)
                assert project is None

        asyncio.run(_run())

    def test_cascade_delete_simulation_removes_flowsheet(self):
        from sqlalchemy import select

        async def _run():
            _, _, sid = await self._seed()
            async with TestSession() as session:
                sim = await session.get(Simulation, sid)
                await session.delete(sim)
                await session.commit()
                result = await session.execute(
                    select(Flowsheet).where(Flowsheet.simulation_id == sid)
                )
                assert result.scalar_one_or_none() is None

        asyncio.run(_run())

    def test_updated_at_populated_on_insert(self):
        async def _run():
            _, pid, _ = await self._seed()
            async with TestSession() as session:
                project = await session.get(Project, pid)
                assert project.updated_at is not None

        asyncio.run(_run())

    def test_unique_email_constraint(self):
        from sqlalchemy.exc import IntegrityError

        async def _run():
            await self._seed()
            async with TestSession() as session:
                dup = User(
                    email="orm@test.dev",
                    hashed_password=hash_password("other"),
                    plan=UserPlan.FREE.value,
                )
                session.add(dup)
                with pytest.raises(IntegrityError):
                    await session.commit()

        asyncio.run(_run())

    def test_flowsheet_unique_simulation_id(self):
        from sqlalchemy.exc import IntegrityError

        async def _run():
            _, _, sid = await self._seed()
            async with TestSession() as session:
                dup = Flowsheet(simulation_id=sid, nodes=[], edges=[])
                session.add(dup)
                with pytest.raises(IntegrityError):
                    await session.commit()

        asyncio.run(_run())
