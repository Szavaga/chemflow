import { Link, useLocation } from 'react-router-dom'

export default function Navbar() {
  const { pathname } = useLocation()

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
    </nav>
  )
}
