import axios from 'axios'
import type {
  ChemComponent,
  CSTRResult,
  FlashResult,
  HEXResult,
  Project,
  SimulationRun,
} from '../types'

const api = axios.create({
  baseURL: '/api',
  headers: { 'Content-Type': 'application/json' },
})

// ── Components ────────────────────────────────────────────────────────────────
export const fetchComponents = (): Promise<ChemComponent[]> =>
  api.get<ChemComponent[]>('/components').then(r => r.data)

// ── Quick simulations (no persistence) ───────────────────────────────────────
export const runFlash = (payload: unknown): Promise<FlashResult> =>
  api.post<FlashResult>('/simulate/flash', payload).then(r => r.data)

export const runCSTR = (payload: unknown): Promise<CSTRResult> =>
  api.post<CSTRResult>('/simulate/cstr', payload).then(r => r.data)

export const runHEX = (payload: unknown): Promise<HEXResult> =>
  api.post<HEXResult>('/simulate/hex', payload).then(r => r.data)

// ── Projects ──────────────────────────────────────────────────────────────────
export const fetchProjects = (): Promise<Project[]> =>
  api.get<Project[]>('/projects').then(r => r.data)

export const createProject = (data: { name: string; description?: string }): Promise<Project> =>
  api.post<Project>('/projects', data).then(r => r.data)

export const deleteProject = (id: string): Promise<void> =>
  api.delete(`/projects/${id}`).then(() => undefined)

// ── Runs ──────────────────────────────────────────────────────────────────────
export const fetchRuns = (projectId: string): Promise<SimulationRun[]> =>
  api.get<SimulationRun[]>(`/projects/${projectId}/runs`).then(r => r.data)

export const createRun = (
  projectId: string,
  data: { unit_type: string; inputs: unknown },
): Promise<SimulationRun> =>
  api.post<SimulationRun>(`/projects/${projectId}/runs`, data).then(r => r.data)
