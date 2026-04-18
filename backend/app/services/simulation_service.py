import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.simulation import (
    CSTRInput,
    FlashInput,
    HeatExchangerInput,
    simulate_cstr,
    simulate_flash,
    simulate_heat_exchanger,
)
from app.models.orm import SimulationProject, SimulationRun
from app.models.schemas import ProjectCreate, RunCreate


class SimulationService:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    # ── Projects ──────────────────────────────────────────────────────────────

    async def create_project(self, data: ProjectCreate) -> SimulationProject:
        project = SimulationProject(
            id=str(uuid.uuid4()),
            name=data.name,
            description=data.description,
        )
        self.db.add(project)
        await self.db.commit()
        await self.db.refresh(project)
        return project

    async def list_projects(self) -> list[SimulationProject]:
        result = await self.db.execute(
            select(SimulationProject).order_by(SimulationProject.created_at.desc())
        )
        return list(result.scalars().all())

    async def get_project(self, project_id: str) -> SimulationProject | None:
        return await self.db.get(SimulationProject, project_id)

    async def delete_project(self, project_id: str) -> bool:
        project = await self.db.get(SimulationProject, project_id)
        if project is None:
            return False
        await self.db.delete(project)
        await self.db.commit()
        return True

    # ── Runs ──────────────────────────────────────────────────────────────────

    async def create_run(self, project_id: str, data: RunCreate) -> SimulationRun:
        run = SimulationRun(
            id=str(uuid.uuid4()),
            project_id=project_id,
            unit_type=data.unit_type,
            inputs=data.inputs,
            status="running",
        )
        self.db.add(run)
        await self.db.commit()

        try:
            run.outputs = self._dispatch(data.unit_type, data.inputs)
            run.status = "success"
        except Exception as exc:
            run.status = "failed"
            run.error_message = str(exc)

        run.completed_at = datetime.now(timezone.utc)
        await self.db.commit()
        await self.db.refresh(run)
        return run

    def _dispatch(self, unit_type: str, inputs: dict[str, Any]) -> dict[str, Any]:
        if unit_type == "flash_drum":
            result = simulate_flash(FlashInput(**inputs))
        elif unit_type == "cstr":
            result = simulate_cstr(CSTRInput(**inputs))
        elif unit_type == "heat_exchanger":
            result = simulate_heat_exchanger(HeatExchangerInput(**inputs))
        else:
            raise ValueError(f"Unknown unit type: {unit_type!r}")
        return result.__dict__

    async def list_runs(self, project_id: str) -> list[SimulationRun]:
        result = await self.db.execute(
            select(SimulationRun)
            .where(SimulationRun.project_id == project_id)
            .order_by(SimulationRun.created_at.desc())
        )
        return list(result.scalars().all())
