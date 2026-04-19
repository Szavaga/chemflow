import { createContext, useCallback, useContext, useEffect, useState } from 'react'
import { apiRegister, apiLogin } from '../api/client'
import type { UserResponse } from '../types'

interface AuthState {
  token: string | null
  user: UserResponse | null
}

interface AuthContextValue extends AuthState {
  login: (email: string, password: string) => Promise<void>
  register: (email: string, password: string) => Promise<void>
  logout: () => void
  isAuthenticated: boolean
}

const AuthContext = createContext<AuthContextValue | null>(null)

const TOKEN_KEY = 'chemflow_token'
const USER_KEY  = 'chemflow_user'

export function AuthProvider({ children }: { children: React.ReactNode }) {
  const [state, setState] = useState<AuthState>(() => {
    try {
      const token = localStorage.getItem(TOKEN_KEY)
      const raw   = localStorage.getItem(USER_KEY)
      return { token, user: raw ? JSON.parse(raw) : null }
    } catch {
      return { token: null, user: null }
    }
  })

  // Keep localStorage in sync
  useEffect(() => {
    if (state.token) {
      localStorage.setItem(TOKEN_KEY, state.token)
      localStorage.setItem(USER_KEY, JSON.stringify(state.user))
    } else {
      localStorage.removeItem(TOKEN_KEY)
      localStorage.removeItem(USER_KEY)
    }
  }, [state])

  const login = useCallback(async (email: string, password: string) => {
    const { access_token, user } = await apiLogin(email, password)
    setState({ token: access_token, user })
  }, [])

  const register = useCallback(async (email: string, password: string) => {
    const { access_token, user } = await apiRegister(email, password)
    setState({ token: access_token, user })
  }, [])

  const logout = useCallback(() => {
    setState({ token: null, user: null })
  }, [])

  return (
    <AuthContext.Provider value={{ ...state, login, register, logout, isAuthenticated: !!state.token }}>
      {children}
    </AuthContext.Provider>
  )
}

export function useAuth(): AuthContextValue {
  const ctx = useContext(AuthContext)
  if (!ctx) throw new Error('useAuth must be used inside <AuthProvider>')
  return ctx
}
