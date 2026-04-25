"""
Auth-protected simulation CRUD + solver endpoints.

Routes
------
POST   /my/projects                    create a Project for the current user
GET    /my/projects                    list current user's Projects
PATCH  /my/projects/{id}               update project name/description/color
DELETE /my/projects/{id}               hard-delete Project (cascade removes all simulations)

POST   /simulations/                   create Simulation (project_id in body)
GET    /simulations/{id}               get Simulation with embedded flowsheet + result
PUT    /simulations/{id}/flowsheet     upsert Flowsheet (nodes + edges JSON)
POST   /simulations/{id}/run           run FlowsheetSolver, persist SimulationResult
GET    /simulations/{id}/results       list results for a Simulation (0 or 1 today)
DELETE /simulations/{id}               hard-delete Simulation (cascade removes children)
"""

from __future__ import annotations

import time
import uuid
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.auth import get_current_user
from app.core.context_builder import build_prompt_context
from app.core.flowsheet_solver import FlowsheetSolver
from app.core.pinch import (
    ColdStream,
    HotStream,
    extract_streams_from_flowsheet,
    run_pinch_analysis,
)
from app.core.process_metrics import compute_enriched_result
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
    PinchRequest,
    PinchResultResponse,
    ProjectCreate,
    ProjectDetail,
    ProjectResponse,
    ProjectUpdate,
    SimulationCreateRequest,
    SimulationDetail,
    SimulationResponse,
    SimulationResultResponse,
    TemperatureIntervalOut,
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


@router.delete("/my/projects/{project_id}", status_code=204)
async def delete_project(
    project_id: str,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> None:
    """Hard-delete a Project and cascade-remove all its Simulations, Flowsheets, and Results."""
    project = await _get_project_for_user(project_id, db, user)
    await db.delete(project)
    await db.commit()


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

    t_start = time.monotonic()
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
    solve_time_ms = int((time.monotonic() - t_start) * 1000)

    streams        = raw.get("streams", {})
    energy_balance = raw.get("energy_balance", {})
    warnings       = raw.get("warnings", [])

    enriched = compute_enriched_result(
        raw, sim.flowsheet.nodes, sim.flowsheet.edges, solve_time_ms
    )

    node_summaries = raw.get("node_summaries", {})

    if sim.result is not None:
        sim.result.streams            = streams
        sim.result.energy_balance     = energy_balance
        sim.result.warnings           = warnings
        sim.result.process_metrics    = enriched["process_metrics"]
        sim.result.stream_annotations = enriched["stream_annotations"]
        sim.result.solver_diagnostics = enriched["solver_diagnostics"]
        sim.result.process_summary    = enriched["process_summary"]
        sim.result.node_summaries     = node_summaries
    else:
        db.add(SimulationResult(
            simulation_id=sim.id,
            streams=streams,
            energy_balance=energy_balance,
            warnings=warnings,
            process_metrics=enriched["process_metrics"],
            stream_annotations=enriched["stream_annotations"],
            solver_diagnostics=enriched["solver_diagnostics"],
            process_summary=enriched["process_summary"],
            node_summaries=node_summaries,
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


# ── Pinch Analysis ───────────────────────────────────────────────────────────

@router.post("/simulations/{sim_id}/pinch", response_model=PinchResultResponse)
async def run_pinch(
    sim_id: str,
    body: PinchRequest,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> PinchResultResponse:
    """
    Run Pinch Analysis on a solved simulation.

    Stream extraction
    -----------------
    • If ``body.streams`` is provided, those are used directly.
    • Otherwise streams are auto-extracted from heat_exchanger nodes in the
      flowsheet, using stored inlet/outlet temperatures and estimated CP.

    Returns minimum utility targets, the pinch temperature, temperature
    interval data, and composite curve points for the T-H diagram.
    """
    sim = await _load_simulation(sim_id, db, user)

    if sim.result is None:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="No simulation result — run the simulation first",
        )

    delta_T_min = body.delta_T_min

    # ── Resolve streams ───────────────────────────────────────────────────────
    if body.streams:
        hot_streams  = [
            HotStream(s.supply_temp, s.target_temp, s.cp, name=s.name)
            for s in body.streams if s.stream_type == "hot"
        ]
        cold_streams = [
            ColdStream(s.supply_temp, s.target_temp, s.cp, name=s.name)
            for s in body.streams if s.stream_type == "cold"
        ]
    else:
        if sim.flowsheet is None:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail="No flowsheet — provide streams explicitly in the request body",
            )
        hot_streams, cold_streams = extract_streams_from_flowsheet(
            sim.flowsheet.nodes,
            sim.flowsheet.edges,
            sim.result.streams or {},
        )

    if not hot_streams and not cold_streams:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=(
                "No heat-exchanging streams found. "
                "Add heat_exchanger nodes to the flowsheet or supply streams explicitly."
            ),
        )

    # ── Run algorithm ─────────────────────────────────────────────────────────
    try:
        pr = run_pinch_analysis(hot_streams, cold_streams, delta_T_min)
    except ValueError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc))

    # ── Energy saving potential ───────────────────────────────────────────────
    pm = sim.result.process_metrics or {}
    current_hot = (
        float(pm.get("total_heat_duty_kW") or 0.0) if isinstance(pm, dict)
        else getattr(pm, "total_heat_duty_kW", 0.0)
    )
    saving = max(0.0, current_hot - pr.q_h_min) if current_hot > 0 else None

    return PinchResultResponse(
        pinch_temperature=pr.pinch_temperature,
        q_h_min=pr.q_h_min,
        q_c_min=pr.q_c_min,
        delta_T_min=pr.delta_T_min,
        temperature_intervals=[
            TemperatureIntervalOut(
                t_high=iv.t_high,
                t_low=iv.t_low,
                hcp_sum=iv.hcp_sum,
                ccp_sum=iv.ccp_sum,
                delta_h=iv.delta_h,
                cascade_in=iv.cascade_in,
                cascade_out=iv.cascade_out,
            )
            for iv in pr.temperature_intervals
        ],
        hot_composite=pr.hot_composite,
        cold_composite=pr.cold_composite,
        above_pinch_streams=pr.above_pinch_streams,
        below_pinch_streams=pr.below_pinch_streams,
        current_hot_utility_kw=current_hot if current_hot > 0 else None,
        energy_saving_kw=round(saving, 3) if saving is not None else None,
    )


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
