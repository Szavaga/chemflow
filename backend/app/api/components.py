"""
Chemical component API — dynamic component library.

Endpoints
─────────
GET  /api/components                    fuzzy search (global + user's project)
GET  /api/components/validate-antoine   Antoine range check for a CAS + T
GET  /api/components/{cas}              full component data by CAS number
POST /api/components                    create project-scoped custom component
PUT  /api/components/{id}               update project-scoped component
DELETE /api/components/{id}             soft-delete project-scoped component

All write endpoints require JWT auth (Authorization: Bearer <token>).
The read endpoints (GET) also require auth so user-scoped components are
visible only to their owner.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import get_current_user
from app.core.exceptions import ThermodynamicRangeError
from app.db import get_db
from app.models.orm import ChemicalComponent, Project, User
from app.models.schemas import (
    AntoineValidateResponse,
    ComponentCreate,
    ComponentResponse,
    ComponentUpdate,
)

router = APIRouter()


# ── Helpers ───────────────────────────────────────────────────────────────────

async def _get_component_or_404(
    comp_id: str, db: AsyncSession
) -> ChemicalComponent:
    row = await db.get(ChemicalComponent, comp_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Component not found")
    return row


async def _assert_project_owner(
    project_id: str, user: User, db: AsyncSession
) -> None:
    """Raise 403 if *user* does not own *project_id*."""
    proj = await db.get(Project, project_id)
    if proj is None or proj.user_id != user.id:
        raise HTTPException(status_code=403, detail="Not your project")


def _matches_search(comp: ChemicalComponent, q: str) -> bool:
    """Case-insensitive substring match on name or CAS number."""
    q = q.lower()
    return q in comp.name.lower() or q in comp.cas_number.lower()


# ── GET /api/components ───────────────────────────────────────────────────────

@router.get(
    "/components",
    response_model=list[ComponentResponse],
    summary="Search components",
)
async def search_components(
    search: str | None = Query(default=None, max_length=100,
                               description="Fuzzy search by name or CAS"),
    limit: int = Query(default=20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> list[ComponentResponse]:
    """Return global components + the user's project-scoped components.

    If *search* is provided, only records whose name or CAS number contains
    the search string (case-insensitive) are returned.
    """
    # Collect all project ids owned by this user
    proj_ids_result = await db.execute(
        select(Project.id).where(Project.user_id == user.id)
    )
    proj_ids = [r[0] for r in proj_ids_result.fetchall()]

    stmt = select(ChemicalComponent).where(
        or_(
            ChemicalComponent.is_global == True,  # noqa: E712
            ChemicalComponent.project_id.in_(proj_ids),
        )
    ).order_by(ChemicalComponent.name).limit(limit)

    result = await db.execute(stmt)
    rows: list[ChemicalComponent] = list(result.scalars().all())

    if search:
        rows = [r for r in rows if _matches_search(r, search)]

    return [ComponentResponse.model_validate(r) for r in rows]


# ── GET /api/components/validate-antoine ─────────────────────────────────────

@router.get(
    "/components/validate-antoine",
    response_model=AntoineValidateResponse,
    summary="Check if Antoine is valid at a given temperature",
)
async def validate_antoine(
    cas: str = Query(description="CAS Registry Number"),
    T: float = Query(description="Temperature to check (K)"),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> AntoineValidateResponse:
    """Return whether T falls within the component's Antoine valid range."""
    result = await db.execute(
        select(ChemicalComponent).where(ChemicalComponent.cas_number == cas)
    )
    comp = result.scalar_one_or_none()
    if comp is None:
        raise HTTPException(status_code=404, detail=f"Component {cas!r} not found")

    if comp.antoine_tmin is None or comp.antoine_tmax is None:
        return AntoineValidateResponse(
            cas_number=cas,
            T_K=T,
            valid=True,          # no range data → assume valid (cannot validate)
            T_min_K=None,
            T_max_K=None,
            message="No Antoine range data available; validity cannot be confirmed",
        )

    valid = comp.antoine_tmin <= T <= comp.antoine_tmax
    if not valid:
        try:
            raise ThermodynamicRangeError(
                "vapor_pressure", T, comp.antoine_tmin, comp.antoine_tmax, comp.name
            )
        except ThermodynamicRangeError as exc:
            message = str(exc)
    else:
        message = f"T={T:.2f} K is within Antoine range [{comp.antoine_tmin:.2f}, {comp.antoine_tmax:.2f}] K"

    return AntoineValidateResponse(
        cas_number=cas,
        T_K=T,
        valid=valid,
        T_min_K=comp.antoine_tmin,
        T_max_K=comp.antoine_tmax,
        message=message,
    )


