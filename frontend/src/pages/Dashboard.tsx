import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { createSimulation } from '../api/client'
import { useProjects } from '../hooks/useSimulations'
import type { Project } from '../types'

// ── Per-project simulation list ───────────────────────────────────────────────

function ProjectSection({ project }: { project: Project }) {
  const navigate = useNavigate()
  const [simName, setSimName] = useState('')
  const [creating, setCreating] = useState(false)
  const [err, setErr] = useState<string | null>(null)

  const handleNewSim = async (e: React.FormEvent) => {
    e.preventDefault()
    if (!simName.trim()) return
    setCreating(true)
    setErr(null)
    try {
      const sim = await createSimulation(project.id, simName.trim())
      navigate(`/flowsheet/${sim.id}`)
    } catch {
      setErr('Failed to create simulation')
      setCreating(false)
    }
  }

  return (
    <div className="project-section card">
      <div className="card-header">
        <h2>{project.name}</h2>
        {project.description && <p className="card-desc">{project.description}</p>}
      </div>
      <form className="new-sim-row" onSubmit={handleNewSim}>
        <input
          value={simName}
          onChange={e => setSimName(e.target.value)}
          placeholder="New simulation name…"
        />
        <button type="submit" className="btn btn-primary btn-sm" disabled={creating || !simName.trim()}>
          {creating ? 'Creating…' : '+ Simulation'}
        </button>
      </form>
      {err && <div className="error-banner">{err}</div>}
    </div>
  )
}

// ── Dashboard ─────────────────────────────────────────────────────────────────

export default function Dashboard() {
  const { projects, loading, error, add } = useProjects()
  const [showForm, setShowForm] = useState(false)
  const [name, setName] = useState('')
  const [description, setDescription] = useState('')
  const [creating, setCreating] = useState(false)

  const handleCreate = async (e: React.FormEvent) => {
    e.preventDefault()
    setCreating(true)
    try {
      await add(name.trim(), description.trim() || undefined)
      setName('')
      setDescription('')
      setShowForm(false)
    } finally {
      setCreating(false)
    }
  }

  return (
    <div className="page">
      <div className="page-header">
        <h1>Projects</h1>
        <button className="btn btn-primary" onClick={() => setShowForm(s => !s)}>
          {showForm ? 'Cancel' : '+ New Project'}
        </button>
      </div>

      {error && <div className="error-banner">{error}</div>}

      {showForm && (
        <form className="card form-card" onSubmit={handleCreate}>
          <h2>Create Project</h2>
          <label>
            Name
            <input
              value={name}
              onChange={e => setName(e.target.value)}
              placeholder="e.g. Benzene–Toluene separation"
              required
              autoFocus
            />
          </label>
          <label>
            Description (optional)
            <input
              value={description}
              onChange={e => setDescription(e.target.value)}
              placeholder="Brief description of process objective"
            />
          </label>
          <div className="form-actions">
            <button type="submit" className="btn btn-primary" disabled={creating}>
              {creating ? 'Creating…' : 'Create'}
            </button>
            <button type="button" className="btn" onClick={() => setShowForm(false)}>
              Cancel
            </button>
          </div>
        </form>
      )}

      {loading ? (
        <div className="loading">Loading projects…</div>
      ) : projects.length === 0 ? (
        <div className="card empty-state">
          <p>No projects yet. Create one above to get started.</p>
        </div>
      ) : (
        <div className="project-list">
          {projects.map(p => (
            <ProjectSection key={p.id} project={p} />
          ))}
        </div>
      )}
    </div>
  )
}
