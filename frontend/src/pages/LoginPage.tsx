import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { useAuth } from '../context/AuthContext'

type Tab = 'login' | 'register'

export default function LoginPage() {
  const { login, register } = useAuth()
  const navigate = useNavigate()

  const [tab, setTab] = useState<Tab>('login')
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault()
    setLoading(true)
    setError(null)
    try {
      if (tab === 'login') {
        await login(email, password)
      } else {
        await register(email, password)
      }
      navigate('/')
    } catch (err: unknown) {
      const e = err as { response?: { data?: { detail?: string | { msg: string }[] } }; message?: string }
      const detail = e.response?.data?.detail
      if (Array.isArray(detail)) {
        setError(detail.map(d => d.msg).join('; '))
      } else {
        setError((detail as string) ?? e.message ?? 'Authentication failed')
      }
    } finally {
      setLoading(false)
    }
  }

  return (
    <div className="auth-page">
      <div className="auth-card card">
        <div className="auth-brand">
          <span className="nav-logo">⚗</span>
          <h1>ChemFlow</h1>
          <p>Chemical process simulation platform</p>
        </div>

        <div className="unit-tabs">
          <button
            className={`tab${tab === 'login' ? ' active' : ''}`}
            onClick={() => { setTab('login'); setError(null) }}
          >
            Sign In
          </button>
          <button
            className={`tab${tab === 'register' ? ' active' : ''}`}
            onClick={() => { setTab('register'); setError(null) }}
          >
            Create Account
          </button>
        </div>

        <form onSubmit={handleSubmit} className="auth-form">
          <label>
            Email
            <input
              type="email"
              value={email}
              onChange={e => setEmail(e.target.value)}
              placeholder="you@example.com"
              required
              autoFocus
            />
          </label>
          <label>
            Password
            <input
              type="password"
              value={password}
              onChange={e => setPassword(e.target.value)}
              placeholder={tab === 'register' ? 'At least 8 characters' : ''}
              minLength={tab === 'register' ? 8 : undefined}
              required
            />
          </label>

          {error && <div className="error-banner">{error}</div>}

          <button type="submit" className="btn btn-primary btn-run" disabled={loading}>
            {loading ? (tab === 'login' ? 'Signing in…' : 'Creating account…') : (tab === 'login' ? 'Sign In' : 'Create Account')}
          </button>
        </form>
      </div>
    </div>
  )
}
