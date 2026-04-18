export type UnitType = 'flash_drum' | 'cstr' | 'heat_exchanger'

export interface ChemComponent {
  id: string
  name: string
  molecular_weight: number
  Tc: number
  Pc: number
  omega: number
}

export interface Project {
  id: string
  name: string
  description?: string
  created_at: string
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
