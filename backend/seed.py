"""
ChemFlow seed script — idempotent demo data.

Creates (skips if already present):
  • one demo user    (demo@chemflow.dev / demo1234, plan=pro)
  • one project      (Benzene–Toluene Separation)
  • one simulation   (Flash Drum Study, status=complete)
  • one flowsheet    (feed → flash drum → liquid + vapour nodes)
  • one result       (actual Rachford-Rice outputs at 95 °C, 1 bar)

Run from the backend/ directory:
    python seed.py

Or inside Docker:
    docker compose exec backend python seed.py
"""

from __future__ import annotations

import asyncio
import sys

from sqlalchemy import select

from app.core.security import hash_password
from app.db import AsyncSessionLocal, init_db
from app.models.orm import (
    Flowsheet,
    Project,
    Simulation,
    SimulationResult,
    SimulationStatus,
    User,
    UserPlan,
)

# ── Demo data constants ───────────────────────────────────────────────────────

DEMO_EMAIL = "demo@chemflow.dev"
DEMO_PASSWORD = "demo1234"

# Flowsheet topology: feed stream → flash drum F-101 → two product streams
DEMO_NODES = [
    {
        "id": "N001",
        "type": "feed",
        "label": "Feed",
        "data": {
            "flow": 100.0,
            "temperature": 95.0,
            "pressure": 1.0,
            "composition": {"benzene": 0.5, "toluene": 0.5},
        },
        "position": {"x": 80, "y": 220},
    },
    {
        "id": "N002",
        "type": "flash_drum",
        "label": "Flash Drum F-101",
        "data": {"temperature": 95.0, "pressure": 1.0},
        "position": {"x": 320, "y": 220},
    },
    {
        "id": "N003",
        "type": "product",
        "label": "Liquid Product",
        "data": {"phase": "liquid"},
        "position": {"x": 580, "y": 340},
    },
    {
        "id": "N004",
        "type": "product",
        "label": "Vapour Product",
        "data": {"phase": "vapor"},
        "position": {"x": 580, "y": 100},
    },
]

DEMO_EDGES = [
    {"id": "E001", "source": "N001", "target": "N002", "label": "Feed → F-101"},
    {"id": "E002", "source": "N002", "target": "N003", "label": "Liquid"},
    {"id": "E003", "source": "N002", "target": "N004", "label": "Vapour"},
]

# Rachford-Rice flash results: benzene/toluene 50/50, 95 °C, 1 bar
# K_benzene = 1.569, K_toluene = 0.637  →  ψ ≈ 0.499
# x = [0.389, 0.611], y = [0.611, 0.389]
DEMO_STREAMS = {
    "feed": {
        "flow": 100.0,
        "temperature": 95.0,
        "pressure": 1.0,
        "vapor_fraction": None,
        "composition": {"benzene": 0.5, "toluene": 0.5},
    },
    "liquid": {
        "flow": 50.1,
        "temperature": 95.0,
        "pressure": 1.0,
        "vapor_fraction": 0.0,
        "composition": {"benzene": 0.389, "toluene": 0.611},
    },
    "vapor": {
        "flow": 49.9,
        "temperature": 95.0,
        "pressure": 1.0,
        "vapor_fraction": 1.0,
        "composition": {"benzene": 0.611, "toluene": 0.389},
    },
}

DEMO_ENERGY_BALANCE = {
    "heat_duty_kW": 0.0,
    "Q_in_kW": 0.0,
    "Q_out_kW": 0.0,
    "net_kW": 0.0,
}

DEMO_WARNINGS = [
    "Antoine constants for benzene valid 8–103 °C — extrapolation above 103 °C may reduce accuracy.",
    "Ideal (Raoult's law) K-values used; activity coefficient corrections not applied.",
]


# ── Seed logic ────────────────────────────────────────────────────────────────

async def seed() -> None:
    print("Ensuring database tables exist …")
    await init_db()

    async with AsyncSessionLocal() as session:
        # ── User ──────────────────────────────────────────────────────────────
        existing = await session.scalar(select(User).where(User.email == DEMO_EMAIL))
        if existing:
            print(f"  ✓ Demo user already exists ({DEMO_EMAIL}) — skipping.")
            return

        print(f"  + Creating user: {DEMO_EMAIL}")
        user = User(
            email=DEMO_EMAIL,
            hashed_password=hash_password(DEMO_PASSWORD),
            plan=UserPlan.PRO.value,
        )
        session.add(user)
        await session.flush()   # populate user.id before FK references

        # ── Project ───────────────────────────────────────────────────────────
        print("  + Creating project: Benzene–Toluene Separation")
        project = Project(
            user_id=user.id,
            name="Benzene–Toluene Separation",
            description=(
                "Steady-state process simulation of a binary vapour–liquid "
                "equilibrium system using an isothermal flash drum."
            ),
        )
        session.add(project)
        await session.flush()

        # ── Simulation ────────────────────────────────────────────────────────
        print("  + Creating simulation: Flash Drum Study")
        simulation = Simulation(
            project_id=project.id,
            name="Flash Drum Study",
            status=SimulationStatus.COMPLETE.value,
        )
        session.add(simulation)
        await session.flush()

        # ── Flowsheet ─────────────────────────────────────────────────────────
        print("  + Creating flowsheet (4 nodes, 3 edges)")
        flowsheet = Flowsheet(
            simulation_id=simulation.id,
            nodes=DEMO_NODES,
            edges=DEMO_EDGES,
        )
        session.add(flowsheet)

        # ── SimulationResult ──────────────────────────────────────────────────
        print("  + Creating simulation result (3 streams, energy balance)")
        result = SimulationResult(
            simulation_id=simulation.id,
            streams=DEMO_STREAMS,
            energy_balance=DEMO_ENERGY_BALANCE,
            warnings=DEMO_WARNINGS,
        )
        session.add(result)

        await session.commit()

    print("\nSeed complete.")
    print(f"  Login : {DEMO_EMAIL}")
    print(f"  Password: {DEMO_PASSWORD}")


if __name__ == "__main__":
    asyncio.run(seed())
