"""
Auth-protected simulation CRUD + solver endpoints.

Routes
------
POST   /my/projects                    create a Project for the current user
GET    /my/projects                    list current user's Projects

POST   /simulations/                   create Simulation (project_id in body)
GET    /simulations/{id}               get Simulation with embedded flowsheet + result
PUT    /simulations/{id}/flowsheet     upsert Flowsheet (nodes + edges JSON)
POST   /simulations/{id}/run           run FlowsheetSolver, persist SimulationResult
GET    /simulations/{id}/results       list results for a Simulation (0 or 1 today)
DELETE /simulations/{id}               hard-delete Simulation (cascade removes children)
"""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.auth import get_current_user
from app.core.flowsheet_solver import FlowsheetSolver
from app.core.unit_ops import SimulationError
from app.db import get_db
from app.models.orm import (
    Flowsheet,
    Project,
    Simulation,
    SimulationResult,
    SimulationStatus,
    User,
)
from app.models.schemas import (
    FlowsheetCreate,
    FlowsheetResponse,
    ProjectCreate,
    ProjectDetail,
    ProjectResponse,
    ProjectUpdate,
    SimulationCreateRequest,
    SimulationDetail,
    SimulationResponse,
    SimulationResultResponse,
)

router = APIRouter(tags=["simulations"])


# ── Project helpers ───────────────────────────────────────────────────────────

