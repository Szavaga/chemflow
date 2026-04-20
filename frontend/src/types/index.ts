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

export interface SimulationResult {
  id: string
  simulation_id: string
  streams: Record<string, StreamState>
  energy_balance: EnergyBalance
  warnings: string[]
  created_at: string
}
