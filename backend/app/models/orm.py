"""
SQLAlchemy 2 ORM models for ChemFlow.

Table hierarchy
───────────────
users
 └─ projects  (user_id FK)
     └─ simulations  (project_id FK)
         ├─ flowsheets          (simulation_id FK, 1-to-1)
         └─ simulation_results  (simulation_id FK, 1-to-1)

Legacy tables kept for backward-compatibility with the quick-simulation API:
  simulation_projects → simulation_runs
"""

from __future__ import annotations

import enum
import uuid
from datetime import datetime, timezone

from sqlalchemy import (
    DateTime,
    ForeignKey,
    JSON,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


# ── Helpers ───────────────────────────────────────────────────────────────────

def _now() -> datetime:
    return datetime.now(timezone.utc)


def _uuid() -> str:
    return str(uuid.uuid4())


# ── Python enums (used by Pydantic; ORM stores as String) ────────────────────

class UserPlan(str, enum.Enum):
    FREE = "free"
    PRO = "pro"


class SimulationStatus(str, enum.Enum):
    DRAFT = "draft"
    RUNNING = "running"
    COMPLETE = "complete"
    ERROR = "error"


# ── Declarative base ──────────────────────────────────────────────────────────

class Base(DeclarativeBase):
    pass


# ── Users ─────────────────────────────────────────────────────────────────────

class User(Base):
    """Platform user — owns one or more Projects."""

    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    email: Mapped[str] = mapped_column(String(255), nullable=False, unique=True, index=True)
    hashed_password: Mapped[str] = mapped_column(String(255), nullable=False)
    plan: Mapped[str] = mapped_column(String(16), nullable=False, default=UserPlan.FREE.value)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_now
    )

    projects: Mapped[list["Project"]] = relationship(
        "Project", back_populates="user", cascade="all, delete-orphan"
    )


# ── Projects ──────────────────────────────────────────────────────────────────

class Project(Base):
    """A named container for one or more Simulations, owned by a User."""

    __tablename__ = "projects"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_now
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_now, onupdate=_now
    )

    color: Mapped[str | None] = mapped_column(String(7))

    user: Mapped["User"] = relationship("User", back_populates="projects")
    simulations: Mapped[list["Simulation"]] = relationship(
        "Simulation", back_populates="project", cascade="all, delete-orphan"
    )


# ── Simulations ───────────────────────────────────────────────────────────────

class Simulation(Base):
    """
    A single simulation run within a Project.
    Has an optional Flowsheet (process topology) and SimulationResult (outputs).
    """

    __tablename__ = "simulations"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    project_id: Mapped[str] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    status: Mapped[str] = mapped_column(
        String(32), nullable=False, default=SimulationStatus.DRAFT.value
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_now
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_now, onupdate=_now
    )

    project: Mapped["Project"] = relationship("Project", back_populates="simulations")
    flowsheet: Mapped["Flowsheet | None"] = relationship(
        "Flowsheet", back_populates="simulation",
        cascade="all, delete-orphan", uselist=False,
    )
    result: Mapped["SimulationResult | None"] = relationship(
        "SimulationResult", back_populates="simulation",
        cascade="all, delete-orphan", uselist=False,
    )


# ── Flowsheets ────────────────────────────────────────────────────────────────

class Flowsheet(Base):
    """
    Process topology (nodes + edges) for a Simulation.
    One-to-one with Simulation.

    nodes: list of process-unit descriptors
      [{id, type, label, data: {…}, position: {x, y}}]
    edges: list of stream connections
      [{id, source_node_id, target_node_id, label}]
    """

    __tablename__ = "flowsheets"
    __table_args__ = (UniqueConstraint("simulation_id"),)

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    simulation_id: Mapped[str] = mapped_column(
        ForeignKey("simulations.id", ondelete="CASCADE"), nullable=False
    )
    nodes: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    edges: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_now
    )

    simulation: Mapped["Simulation"] = relationship("Simulation", back_populates="flowsheet")


# ── SimulationResults ─────────────────────────────────────────────────────────

class SimulationResult(Base):
    """
    Computed outputs for a completed Simulation.
    One-to-one with Simulation.

    Core solver outputs
    -------------------
    streams:        {edge_id → {flow, temperature, pressure, vapor_fraction, composition}}
    energy_balance: {total_duty_kW, heating_kW, cooling_kW}
    warnings:       list of human-readable solver warnings (strings)

    Phase-2 enrichment (populated by process_metrics.compute_enriched_result)
    --------------------------------------------------------------------------
    process_metrics:    aggregate heat/work/conversion/recycle/pinch figures
    stream_annotations: per-stream role + phase classification
    solver_diagnostics: timing, iteration count, structured warning objects
    process_summary:    auto-generated plain-English paragraph for AI prompts

    Note: process_metrics … process_summary are nullable so that rows written
    before this schema version remain valid.  Run the following migration on
    any existing PostgreSQL database before deploying:

        ALTER TABLE simulation_results
            ADD COLUMN IF NOT EXISTS process_metrics     JSONB,
            ADD COLUMN IF NOT EXISTS stream_annotations  JSONB,
            ADD COLUMN IF NOT EXISTS solver_diagnostics  JSONB,
            ADD COLUMN IF NOT EXISTS process_summary     TEXT;
    """

    __tablename__ = "simulation_results"
    __table_args__ = (UniqueConstraint("simulation_id"),)

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    simulation_id: Mapped[str] = mapped_column(
        ForeignKey("simulations.id", ondelete="CASCADE"), nullable=False
    )
    streams: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    energy_balance: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    warnings: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_now
    )

    # Phase-2 enrichment fields
    process_metrics:    Mapped[dict | None] = mapped_column(JSON, nullable=True)
    stream_annotations: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    solver_diagnostics: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    process_summary:    Mapped[str | None]  = mapped_column(Text, nullable=True)
    # Per-node solver summaries — used to seed the MPC Control Studio
    node_summaries:     Mapped[dict | None] = mapped_column(JSON, nullable=True)

    simulation: Mapped["Simulation"] = relationship("Simulation", back_populates="result")


# ── Legacy: quick-simulation tables ───────────────────────────────────────────

class SimulationProject(Base):
    """Legacy container used by the /api/simulate quick-run endpoints."""

    __tablename__ = "simulation_projects"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now(timezone.utc)
    )

    runs: Mapped[list["SimulationRun"]] = relationship(
        "SimulationRun", back_populates="project", cascade="all, delete-orphan"
    )


class SimulationRun(Base):
    """Legacy per-unit-operation run record."""

    __tablename__ = "simulation_runs"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    project_id: Mapped[str] = mapped_column(
        ForeignKey("simulation_projects.id"), nullable=False
    )
    unit_type: Mapped[str] = mapped_column(String(64), nullable=False)
    inputs: Mapped[dict] = mapped_column(JSON, nullable=False)
    outputs: Mapped[dict | None] = mapped_column(JSON)
    status: Mapped[str] = mapped_column(String(32), default="pending")
    error_message: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now(timezone.utc)
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime)

    project: Mapped["SimulationProject"] = relationship(
        "SimulationProject", back_populates="runs"
    )