# ── GET /api/components/{cas} ─────────────────────────────────────────────────

@router.get(
    "/components/{cas}",
    response_model=ComponentResponse,
    summary="Get component by CAS number",
)
async def get_component(
    cas: str,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> ComponentResponse:
    result = await db.execute(
        select(ChemicalComponent).where(ChemicalComponent.cas_number == cas)
    )
    comp = result.scalar_one_or_none()
    if comp is None:
        raise HTTPException(status_code=404, detail=f"Component {cas!r} not found")

    # Project-scoped components are only visible to their project owner
    if not comp.is_global and comp.project_id:
        await _assert_project_owner(comp.project_id, user, db)

    return ComponentResponse.model_validate(comp)


# ── POST /api/components ──────────────────────────────────────────────────────

@router.post(
    "/components",
    response_model=ComponentResponse,
    status_code=201,
    summary="Create a project-scoped custom component",
)
async def create_component(
    body: ComponentCreate,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> ComponentResponse:
    """Create a custom component scoped to a project.

    Rejects if the CAS number already exists in the global library.
    """
    await _assert_project_owner(body.project_id, user, db)

    # Reject CAS already in global library
    existing = await db.execute(
        select(ChemicalComponent).where(
            ChemicalComponent.cas_number == body.cas_number,
            ChemicalComponent.is_global == True,  # noqa: E712
        )
    )
    if existing.scalar_one_or_none() is not None:
        raise HTTPException(
            status_code=409,
            detail=f"CAS {body.cas_number!r} already exists in the global library",
        )

    comp = ChemicalComponent(
        name=body.name,
        cas_number=body.cas_number,
        formula=body.formula,
        mw=body.mw,
        tc=body.tc,
        pc=body.pc,
        omega=body.omega,
        antoine_a=body.antoine_a,
        antoine_b=body.antoine_b,
        antoine_c=body.antoine_c,
        antoine_tmin=body.antoine_tmin,
        antoine_tmax=body.antoine_tmax,
        antoine_units=body.antoine_units,
        mu_coeffs=body.mu_coeffs,
        is_global=False,
        project_id=body.project_id,
        created_by=user.id,
    )
    db.add(comp)
    await db.commit()
    await db.refresh(comp)
    return ComponentResponse.model_validate(comp)


# ── PUT /api/components/{id} ──────────────────────────────────────────────────

@router.put(
    "/components/{comp_id}",
    response_model=ComponentResponse,
    summary="Update a project-scoped custom component",
)
async def update_component(
    comp_id: str,
    body: ComponentUpdate,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> ComponentResponse:
    comp = await _get_component_or_404(comp_id, db)

    if comp.is_global:
        raise HTTPException(status_code=403, detail="Global components cannot be edited")

    await _assert_project_owner(comp.project_id, user, db)

    for field, value in body.model_dump(exclude_unset=True).items():
        setattr(comp, field, value)

    await db.commit()
    await db.refresh(comp)
    return ComponentResponse.model_validate(comp)


# ── DELETE /api/components/{id} ───────────────────────────────────────────────

@router.delete(
    "/components/{comp_id}",
    status_code=204,
    summary="Delete a project-scoped custom component",
)
async def delete_component(
    comp_id: str,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> None:
    comp = await _get_component_or_404(comp_id, db)

    if comp.is_global:
        raise HTTPException(status_code=403, detail="Global components cannot be deleted")

    await _assert_project_owner(comp.project_id, user, db)

    await db.delete(comp)
    await db.commit()
