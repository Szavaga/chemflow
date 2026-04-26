// ── Auth ──────────────────────────────────────────────────────────────────────

export interface UserResponse {
  id: string
  email: string
  plan: string
  created_at: string
}

export interface TokenResponse {
  access_token: string
  token_type: string
  user: UserResponse
}

// ── Legacy quick-sim types (kept for /simulate page) ─────────────────────────

export type UnitType = 'flash_drum' | 'cstr' | 'heat_exchanger'

export interface ChemComponent {
  id: string
  name: string
  molecular_weight: number
  Tc: number
  Pc: number
  omega: number
}

export type RunStatus = 'pending' | 'running' | 'success' | 'failed'

export interface SimulationRun {
  id: string
  project_id: string
  unit_type: UnitType
  inputs: Record<string, unknown>
  outputs?: Record<string, unknown>
  status: RunStatus
  error_message?: string
  created_at: string
  completed_at?: string
}

export interface FlashResult {
  vapor_fraction: number
  liquid_flow: number
  vapor_flow: number
  liquid_composition: number[]
  vapor_composition: number[]
  K_values: number[]
  converged: boolean
  message: string
}

export interface CSTRResult {
  conversion: number
  outlet_concentration: number
  outlet_flow: number
  reaction_rate: number
  residence_time: number
  space_time_yield: number
  converged: boolean
  message: string
}

export interface HEXResult {
  cold_outlet_temp: number
  heat_duty: number
  lmtd: number
  UA: number
  effectiveness: number
  converged: boolean
  message: string
}

export type SimResult = FlashResult | CSTRResult | HEXResult

// ── New project / simulation types ────────────────────────────────────────────

export interface Project {
  id: string
  user_id: string
  name: string
  description?: string
  color?: string
  created_at: string
  updated_at: string
  simulations: Simulation[]
}

export type SimulationStatus = 'idle' | 'running' | 'complete' | 'error'

export interface Simulation {
  id: string
  project_id: string
  name: string
  status: SimulationStatus
  created_at: string
  updated_at: string
}

export interface FlowsheetNode {
  id: string
  type: string
  label: string
  data: Record<string, unknown>
  position: { x: number; y: number }
}

export interface FlowsheetEdge {
  id: string
  source: string
  target: string
  label?: string
  source_handle?: string
  target_handle?: string
}

export interface Flowsheet {
  id: string
  simulation_id: string
  nodes: FlowsheetNode[]
  edges: FlowsheetEdge[]
  created_at: string
}

export interface SimulationDetail extends Simulation {
  flowsheet?: Flowsheet
  result?: SimulationResult
}

export interface StreamState {
  name?: string
  flow: number
  temperature: number
  pressure: number
  vapor_fraction?: number
  composition: Record<string, number>
}

export interface EnergyBalance {
  total_duty_kW: number
  heating_kW:    number
  cooling_kW:    number
  [key: string]: number   // forward-compat index signature
}

export interface ConvergenceInfo {
  converged: boolean
  iterations: number
  tear_streams: string[]
  residuals: number[]
}

export interface SimulationResult {
  id: string
  simulation_id: string
  streams: Record<string, StreamState>
  energy_balance: EnergyBalance
  warnings: string[]
  convergence_info?: ConvergenceInfo
  process_metrics?: ProcessMetrics
  node_summaries?: Record<string, unknown>
  created_at: string
}

export interface ProcessMetrics {
  total_heat_duty_kW: number
  total_cooling_duty_kW: number
  total_shaft_work_kW: number
  overall_conversion: Record<string, number>
  recycle_ratio: Record<string, number>
  pinch_temperature: number | null
  Q_H_min: number | null
  energy_efficiency_pct: number | null
}

// ── Pinch Analysis ────────────────────────────────────────────────────────────

export interface StreamInput {
  name?: string
  supply_temp: number
  target_temp: number
  cp: number
  stream_type: 'hot' | 'cold'
}

export interface PinchRequest {
  delta_T_min?: number
  streams?: StreamInput[]
}

export interface TemperatureInterval {
  t_high: number
  t_low: number
  hcp_sum: number
  ccp_sum: number
  delta_h: number
  cascade_in: number
  cascade_out: number
}

export interface CompositeCurvePoint {
  T: number
  H: number
}

export interface PinchStreamEntry {
  name: string
  supply_temp: number
  target_temp: number
  cp: number
}

export interface PinchResult {
  pinch_temperature: number
  q_h_min: number
  q_c_min: number
  delta_T_min: number
  temperature_intervals: TemperatureInterval[]
  hot_composite: CompositeCurvePoint[]
  cold_composite: CompositeCurvePoint[]
  above_pinch_streams: { hot: PinchStreamEntry[]; cold: PinchStreamEntry[] }
  below_pinch_streams: { hot: PinchStreamEntry[]; cold: PinchStreamEntry[] }
  current_hot_utility_kw: number | null
  energy_saving_kw: number | null
}

// ── Chemical Components ───────────────────────────────────────────────────────

export interface ChemicalComponent {
  id: string
  name: string
  cas_number: string
  formula: string | null
  mw: number | null
  tc: number | null
  pc: number | null           // Pa
  omega: number | null
  antoine_a: number | null
  antoine_b: number | null
  antoine_c: number | null
  antoine_tmin: number | null // K
  antoine_tmax: number | null // K
  antoine_units: 'mmHg' | 'Pa' | null
  mu_coeffs: number[] | null
  is_global: boolean
  project_id: string | null
  created_at: string
}

export interface ComponentCreate {
  name: string
  cas_number: string
  formula?: string
  mw: number
  tc: number
  pc: number
  omega: number
  antoine_a?: number
  antoine_b?: number
  antoine_c?: number
  antoine_tmin?: number
  antoine_tmax?: number
  antoine_units?: 'mmHg' | 'Pa'
  mu_coeffs?: number[]
  project_id: string
}

export interface AntoineValidateResponse {
  cas_number: string
  T_K: number
  valid: boolean
  T_min_K: number | null
  T_max_K: number | null
  message: string
}

// ── MPC Control Studio ────────────────────────────────────────────────────────

export interface MPCNodeSummary {
  CA_ss:       number   // mol/L
  T_ss_K:      number   // K
  F_ss_L_min:  number   // L/min
  Tc_ss_K:     number   // K
  conversion:  number
}

export interface MPCStateSnapshot {
  time:                 number
  states:               [number, number]
  states_true:          [number, number]
  states_raw:           [number, number]
  control:              [number, number]
  setpoints:            [number, number]
  approaching_runaway:  boolean
  is_runaway:           boolean
  mpc_success:          boolean
  estimator_type:       'KF' | 'MHE'
  mhe_success:          boolean
  kalman_gain:          [number, number]
  iae_ca:               number
  iae_temp:             number
}

export interface PredTrajectory {
  time: number[]
  CA:   number[]
  T:    number[]
  u1:   number[]
  u2:   number[]
}

export interface HistoryPoint {
  time: number
  CA:   number
  T:    number
  F:    number
  Tc:   number
  CA_sp: number
  T_sp:  number
}

export interface MPCConfig {
  prediction_horizon: number
  control_horizon:    number
  Q00: number
  Q11: number
  R00: number
  R11: number
  controller_type: 'NONLINEAR' | 'LINEAR'
}
