import { useEffect, useState } from 'react'
import { createProject, deleteProject, fetchProjects } from '../api/client'
import type { Project } from '../types'

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

  const remove = async (id: string): Promise<void> => {
    await deleteProject(id)
    setProjects(prev => prev.filter(p => p.id !== id))
  }

  return { projects, loading, error, refresh: load, add, remove }
}
