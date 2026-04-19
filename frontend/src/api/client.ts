import axios from 'axios'
import type {
  CSTRResult,
  FlashResult,
  Flowsheet,
  FlowsheetEdge,
  FlowsheetNode,
  HEXResult,
  Project,
  Simulation,
  SimulationDetail,
  SimulationResult,
  TokenResponse,
} from '../types'

const TOKEN_KEY = 'chemflow_token'

export const api = axios.create({
  baseURL: '/api',
  headers: { 'Content-Type': 'application/json' },
})

// Attach JWT on every request if present
api.interceptors.request.use(config => {
  const token = localStorage.getItem(TOKEN_KEY)
  if (token) config.headers.Authorization = `Bearer ${token}`
  return config
})

// ── Auth ──────────────────────────────────────────────────────────────────────

export const apiRegister = (email: string, password: string): Promise<TokenResponse> =>
  api.post<TokenResponse>('/auth/register', { email, password }).then(r => r.data)

export const apiLogin = (email: string, password: string): Promise<TokenResponse> =>
  api.post<TokenResponse>('/auth/login', { email, password }).then(r => r.data)

// ── Projects ──────────────────────────────────────────────────────────────────

export const fetchProjects = (): Promise<Project[]> =>
  api.get<Project[]>('/my/projects').then(r => r.data)

export const createProject = (data: { name: string; description?: string }): Promise<Project> =>
  api.post<Project>('/my/projects', data).then(r => r.data)

// ── Simulations ───────────────────────────────────────────────────────────────

export const fetchSimulation = (id: string): Promise<SimulationDetail> =>
  api.get<SimulationDetail>(`/simulations/${id}`).then(r => r.data)

export const createSimulation = (projectId: string, name: string): Promise<Simulation> =>
  api.post<Simulation>('/simulations/', { project_id: projectId, name }).then(r => r.data)

export const deleteSimulation = (id: string): Promise<void> =>
  api.delete(`/simulations/${id}`).then(() => undefined)

export const saveFlowsheet = (
  simId: string,
  nodes: FlowsheetNode[],
  edges: FlowsheetEdge[],
): Promise<Flowsheet> =>
  api
    .put<Flowsheet>(`/simulations/${simId}/flowsheet`, { nodes, edges })
    .then(r => r.data)

export const runSimulation = (simId: string): Promise<SimulationResult> =>
  api.post<SimulationResult>(`/simulations/${simId}/run`).then(r => r.data)

export const fetchResults = (simId: string): Promise<SimulationResult[]> =>
  api.get<SimulationResult[]>(`/simulations/${simId}/results`).then(r => r.data)

// ── Legacy quick simulations (stateless) ─────────────────────────────────────

export const runFlash = (payload: unknown): Promise<FlashResult> =>
  api.post<FlashResult>('/simulate/flash', payload).then(r => r.data)

export const runCSTR = (payload: unknown): Promise<CSTRResult> =>
  api.post<CSTRResult>('/simulate/cstr', payload).then(r => r.data)

export const runHEX = (payload: unknown): Promise<HEXResult> =>
  api.post<HEXResult>('/simulate/hex', payload).then(r => r.data)
