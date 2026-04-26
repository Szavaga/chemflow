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

from typing import Literal

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
    target_handle: str | None = Field(
        default=None,
        description="Inlet port id for multi-inlet nodes, e.g. 'in0'/'in1' on a mixer",
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


class StructuredWarning(BaseModel):
    """A single structured solver warning emitted by process_metrics."""
    code:     str
    severity: str = Field(description="'info' | 'warning' | 'error'")
    message:  str
    node_id:  str | None = None


class ProcessMetrics(BaseModel):
    """Aggregate process figures computed from the solver result."""
    total_heat_duty_kW:    float
    total_cooling_duty_kW: float
    total_shaft_work_kW:   float
    overall_conversion:    dict[str, float] = Field(default_factory=dict)
    recycle_ratio:         dict[str, float] = Field(default_factory=dict)
    pinch_temperature:     float | None = None
    Q_H_min:               float | None = None
    energy_efficiency_pct: float | None = None


class StreamAnnotation(BaseModel):
    """Role and phase classification for a single process stream."""
    is_recycle:          bool
    is_product:          bool
    is_waste:            bool
    phase:               str = Field(description="'liquid' | 'vapor' | 'mixed'")
    distance_from_pinch: float | None = None


class SolverDiagnostics(BaseModel):
    """Timing, convergence, and structured warnings from the solver."""
    solve_time_ms:          int
    convergence_iterations: int
    converged:              bool
    tear_streams:           list[str]            = Field(default_factory=list)
    residuals:              list[float]           = Field(default_factory=list)
    warnings:               list[StructuredWarning] = Field(default_factory=list)


class SimulationResultResponse(BaseModel):
    id:             str
    simulation_id:  str
    streams:        dict[str, Any]
    energy_balance: dict[str, Any]
    warnings:       list[Any]      # legacy string warnings — kept for backward compat

    # Phase-2 enrichment (None on results produced before this schema version)
    process_metrics:    ProcessMetrics | None    = None
    stream_annotations: dict[str, StreamAnnotation] | None = None
    solver_diagnostics: SolverDiagnostics | None = None
    process_summary:    str | None               = None
    # Per-node solver summaries — used to seed the MPC Control Studio
    node_summaries:     dict[str, Any] | None    = None

    created_at: datetime

    model_config = {"from_attributes": True}


# ══════════════════════════════════════════════════════════════════════════════
# Pinch Analysis
# ══════════════════════════════════════════════════════════════════════════════

class StreamInput(BaseModel):
    """One process stream supplied explicitly for pinch analysis."""
    name: str = ""
    supply_temp: float = Field(description="Supply temperature (°C)")
    target_temp: float = Field(description="Target temperature (°C)")
    cp: float = Field(gt=0, description="Heat-capacity flowrate ṁ·Cₚ (kW/K)")
    stream_type: str = Field(
        description="'hot' (being cooled) or 'cold' (being heated)",
        pattern="^(hot|cold)$",
    )


class PinchRequest(BaseModel):
    delta_T_min: float = Field(
        default=10.0, gt=0,
        description="Minimum approach temperature (K or °C)",
    )
    streams: list[StreamInput] | None = Field(
        default=None,
        description="Explicit stream list; if omitted, auto-extracted from the flowsheet",
    )


class TemperatureIntervalOut(BaseModel):
    t_high: float
    t_low: float
    hcp_sum: float
    ccp_sum: float
    delta_h: float
    cascade_in: float
    cascade_out: float


class PinchResultResponse(BaseModel):
    pinch_temperature: float
    q_h_min: float
    q_c_min: float
    delta_T_min: float
    temperature_intervals: list[TemperatureIntervalOut]
    hot_composite: list[dict[str, float]]
    cold_composite: list[dict[str, float]]
    above_pinch_streams: dict[str, list[dict[str, Any]]]
    below_pinch_streams: dict[str, list[dict[str, Any]]]
    current_hot_utility_kw: float | None = None
    energy_saving_kw: float | None = None


# ══════════════════════════════════════════════════════════════════════════════
# Chemical Components
# ══════════════════════════════════════════════════════════════════════════════

class ComponentCreate(BaseModel):
    """Body for POST /api/components — create a project-scoped custom component."""
    name: str = Field(min_length=1, max_length=255)
    cas_number: str = Field(min_length=5, max_length=32,
                            description="CAS Registry Number, e.g. '64-17-5'")
    formula: str | None = Field(default=None, max_length=64)
    mw: float = Field(gt=0, description="Molecular weight (g/mol)")
    tc: float = Field(gt=0, description="Critical temperature (K)")
    pc: float = Field(gt=0, description="Critical pressure (Pa)")
    omega: float = Field(gt=0, lt=2, description="Acentric factor (dimensionless)")
    antoine_a: float | None = None
    antoine_b: float | None = None
    antoine_c: float | None = None
    antoine_tmin: float | None = Field(default=None, description="Antoine valid Tmin (K)")
    antoine_tmax: float | None = Field(default=None, description="Antoine valid Tmax (K)")
    antoine_units: str | None = Field(default=None, pattern="^(mmHg|Pa)$")
    mu_coeffs: list[float] | None = Field(default=None, max_length=4,
                                          description="Viscosity poly coeffs [a,b,c,d]")
    project_id: str = Field(description="Project this component is scoped to")

    @field_validator("antoine_tmax")
    @classmethod
    def tmax_gt_tmin(cls, v: float | None, info) -> float | None:
        tmin = info.data.get("antoine_tmin")
        if v is not None and tmin is not None and v <= tmin:
            raise ValueError("antoine_tmax must be greater than antoine_tmin")
        return v

    @field_validator("antoine_b", "antoine_c", "antoine_tmin", "antoine_tmax", "antoine_units",
                     mode="after")
    @classmethod
    def antoine_all_or_none(cls, v, info) -> Any:
        antoine_fields = ("antoine_a", "antoine_b", "antoine_c",
                          "antoine_tmin", "antoine_tmax", "antoine_units")
        provided = {f: info.data.get(f) for f in antoine_fields if f in info.data}
        any_set = any(val is not None for val in provided.values())
        all_set = all(val is not None for val in provided.values())
        if any_set and not all_set:
            raise ValueError(
                "All Antoine fields (A, B, C, tmin, tmax, units) must be provided together or not at all"
            )
        return v


class ComponentUpdate(BaseModel):
    """Body for PUT /api/components/{id} — only project-scoped components."""
    name: str | None = Field(default=None, min_length=1, max_length=255)
    formula: str | None = Field(default=None, max_length=64)
    mw: float | None = Field(default=None, gt=0)
    tc: float | None = Field(default=None, gt=0)
    pc: float | None = Field(default=None, gt=0)
    omega: float | None = Field(default=None, gt=0, lt=2)
    antoine_a: float | None = None
    antoine_b: float | None = None
    antoine_c: float | None = None
    antoine_tmin: float | None = None
    antoine_tmax: float | None = None
    antoine_units: str | None = Field(default=None, pattern="^(mmHg|Pa)$")
    mu_coeffs: list[float] | None = Field(default=None, max_length=4)


class ComponentResponse(BaseModel):
    id: str
    name: str
    cas_number: str
    formula: str | None
    mw: float | None
    tc: float | None
    pc: float | None
    omega: float | None
    antoine_a: float | None
    antoine_b: float | None
    antoine_c: float | None
    antoine_tmin: float | None
    antoine_tmax: float | None
    antoine_units: str | None
    mu_coeffs: list[float] | None
    is_global: bool
    project_id: str | None
    created_at: datetime

    model_config = {"from_attributes": True}


class AntoineValidateResponse(BaseModel):
    cas_number: str
    T_K: float
    valid: bool
    T_min_K: float | None
    T_max_K: float | None
    message: str


# ══════════════════════════════════════════════════════════════════════════════
# MPC Control Studio
# ══════════════════════════════════════════════════════════════════════════════

class MPCStartRequest(BaseModel):
    x0: list[float] | None = Field(None, min_length=2, max_length=2,
                                   description="Initial state [CA mol/L, T K]")
    u0: list[float] | None = Field(None, min_length=2, max_length=2,
                                   description="Initial control [F L/min, Tc K]")
    ca_sp:   float | None = Field(None, ge=0.02, le=0.98,
                                  description="Concentration setpoint (mol/L)")
    temp_sp: float | None = Field(None, ge=300.0, le=430.0,
                                  description="Temperature setpoint (K)")
    dt: float = Field(1.0, ge=0.1, le=10.0, description="Simulation timestep (s)")


class MPCConfigPatch(BaseModel):
    prediction_horizon: int   | None = Field(None, ge=5, le=80)
    control_horizon:    int   | None = Field(None, ge=1, le=30)
    Q00: float | None = Field(None, ge=0.1)
    Q11: float | None = Field(None, ge=0.001)
    R00: float | None = Field(None, ge=1e-5)
    R11: float | None = Field(None, ge=1e-5)
    controller_type: Literal["NONLINEAR", "LINEAR"] | None = None
    noise_sigma: float | None = Field(None, ge=0.0, le=5.0)


class MPCSessionInfo(BaseModel):
    sim_id:    str
    node_id:   str
    running:   bool
    time:      float
    states:    list[float]
    setpoints: list[float]
    controller_type: str
    estimator_type:  str
    noise_sigma: float


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


# ── Distillation shortcut preview ─────────────────────────────────────────────

class DistillationPreviewRequest(BaseModel):
    components: list[str] = Field(min_length=2, examples=[["benzene", "toluene", "xylene"]])
    feed_composition: list[float] = Field(description="Mole fractions (will be normalised)")
    feed_flow_mol_s: float = Field(default=1.0, gt=0, description="Total feed flow (mol/s)")
    feed_temperature_C: float = Field(default=25.0, description="Feed temperature (°C)")
    feed_pressure_bar: float = Field(default=1.013, gt=0, description="Column pressure (bar)")
    light_key: str = Field(description="Light-key component ID or CAS number")
    heavy_key: str = Field(description="Heavy-key component ID or CAS number")
    lk_recovery: float = Field(default=0.99, gt=0, lt=1, description="LK recovery in distillate")
    hk_recovery: float = Field(default=0.99, gt=0, lt=1, description="HK recovery in bottoms")
    reflux_ratio: float = Field(gt=0, description="Actual reflux ratio R (must be > R_min)")
    condenser_type: Literal["total", "partial"] = "total"
    property_package: Literal["ideal", "peng_robinson"] = "ideal"
    q: float = Field(default=1.0, description="Feed quality (1=sat. liquid, 0=sat. vapour)")


class DistillationPreviewResponse(BaseModel):
    N_min: float
    R_min: float
    N_actual: int
    N_feed_tray: int
    alpha_lk_hk: float
    distillate_flow_mol_s: float
    bottoms_flow_mol_s: float
    distillate_composition: dict[str, float]
    bottoms_composition: dict[str, float]
    distillate_temperature_C: float
    bottoms_temperature_C: float
    condenser_duty_kW: float
    reboiler_duty_kW: float
    reflux_ratio: float
    R_min_warning: bool = Field(description="True when R < 1.1 × R_min")


# Rebuild forward refs so nested schemas resolve correctly
ProjectDetail.model_rebuild()
SimulationDetail.model_rebuild()
