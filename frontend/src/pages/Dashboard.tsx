import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { createSimulation, deleteSimulation, updateProject } from '../api/client'
import { useProjects } from '../hooks/useSimulations'
import type { Project } from '../types'

const PALETTE = [
  '#6366f1', '#8b5cf6', '#ec4899', '#ef4444',
  '#f97316', '#eab308', '#22c55e', '#14b8a6',
  '#3b82f6', '#64748b',
]

// ── Per-project simulation list ───────────────────────────────────────────────

function ProjectSection({
  project,
  onDelete,
}: {
  project: Project
  onDelete: (id: string) => Promise<void>
}) {
  const navigate = useNavigate()
  const [simName, setSimName] = useState('')
  const [creating, setCreating] = useState(false)
  const [deletingId, setDeletingId] = useState<string | null>(null)
  const [deletingProject, setDeletingProject] = useState(false)
  const [err, setErr] = useState<string | null>(null)
  const [sims, setSims] = useState(project.simulations ?? [])
  const [color, setColor] = useState(project.color ?? '#6366f1')
  const [showPalette, setShowPalette] = useState(false)

  const handleNewSim = async (e: React.FormEvent) => {
    e.preventDefault()
    if (!simName.trim()) return
    setCreating(true)
    setErr(null)
    try {
      const sim = await createSimulation(project.id, simName.trim())
      setSims(prev => [sim, ...prev])
      setSimName('')
      navigate(`/flowsheet/${sim.id}`)
    } catch {
      setErr('Failed to create simulation')
      setCreating(false)
    }
  }

  const handleDelete = async (id: string) => {
    setDeletingId(id)
    try {
      await deleteSimulation(id)
      setSims(prev => prev.filter(s => s.id !== id))
    } catch {
      setErr('Failed to delete simulation')
    } finally {
      setDeletingId(null)
    }
  }

  const handleDeleteProject = async () => {
    if (!window.confirm(`Delete project "${project.name}" and all its simulations? This cannot be undone.`))
      return
    setDeletingProject(true)
    try {
      await onDelete(project.id)
    } catch {
      setErr('Failed to delete project')
      setDeletingProject(false)
    }
  }

  const handleColorPick = async (c: string) => {
    setColor(c)
    setShowPalette(false)
    try {
      await updateProject(project.id, { color: c })
    } catch {
      setErr('Failed to save colour')
    }
  }

  return (
    <div className="project-section card" style={{ borderTopColor: color }}>
      <div className="project-header">
        <div className="project-color-wrap">
          <button
            className="project-color-swatch"
            style={{ background: color }}
            onClick={() => setShowPalette(p => !p)}
            title="Change colour"
          />
          {showPalette && (
            <div className="color-palette">
              {PALETTE.map(c => (
                <button
                  key={c}
                  className={`color-swatch${c === color ? ' color-swatch--active' : ''}`}
                  style={{ background: c }}
                  onClick={() => handleColorPick(c)}
                />
              ))}
            </div>
          )}
        </div>
        <div className="project-title-block">
          <h2>{project.name}</h2>
          {project.description && <p className="card-desc">{project.description}</p>}
        </div>
        <button
          className="project-delete-btn"
          onClick={handleDeleteProject}
          disabled={deletingProject}
          title="Delete project"
        >
          {deletingProject ? '…' : '✕'}
        </button>
      </div>

      {sims.length > 0 && (
        <div className="sim-grid">
          {sims.map(s => (
            <div key={s.id} className="sim-box">
              <button className="sim-box-btn" onClick={() => navigate(`/flowsheet/${s.id}`)}>
                <span className="sim-box-name">{s.name}</span>
                <span className={`badge badge-sim-${s.status}`}>{s.status}</span>
              </button>
              <button
                className="sim-box-delete"
                onClick={() => handleDelete(s.id)}
                disabled={deletingId === s.id}
                title="Delete"
              >
                {deletingId === s.id ? '…' : '✕'}
              </button>
            </div>
          ))}
        </div>
      )}

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
  const { projects, loading, error, add, remove } = useProjects()
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
            <ProjectSection key={p.id} project={p} onDelete={remove} />
          ))}
        </div>
      )}
    </div>
  )
}
