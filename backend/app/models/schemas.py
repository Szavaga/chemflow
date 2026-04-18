from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


# ── Flash Drum ─────────────────────────────────────────────────────────────────

class FlashDrumRequest(BaseModel):
    components: list[str] = Field(min_length=2, examples=[["benzene", "toluene"]])
    feed_flow: float = Field(gt=0, description="Total feed flow (mol/s)")
    feed_composition: list[float] = Field(description="Mole fractions (will be normalised)")
    temperature: float = Field(description="Temperature (°C)")
    pressure: float = Field(gt=0, description="Pressure (bar)")


class FlashDrumResponse(BaseModel):
    vapor_fraction: float
    liquid_flow: float
    vapor_flow: float
    liquid_composition: list[float]
    vapor_composition: list[float]
    K_values: list[float]
    converged: bool
    message: str


# ── CSTR ──────────────────────────────────────────────────────────────────────

class CSTRRequest(BaseModel):
    reactant_name: str = "A"
    feed_concentration: float = Field(gt=0, description="Feed concentration Ca0 (mol/L)")
    feed_flow: float = Field(gt=0, description="Volumetric feed flow (L/s)")
    volume: float = Field(gt=0, description="Reactor volume (L)")
    temperature: float = Field(description="Operating temperature (°C)")
    pre_exponential: float = Field(gt=0, description="Arrhenius pre-exponential factor (1/s for n=1)")
    activation_energy: float = Field(gt=0, description="Activation energy (J/mol)")
    reaction_order: float = Field(default=1.0, description="Reaction order with respect to A")


class CSTRResponse(BaseModel):
    conversion: float
    outlet_concentration: float
    outlet_flow: float
    reaction_rate: float
    residence_time: float
    space_time_yield: float
    converged: bool
    message: str


# ── Heat Exchanger ─────────────────────────────────────────────────────────────

class HeatExchangerRequest(BaseModel):
    hot_inlet_temp: float = Field(description="Hot stream inlet temperature (°C)")
    hot_outlet_temp: float = Field(description="Hot stream outlet temperature (°C)")
    hot_flow: float = Field(gt=0, description="Hot stream mass flow (kg/s)")
    hot_Cp: float = Field(gt=0, description="Hot stream specific heat (J/kg·K)")
    cold_inlet_temp: float = Field(description="Cold stream inlet temperature (°C)")
    cold_flow: float = Field(gt=0, description="Cold stream mass flow (kg/s)")
    cold_Cp: float = Field(gt=0, description="Cold stream specific heat (J/kg·K)")
    flow_arrangement: str = Field(default="counterflow", pattern="^(counterflow|parallel)$")


class HeatExchangerResponse(BaseModel):
    cold_outlet_temp: float
    heat_duty: float
    lmtd: float
    UA: float
    effectiveness: float
    converged: bool
    message: str


# ── Projects ──────────────────────────────────────────────────────────────────

class ProjectCreate(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    description: str | None = None


class ProjectResponse(BaseModel):
    id: str
    name: str
    description: str | None
    created_at: datetime

    model_config = {"from_attributes": True}


# ── Simulation Runs ───────────────────────────────────────────────────────────

class RunCreate(BaseModel):
    unit_type: str = Field(pattern="^(flash_drum|cstr|heat_exchanger)$")
    inputs: dict[str, Any]


class RunResponse(BaseModel):
    id: str
    project_id: str
    unit_type: str
    inputs: dict[str, Any]
    outputs: dict[str, Any] | None
    status: str
    error_message: str | None
    created_at: datetime
    completed_at: datetime | None

    model_config = {"from_attributes": True}
