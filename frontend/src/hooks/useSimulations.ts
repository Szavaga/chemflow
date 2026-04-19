import { useEffect, useState } from 'react'
import {
  createProject,
  createSimulation,
  deleteSimulation,
  fetchProjects,
} from '../api/client'
import type { Project, Simulation } from '../types'

export function useProjects() {
  const [projects, setProjects] = useState<Project[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  const load = async () => {
    setLoading(true)
    try {
      setProjects(await fetchProjects())
      setError(null)
    } catch {
      setError('Failed to load projects. Is the backend running?')
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => { load() }, [])

  const add = async (name: string, description?: string): Promise<Project> => {
    const project = await createProject({ name, description })
    setProjects(prev => [project, ...prev])
    return project
  }

  return { projects, loading, error, refresh: load, add }
}

export function useSimulations(projectId: string) {
  const [simulations, setSimulations] = useState<Simulation[]>([])

  const addSim = async (name: string): Promise<Simulation> => {
    const sim = await createSimulation(projectId, name)
    setSimulations(prev => [sim, ...prev])
    return sim
  }

  const removeSim = async (id: string): Promise<void> => {
    await deleteSimulation(id)
    setSimulations(prev => prev.filter(s => s.id !== id))
  }

  return { simulations, addSim, removeSim }
}
