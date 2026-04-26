from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.simulation import (
    COMPONENT_LIBRARY,
    CSTRInput,
    FlashInput,
    HeatExchangerInput,
    simulate_cstr,
    simulate_flash,
    simulate_heat_exchanger,
)
from app.core.unit_ops import DistillationShortcut, SimulationError, Stream
from app.db import get_db
from app.models.schemas import (
    CSTRRequest,
    CSTRResponse,
    DistillationPreviewRequest,
    DistillationPreviewResponse,
    FlashDrumRequest,
    FlashDrumResponse,
    HeatExchangerRequest,
    HeatExchangerResponse,
    SimProjectCreate,
    SimProjectResponse,
    RunCreate,
    RunResponse,
)
from app.services.simulation_service import SimulationService

router = APIRouter()


# ── Component library ─────────────────────────────────────────────────────────

@router.get("/components")
def list_components() -> list[dict]:
    return [
        {"id": k, "name": v.name, "molecular_weight": v.molecular_weight,
         "Tc": v.Tc, "Pc": v.Pc, "omega": v.omega}
        for k, v in COMPONENT_LIBRARY.items()
    ]


# ── Quick (stateless) simulations ────────────────────────────────────────────

@router.post("/simulate/flash", response_model=FlashDrumResponse)
def quick_flash(req: FlashDrumRequest) -> FlashDrumResponse:
    unknown = [c for c in req.components if c not in COMPONENT_LIBRARY]
    if unknown:
        raise HTTPException(422, f"Unknown components: {unknown}. "
                                 f"Available: {list(COMPONENT_LIBRARY)}")
    return simulate_flash(FlashInput(**req.model_dump()))  # type: ignore[return-value]


@router.post("/simulate/cstr", response_model=CSTRResponse)
def quick_cstr(req: CSTRRequest) -> CSTRResponse:
    return simulate_cstr(CSTRInput(**req.model_dump()))  # type: ignore[return-value]


@router.post("/simulate/hex", response_model=HeatExchangerResponse)
def quick_hex(req: HeatExchangerRequest) -> HeatExchangerResponse:
    return simulate_heat_exchanger(HeatExchangerInput(**req.model_dump()))  # type: ignore[return-value]


@router.post("/unit-ops/distillation/preview", response_model=DistillationPreviewResponse)
def distillation_preview(req: DistillationPreviewRequest) -> DistillationPreviewResponse:
    """Run Fenske-Underwood-Gilliland shortcut distillation without saving to a simulation."""
    unknown = [c for c in req.components if c not in COMPONENT_LIBRARY]
    if unknown:
        raise HTTPException(
            422,
            f"Unknown components: {unknown}. Available: {sorted(COMPONENT_LIBRARY)}",
        )
    if len(req.feed_composition) != len(req.components):
        raise HTTPException(
            422,
            f"feed_composition length ({len(req.feed_composition)}) must match "
            f"components length ({len(req.components)})",
        )

    total = sum(req.feed_composition)
    if total < 1e-12:
        raise HTTPException(422, "feed_composition values must not all be zero")
    z_norm = {c: v / total for c, v in zip(req.components, req.feed_composition)}

    feed = Stream(
        name="feed",
        temperature=req.feed_temperature_C,
        pressure=req.feed_pressure_bar,
        flow=req.feed_flow_mol_s,
        composition=z_norm,
        vapor_fraction=0.0,
    )

    try:
        outlets, summary = DistillationShortcut().solve(
            [feed],
            light_key=req.light_key,
            heavy_key=req.heavy_key,
            lk_recovery=req.lk_recovery,
            hk_recovery=req.hk_recovery,
            reflux_ratio=req.reflux_ratio,
            condenser_type=req.condenser_type,
            property_package=req.property_package,
            q=req.q,
        )
    except SimulationError as exc:
        raise HTTPException(422, str(exc))

    distillate, bottoms = outlets[0], outlets[1]
    R_min = summary["R_min"]

    return DistillationPreviewResponse(
        N_min=summary["N_min"],
        R_min=R_min,
        N_actual=summary["N_actual"],
        N_feed_tray=summary["N_feed_tray"],
        alpha_lk_hk=summary["alpha_lk_hk"],
        distillate_flow_mol_s=distillate.flow,
        bottoms_flow_mol_s=bottoms.flow,
        distillate_composition=dict(distillate.composition),
        bottoms_composition=dict(bottoms.composition),
        distillate_temperature_C=distillate.temperature,
        bottoms_temperature_C=bottoms.temperature,
        condenser_duty_kW=summary["condenser_duty_kW"],
        reboiler_duty_kW=summary["reboiler_duty_kW"],
        reflux_ratio=req.reflux_ratio,
        R_min_warning=req.reflux_ratio < 1.1 * R_min,
    )


# ── Projects ──────────────────────────────────────────────────────────────────

@router.post("/projects", response_model=SimProjectResponse, status_code=201)
async def create_project(
    data: SimProjectCreate, db: AsyncSession = Depends(get_db)
) -> SimProjectResponse:
    svc = SimulationService(db)
    return await svc.create_project(data)  # type: ignore[return-value]


@router.get("/projects", response_model=list[SimProjectResponse])
async def list_projects(db: AsyncSession = Depends(get_db)) -> list[SimProjectResponse]:
    svc = SimulationService(db)
    return await svc.list_projects()  # type: ignore[return-value]


@router.get("/projects/{project_id}", response_model=SimProjectResponse)
async def get_project(project_id: str, db: AsyncSession = Depends(get_db)) -> SimProjectResponse:
    svc = SimulationService(db)
    project = await svc.get_project(project_id)
    if project is None:
        raise HTTPException(404, "Project not found")
    return project  # type: ignore[return-value]


@router.delete("/projects/{project_id}", status_code=204)
async def delete_project(project_id: str, db: AsyncSession = Depends(get_db)) -> None:
    svc = SimulationService(db)
    if not await svc.delete_project(project_id):
        raise HTTPException(404, "Project not found")


# ── Runs ──────────────────────────────────────────────────────────────────────

@router.post("/projects/{project_id}/runs", response_model=RunResponse, status_code=201)
async def create_run(
    project_id: str, data: RunCreate, db: AsyncSession = Depends(get_db)
) -> RunResponse:
    svc = SimulationService(db)
    if await svc.get_project(project_id) is None:
        raise HTTPException(404, "Project not found")
    return await svc.create_run(project_id, data)  # type: ignore[return-value]


@router.get("/projects/{project_id}/runs", response_model=list[RunResponse])
async def list_runs(
    project_id: str, db: AsyncSession = Depends(get_db)
) -> list[RunResponse]:
    svc = SimulationService(db)
    return await svc.list_runs(project_id)  # type: ignore[return-value]
