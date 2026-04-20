"""
Pydantic v2 schemas for ChemFlow.

Naming convention
─────────────────
  <Model>Create   — accepted fields when creating a resource (POST body)
  <Model>Update   — all-optional patch body (PATCH body)
  <Model>Response — full server response including computed fields
  <Model>Detail   — response that embeds related resources
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, EmailStr, Field, field_validator

from app.models.orm import SimulationStatus, UserPlan


# ══════════════════════════════════════════════════════════════════════════════
# Users
# ══════════════════════════════════════════════════════════════════════════════

class UserCreate(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8, description="Plain-text password (hashed before storage)")

    @field_validator("password")
    @classmethod
    def password_not_blank(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("Password must not be blank")
        return v


class UserUpdate(BaseModel):
    """Only the mutable fields a user (or admin) may change."""
    plan: UserPlan | None = None


class UserResponse(BaseModel):
    id: str
    email: str
    plan: UserPlan
    created_at: datetime

    model_config = {"from_attributes": True}


class UserPublic(BaseModel):
    """Minimal embedding — safe to include inside other objects."""
    id: str
    email: str

    model_config = {"from_attributes": True}


# ══════════════════════════════════════════════════════════════════════════════
# Projects
# ══════════════════════════════════════════════════════════════════════════════

class ProjectCreate(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    description: str | None = None
    color: str | None = Field(default=None, pattern=r'^#[0-9a-fA-F]{6}$')


class ProjectUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=255)
    description: str | None = None
    color: str | None = Field(default=None, pattern=r'^#[0-9a-fA-F]{6}$')


class ProjectResponse(BaseModel):
    id: str
    user_id: str
    name: str
    description: str | None
    color: str | None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class ProjectDetail(ProjectResponse):
    """Project with its simulations embedded."""
    simulations: list["SimulationResponse"] = []

    model_config = {"from_attributes": True}


# ══════════════════════════════════════════════════════════════════════════════
# Simulations
# ══════════════════════════════════════════════════════════════════════════════

class SimulationCreate(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    # project_id supplied via URL path, not body — kept here for service-layer use


class SimulationUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=255)
    status: SimulationStatus | None = None


class SimulationResponse(BaseModel):
    id: str
    project_id: str
    name: str
    status: SimulationStatus
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class SimulationDetail(SimulationResponse):
    """Simulation with its flowsheet and result embedded (if they exist)."""
    flowsheet: "FlowsheetResponse | None" = None
    result: "SimulationResultResponse | None" = None

    model_config = {"from_attributes": True}


# ══════════════════════════════════════════════════════════════════════════════
# Flowsheets
# ══════════════════════════════════════════════════════════════════════════════

class NodeData(BaseModel):
    """Per-unit operation parameters stored inside a node."""
    model_config = {"extra": "allow"}   # arbitrary domain fields allowed


class Node(BaseModel):
    id: str
    type: str = Field(
        description="Unit type key, e.g. 'feed', 'flash_drum', 'cstr', 'heat_exchanger', 'product'"
    )
    label: str
    data: dict[str, Any] = Field(default_factory=dict)
    position: dict[str, float] = Field(
        default_factory=lambda: {"x": 0.0, "y": 0.0},
        description="Canvas position {x, y}",
    )


class Edge(BaseModel):
    id: str
    source: str = Field(description="Source node id")
    target: str = Field(description="Target node id")
    label: str | None = None
    source_handle: str | None = Field(
        default=None,
        description="Outlet port index (as string) for multi-outlet nodes, e.g. '0'=liquid '1'=vapour on a flash drum",
    )


class FlowsheetCreate(BaseModel):
    nodes: list[Node] = Field(default_factory=list)
    edges: list[Edge] = Field(default_factory=list)

    @field_validator("nodes")
    @classmethod
    def node_ids_unique(cls, v: list[Node]) -> list[Node]:
        ids = [n.id for n in v]
        if len(ids) != len(set(ids)):
            raise ValueError("Node ids must be unique within a flowsheet")
        return v

    @field_validator("edges")
    @classmethod
    def edge_ids_unique(cls, v: list[Edge]) -> list[Edge]:
        ids = [e.id for e in v]
        if len(ids) != len(set(ids)):
            raise ValueError("Edge ids must be unique within a flowsheet")
        return v


class FlowsheetUpdate(BaseModel):
    nodes: list[Node] | None = None
    edges: list[Edge] | None = None


class FlowsheetResponse(BaseModel):
    id: str
    simulation_id: str
    nodes: list[dict[str, Any]]
    edges: list[dict[str, Any]]
    created_at: datetime

    model_config = {"from_attributes": True}


# ══════════════════════════════════════════════════════════════════════════════
# Simulation Results
# ══════════════════════════════════════════════════════════════════════════════

class StreamState(BaseModel):
    """Thermodynamic state of a process stream."""
    flow: float = Field(description="Molar flow (mol/s)")
    temperature: float = Field(description="Temperature (°C)")
    pressure: float = Field(description="Pressure (bar)")
    vapor_fraction: float | None = Field(default=None, ge=0.0, le=1.0)
    composition: dict[str, float] = Field(
        default_factory=dict,
        description="Component mole fractions {component_id: fraction}",
    )

    @field_validator("composition")
    @classmethod
    def composition_sums_to_one(cls, v: dict[str, float]) -> dict[str, float]:
        if v and abs(sum(v.values()) - 1.0) > 1e-4:
            raise ValueError("Stream composition mole fractions must sum to 1")
        return v


class EnergyBalance(BaseModel):
    heat_duty_kW: float = 0.0
    Q_in_kW: float = 0.0
    Q_out_kW: float = 0.0
    net_kW: float = 0.0


class SimulationResultCreate(BaseModel):
    streams: dict[str, StreamState] = Field(
        default_factory=dict,
        description="Named stream states: {stream_name → StreamState}",
    )
    energy_balance: EnergyBalance = Field(default_factory=EnergyBalance)
    warnings: list[str] = Field(default_factory=list)


class SimulationResultResponse(BaseModel):
    id: str
    simulation_id: str
    streams: dict[str, Any]
    energy_balance: dict[str, Any]
    warnings: list[Any]
    created_at: datetime

    model_config = {"from_attributes": True}


# ══════════════════════════════════════════════════════════════════════════════
# Auth
# ══════════════════════════════════════════════════════════════════════════════

class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: "UserResponse"


# ══════════════════════════════════════════════════════════════════════════════
# Simulation creation (top-level route — project_id in body)
# ══════════════════════════════════════════════════════════════════════════════

class SimulationCreateRequest(BaseModel):
    """Body for POST /api/simulations/ — project_id is required here because
    simulations are a top-level resource (not nested under /projects/{id})."""
    name: str = Field(min_length=1, max_length=255)
    project_id: str


# ══════════════════════════════════════════════════════════════════════════════
# Legacy quick-simulation schemas (unchanged — used by /api/simulate/* routes)
# ══════════════════════════════════════════════════════════════════════════════

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


# Legacy project schemas — wrap SimulationProject (no user FK, no updated_at)
class SimProjectCreate(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    description: str | None = None


class SimProjectResponse(BaseModel):
    id: str
    name: str
    description: str | None
    created_at: datetime

    model_config = {"from_attributes": True}


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


# Rebuild forward refs so nested schemas resolve correctly
ProjectDetail.model_rebuild()
SimulationDetail.model_rebuild()
