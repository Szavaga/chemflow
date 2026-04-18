import { Link } from 'react-router-dom'
import type { Project } from '../types'

interface Props {
  project: Project
  onDelete: (id: string) => void
}

export default function SimulationCard({ project, onDelete }: Props) {
  const date = new Date(project.created_at).toLocaleDateString(undefined, {
    year: 'numeric', month: 'short', day: 'numeric',
  })

  const handleDelete = () => {
    if (window.confirm(`Delete project "${project.name}"?`)) {
      onDelete(project.id)
    }
  }

  return (
    <div className="card sim-card">
      <div className="card-header">
        <h2>{project.name}</h2>
        <button className="btn btn-danger btn-sm" onClick={handleDelete}>Delete</button>
      </div>
      {project.description && (
        <p className="card-desc">{project.description}</p>
      )}
      <div className="card-footer">
        <span className="card-date">{date}</span>
        <Link to={`/simulate?project=${project.id}`} className="btn btn-primary btn-sm">
          Run Simulation
        </Link>
      </div>
    </div>
  )
}
