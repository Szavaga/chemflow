import { useState } from 'react'
import SimulationCard from '../components/SimulationCard'
import { useProjects } from '../hooks/useSimulations'

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
          <p>No projects yet. Create one above to start simulating unit operations.</p>
        </div>
      ) : (
        <div className="card-grid">
          {projects.map(p => (
            <SimulationCard key={p.id} project={p} onDelete={remove} />
          ))}
        </div>
      )}
    </div>
  )
}
