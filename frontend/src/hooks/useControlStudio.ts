import { useCallback, useEffect, useRef, useState } from 'react'
import type { HistoryPoint, MPCConfig, MPCNodeSummary, MPCStateSnapshot, PredTrajectory } from '../types'

const TOKEN_KEY = 'chemflow_token'
const CHART_MAX_POINTS = 300
const RECONNECT_DELAY_MS = 2000

type WsStatus = 'connecting' | 'connected' | 'disconnected'

interface ControlStudioState {
  wsStatus:       WsStatus
  running:        boolean
  history:        HistoryPoint[]
  currentState:   MPCStateSnapshot | null
  predTrajectory: PredTrajectory | null
  setpoints:      { ca: number; temp: number }
  mpcConfig:      MPCConfig
  estimatorType:  'KF' | 'MHE'
  approachingRunaway: boolean
  isRunaway:          boolean
}

interface ControlStudioActions {
  setRunning:      (v: boolean) => void
  updateSetpoints: (ca: number, temp: number) => void
  updateMpcConfig: (patch: Partial<MPCConfig>) => void
  updateEstimator: (type: 'KF' | 'MHE') => void
  reset:           () => void
}

const DEFAULT_CONFIG: MPCConfig = {
  prediction_horizon: 40,
  control_horizon:    10,
  Q00: 50.0,
  Q11: 0.2,
  R00: 0.001,
  R11: 0.01,
  controller_type: 'NONLINEAR',
}

export function useControlStudio(
  simId:    string,
  nodeId:   string,
  seed:     MPCNodeSummary | null,
): ControlStudioState & ControlStudioActions {
  const wsRef        = useRef<WebSocket | null>(null)
  const reconnectRef = useRef<ReturnType<typeof setTimeout> | null>(null)
  const mountedRef   = useRef(true)

  const [wsStatus,       setWsStatus]       = useState<WsStatus>('disconnected')
  const [running,        setRunningState]   = useState(false)
  const [history,        setHistory]        = useState<HistoryPoint[]>([])
  const [currentState,   setCurrentState]   = useState<MPCStateSnapshot | null>(null)
  const [predTrajectory, setPredTrajectory] = useState<PredTrajectory | null>(null)
  const [setpoints,      setSetpoints]      = useState({ ca: 0.5, temp: 350.0 })
  const [mpcConfig,      setMpcConfig]      = useState<MPCConfig>(DEFAULT_CONFIG)
  const [estimatorType,  setEstimatorType]  = useState<'KF' | 'MHE'>('KF')
  const [approachingRunaway, setApproaching] = useState(false)
  const [isRunaway,          setIsRunaway]  = useState(false)

  // Keep setpoints ref for use inside WS callbacks without stale closure
  const setpointsRef = useRef(setpoints)
  useEffect(() => { setpointsRef.current = setpoints }, [setpoints])

  const sendCmd = useCallback((cmd: string, data?: Record<string, unknown>) => {
    if (wsRef.current?.readyState === WebSocket.OPEN) {
      wsRef.current.send(JSON.stringify({ cmd, data: data ?? {} }))
    }
  }, [])

  const connect = useCallback(() => {
    if (!mountedRef.current) return
    const token = localStorage.getItem(TOKEN_KEY) ?? ''
    const proto = location.protocol === 'https:' ? 'wss:' : 'ws:'
    const url   = `${proto}//${location.host}/api/simulations/${simId}/mpc/${nodeId}/ws?token=${token}`

    setWsStatus('connecting')
    const ws = new WebSocket(url)
    wsRef.current = ws

    ws.onopen = () => {
      if (!mountedRef.current) return
      setWsStatus('connected')
      // Seed from steady-state if available
      if (seed) {
        sendCmd('start', {
          x0: [seed.CA_ss, seed.T_ss_K],
          u0: [seed.F_ss_L_min, seed.Tc_ss_K],
          ca_sp:   setpointsRef.current.ca,
          temp_sp: setpointsRef.current.temp,
        })
      }
    }

    ws.onmessage = (ev) => {
      if (!mountedRef.current) return
      try {
        const msg = JSON.parse(ev.data as string)
        if (msg.type === 'state') {
          const snap = msg as MPCStateSnapshot & {
            predicted_trajectory?: PredTrajectory
            mpc_success?: boolean
          }
          setCurrentState(snap)
          setApproaching(snap.approaching_runaway)
          setIsRunaway(snap.is_runaway)
          setEstimatorType(snap.estimator_type)

          if (snap.predicted_trajectory && Object.keys(snap.predicted_trajectory).length > 0) {
            setPredTrajectory(snap.predicted_trajectory as PredTrajectory)
          }

          const pt: HistoryPoint = {
            time:  snap.time,
            CA:    snap.states[0],
            T:     snap.states[1],
            F:     snap.control[0],
            Tc:    snap.control[1],
            CA_sp: snap.setpoints[0],
            T_sp:  snap.setpoints[1],
          }
          setHistory(prev => {
            const next = [...prev, pt]
            return next.length > CHART_MAX_POINTS ? next.slice(next.length - CHART_MAX_POINTS) : next
          })
        } else if (msg.type === 'reset_done') {
          setHistory([])
          setCurrentState(null)
          setPredTrajectory(null)
          setRunningState(false)
        }
      } catch {
        // ignore malformed messages
      }
    }

    ws.onerror = () => {
      setWsStatus('disconnected')
    }

    ws.onclose = () => {
      if (!mountedRef.current) return
      setWsStatus('disconnected')
      setRunningState(false)
      // Reconnect after delay
      reconnectRef.current = setTimeout(() => {
        if (mountedRef.current) connect()
      }, RECONNECT_DELAY_MS)
    }
  }, [simId, nodeId, seed, sendCmd])

  // Connect on mount, disconnect on unmount
  useEffect(() => {
    mountedRef.current = true
    connect()
    return () => {
      mountedRef.current = false
      if (reconnectRef.current) clearTimeout(reconnectRef.current)
      wsRef.current?.close()
    }
  }, [connect])

  // ── Actions ────────────────────────────────────────────────────────────────

  const setRunning = useCallback((v: boolean) => {
    setRunningState(v)
    sendCmd(v ? 'start' : 'stop')
  }, [sendCmd])

  const updateSetpoints = useCallback((ca: number, temp: number) => {
    setSetpoints({ ca, temp })
    sendCmd('setpoints', { ca_sp: ca, temp_sp: temp })
  }, [sendCmd])

  const updateMpcConfig = useCallback((patch: Partial<MPCConfig>) => {
    setMpcConfig(prev => ({ ...prev, ...patch }))
    sendCmd('config', patch as Record<string, unknown>)
  }, [sendCmd])

  const updateEstimator = useCallback((type: 'KF' | 'MHE') => {
    setEstimatorType(type)
    sendCmd('estimator', { type })
  }, [sendCmd])

  const reset = useCallback(() => {
    setRunningState(false)
    sendCmd('reset')
  }, [sendCmd])

  return {
    wsStatus, running, history, currentState, predTrajectory,
    setpoints, mpcConfig, estimatorType, approachingRunaway, isRunaway,
    setRunning, updateSetpoints, updateMpcConfig, updateEstimator, reset,
  }
}