@router.post("/my/projects", response_model=ProjectResponse, status_code=201)
async def create_project(
    body: ProjectCreate,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> ProjectResponse:
    """Create a Project owned by the authenticated user."""
    project = Project(user_id=user.id, name=body.name, description=body.description, color=body.color)
    db.add(project)
    await db.commit()
    await db.refresh(project)
    return ProjectResponse.model_validate(project)


@router.patch("/my/projects/{project_id}", response_model=ProjectResponse)
async def update_project(
    project_id: str,
    body: ProjectUpdate,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> ProjectResponse:
    """Update name, description, or color of a project owned by the caller."""
    project = await _get_project_for_user(project_id, db, user)
    if body.name is not None:
        project.name = body.name
    if body.description is not None:
        project.description = body.description
    if body.color is not None:
        project.color = body.color
    await db.commit()
    await db.refresh(project)
    return ProjectResponse.model_validate(project)


@router.get("/my/projects", response_model=list[ProjectDetail])
async def list_my_projects(
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> list[ProjectDetail]:
    """List all Projects owned by the authenticated user, newest first, with simulations embedded."""
    rows = await db.execute(
        select(Project)
        .where(Project.user_id == user.id)
        .options(selectinload(Project.simulations))
        .order_by(Project.created_at.desc())
    )
    return [ProjectDetail.model_validate(p) for p in rows.scalars()]


# ── Simulation CRUD ───────────────────────────────────────────────────────────

@router.post("/simulations/", response_model=SimulationResponse, status_code=201)
async def create_simulation(
    body: SimulationCreateRequest,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> SimulationResponse:
    """Create a Simulation under an existing Project owned by the caller."""
    await _get_project_for_user(body.project_id, db, user)   # 404 if not found/owned
    sim = Simulation(project_id=body.project_id, name=body.name)
    db.add(sim)
    await db.commit()
    await db.refresh(sim)
    return SimulationResponse.model_validate(sim)


@router.get("/simulations/{sim_id}", response_model=SimulationDetail)
async def get_simulation(
    sim_id: str,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> SimulationDetail:
    """Get a Simulation with its latest flowsheet and result embedded."""
    sim = await _load_simulation(sim_id, db, user)
    return SimulationDetail.model_validate(sim)


@router.put("/simulations/{sim_id}/flowsheet", response_model=FlowsheetResponse)
async def save_flowsheet(
    sim_id: str,
    body: FlowsheetCreate,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> FlowsheetResponse:
    """Create or replace the Flowsheet for a Simulation.

    Pydantic validates node/edge uniqueness before anything is persisted.
    """
    sim = await _load_simulation(sim_id, db, user)
    nodes = [n.model_dump() for n in body.nodes]
    edges = [e.model_dump() for e in body.edges]

    if sim.flowsheet is not None:
        fs = sim.flowsheet
        fs.nodes = nodes
        fs.edges = edges
    else:
        fs = Flowsheet(simulation_id=sim.id, nodes=nodes, edges=edges)
        db.add(fs)

    await db.commit()
    await db.refresh(fs)   # repopulate id / created_at from DB; avoids stale identity-map
    return FlowsheetResponse.model_validate(fs)


@router.post("/simulations/{sim_id}/run", response_model=SimulationResultResponse)
async def run_simulation(
    sim_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> SimulationResultResponse:
    """
    Solve the saved Flowsheet and persist the result.

    * If the flowsheet is missing or empty → 422
    * SimulationError from the solver      → 422 with solver message
    * Any other uncaught exception          → 500 with X-Trace-ID
    """
    sim = await _load_simulation(sim_id, db, user)
    trace_id: str = getattr(request.state, "trace_id", str(uuid.uuid4()))

    if sim.flowsheet is None:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="No flowsheet saved — call PUT /simulations/{id}/flowsheet first",
        )
    if not sim.flowsheet.nodes:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="Flowsheet has no nodes",
        )

    # Mark as running
    sim.status = SimulationStatus.RUNNING.value
    await db.commit()

    try:
        raw: dict[str, Any] = FlowsheetSolver(
            sim.flowsheet.nodes, sim.flowsheet.edges
        ).solve()
    except SimulationError as exc:
        sim.status = SimulationStatus.ERROR.value
        await db.commit()
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={"message": str(exc), "type": "simulation_error"},
        )
    except Exception:
        sim.status = SimulationStatus.ERROR.value
        await db.commit()
        raise HTTPException(
            status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={"message": "Solver raised an unexpected error", "trace_id": trace_id},
        )

    streams         = raw.get("streams", {})
    energy_balance  = raw.get("energy_balance", {})
    warnings        = raw.get("warnings", [])

    if sim.result is not None:
        sim.result.streams        = streams
        sim.result.energy_balance = energy_balance
        sim.result.warnings       = warnings
    else:
        db.add(SimulationResult(
            simulation_id=sim.id,
            streams=streams,
            energy_balance=energy_balance,
            warnings=warnings,
        ))

    sim.status = SimulationStatus.COMPLETE.value
    await db.commit()

    # Fetch the persisted result row directly
    result_row = (
        await db.execute(
            select(SimulationResult).where(SimulationResult.simulation_id == sim.id)
        )
    ).scalar_one()
    return SimulationResultResponse.model_validate(result_row)


@router.get("/simulations/{sim_id}/results", response_model=list[SimulationResultResponse])
async def get_results(
    sim_id: str,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> list[SimulationResultResponse]:
    """Return all results for a Simulation (currently 0 or 1; list ready for history)."""
    sim = await _load_simulation(sim_id, db, user)
    if sim.result is None:
        return []
    return [SimulationResultResponse.model_validate(sim.result)]


@router.delete("/simulations/{sim_id}", status_code=204)
async def delete_simulation(
    sim_id: str,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> None:
    """Hard-delete a Simulation and cascade-remove its Flowsheet and Result."""
    sim = await _load_simulation(sim_id, db, user)
    await db.delete(sim)
    await db.commit()


# ── Private helpers ───────────────────────────────────────────────────────────

async def _get_project_for_user(
    project_id: str, db: AsyncSession, user: User
) -> Project:
    project = await db.get(Project, project_id)
    if project is None or project.user_id != user.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Project not found")
    return project


async def _load_simulation(
    sim_id: str, db: AsyncSession, user: User
) -> Simulation:
    """Load Simulation with flowsheet + result eagerly. Returns 404 if not found
    or not owned by ``user`` (we never reveal the existence of another user's data)."""
    row = await db.execute(
        select(Simulation)
        .where(Simulation.id == sim_id)
        .options(
            selectinload(Simulation.flowsheet),
            selectinload(Simulation.result),
            selectinload(Simulation.project),
        )
    )
    sim = row.scalar_one_or_none()
    if sim is None or sim.project.user_id != user.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Simulation not found")
    return sim
