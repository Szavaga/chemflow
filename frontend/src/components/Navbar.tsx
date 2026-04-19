import { Link, useLocation } from 'react-router-dom'
import { useAuth } from '../context/AuthContext'

export default function Navbar() {
  const { pathname } = useLocation()
  const { user, logout } = useAuth()

  return (
    <nav className="navbar">
      <div className="nav-brand">
        <span className="nav-logo">⚗</span>
        <Link to="/" className="nav-title">ChemFlow</Link>
      </div>
      <div className="nav-links">
        <Link to="/" className={`nav-link${pathname === '/' ? ' active' : ''}`}>
          Projects
        </Link>
        <Link to="/simulate" className={`nav-link${pathname === '/simulate' ? ' active' : ''}`}>
          Simulator
        </Link>
      </div>
      <div className="nav-user">
        <span className="nav-email">{user?.email}</span>
        <button className="btn btn-sm" onClick={logout}>Sign out</button>
      </div>
    </nav>
  )
}
