import { BrowserRouter, Navigate, Route, Routes } from 'react-router-dom'
import { AuthProvider, useAuth } from './context/AuthContext'
import Navbar from './components/Navbar'
import Dashboard from './pages/Dashboard'
import FlowsheetPage from './pages/FlowsheetPage'
import LoginPage from './pages/LoginPage'
import SimulationPage from './pages/SimulationPage'

function ProtectedRoute({ children }: { children: React.ReactNode }) {
  const { isAuthenticated } = useAuth()
  return isAuthenticated ? <>{children}</> : <Navigate to="/login" replace />
}

function AppRoutes() {
  const { isAuthenticated } = useAuth()
  return (
    <>
      {isAuthenticated && <Navbar />}
      <main className={isAuthenticated ? 'main-content' : ''}>
        <Routes>
          <Route path="/login" element={
            isAuthenticated ? <Navigate to="/" replace /> : <LoginPage />
          } />
          <Route path="/" element={
            <ProtectedRoute><Dashboard /></ProtectedRoute>
          } />
          <Route path="/simulate" element={
            <ProtectedRoute><SimulationPage /></ProtectedRoute>
          } />
          <Route path="/flowsheet/:simId" element={
            <ProtectedRoute><FlowsheetPage /></ProtectedRoute>
          } />
        </Routes>
      </main>
    </>
  )
}

export default function App() {
  return (
    <AuthProvider>
      <BrowserRouter>
        <AppRoutes />
      </BrowserRouter>
    </AuthProvider>
  )
}
