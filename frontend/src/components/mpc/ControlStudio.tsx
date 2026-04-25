import {
  CartesianGrid,
  Legend,
  Line,
  LineChart,
  ReferenceLine,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts'
import { useControlStudio } from '../../hooks/useControlStudio'
import type { MPCNodeSummary } from '../../types'

interface Props {
  simId:    string
  nodeId:   string
  nodeLabel: string
  ssOperatingPoint: MPCNodeSummary | null
  onClose:  () => void
}

// ── Small chart card ──────────────────────────────────────────────────────────

function ChartCard({
  title,
  data,
  dataKey,
  spKey,
  color,
  unit,
  domain,
}: {
  title:   string
  data:    Record<string, number>[]
  dataKey: string
  spKey?:  string
  color:   string
  unit:    string
  domain?: [number | 'auto', number | 'auto']
}) {
  const spValue = data.length > 0 && spKey ? (data[data.length - 1][spKey] ?? undefined) : undefined

  return (
    <div className="bg-slate-800 rounded-lg p-3">
      <p className="text-[11px] font-semibold text-slate-300 mb-2">{title}</p>
      <ResponsiveContainer width="100%" height={140}>
        <LineChart data={data} margin={{ top: 4, right: 8, bottom: 0, left: 0 }}>
          <CartesianGrid strokeDasharray="3 3" stroke="#334155" />
          <XAxis dataKey="time" tick={{ fontSize: 10, fill: '#94a3b8' }} tickCount={5}
            label={{ value: 't (s)', position: 'insideBottomRight', offset: -4, fontSize: 10, fill: '#64748b' }} />
          <YAxis domain={domain ?? ['auto', 'auto']}
            tick={{ fontSize: 10, fill: '#94a3b8' }}
            tickFormatter={v => typeof v === 'number' ? v.toFixed(2) : v}
            width={46} />
          <Tooltip
            contentStyle={{ background: '#1e293b', border: '1px solid #334155', fontSize: 11 }}
            labelStyle={{ color: '#94a3b8' }}
            formatter={(v: number) => [`${v.toFixed(4)} ${unit}`, dataKey]}
          />
          {spValue !== undefined && (
            <ReferenceLine y={spValue} stroke="#fbbf24" strokeDasharray="5 3"
              label={{ value: 'SP', position: 'right', fontSize: 10, fill: '#fbbf24' }} />
          )}
          <Line type="monotone" dataKey={dataKey} stroke={color}
            dot={false} strokeWidth={1.5} isAnimationActive={false} />
        </LineChart>
      </ResponsiveContainer>
    </div>
  )
}

// ── Slider row ────────────────────────────────────────────────────────────────

function SliderRow({
  label, value, min, max, step, unit, onChange,
}: {
  label: string; value: number; min: number; max: number
  step: number; unit: string; onChange: (v: number) => void
}) {
  return (
    <div className="mb-3">
      <div className="flex justify-between mb-1">
        <span className="text-[11px] text-slate-400">{label}</span>
        <span className="text-[11px] font-mono text-slate-200">{value.toFixed(step < 1 ? 3 : 1)} {unit}</span>
      </div>
      <input type="range" min={min} max={max} step={step} value={value}
        onChange={e => onChange(parseFloat(e.target.value))}
        className="w-full h-1.5 rounded-full bg-slate-600 appearance-none cursor-pointer
                   [&::-webkit-slider-thumb]:appearance-none [&::-webkit-slider-thumb]:w-3
                   [&::-webkit-slider-thumb]:h-3 [&::-webkit-slider-thumb]:rounded-full
                   [&::-webkit-slider-thumb]:bg-cyan-400" />
    </div>
  )
}

// ── Number input row ──────────────────────────────────────────────────────────

function NumRow({
  label, value, step, onChange,
}: {
  label: string; value: number; step: number; onChange: (v: number) => void
}) {
  return (
    <div className="flex items-center justify-between mb-2 gap-2">
      <span className="text-[11px] text-slate-400 flex-1 min-w-0 truncate">{label}</span>
      <input type="number" step={step} value={value}
        onChange={e => onChange(parseFloat(e.target.value))}
        className="w-20 bg-slate-700 border border-slate-600 rounded px-2 py-0.5
                   text-[11px] text-slate-100 text-right focus:outline-none focus:border-cyan-500" />
    </div>
  )
}

// ── Main component ────────────────────────────────────────────────────────────

export function ControlStudio({ simId, nodeId, nodeLabel, ssOperatingPoint, onClose }: Props) {
  const {
    wsStatus, running, history, currentState,
    setpoints, mpcConfig, estimatorType,
    approachingRunaway, isRunaway,
    setRunning, updateSetpoints, updateMpcConfig, updateEstimator, reset,
  } = useControlStudio(simId, nodeId, ssOperatingPoint)

  const wsColor =
    wsStatus === 'connected'    ? 'text-emerald-400' :
    wsStatus === 'connecting'   ? 'text-amber-400'   : 'text-slate-500'

  const wsLabel =
    wsStatus === 'connected'  ? 'Connected' :
    wsStatus === 'connecting' ? 'Connecting…' : 'Disconnected'

  const runawayBadge = isRunaway
    ? 'bg-red-600 text-white'
    : approachingRunaway
      ? 'bg-amber-500 text-white'
      : 'bg-slate-700 text-slate-400'

  const runawayText = isRunaway ? 'RUNAWAY' : approachingRunaway ? 'High T' : 'Normal'

  return (
    <div className="flex flex-col h-full bg-slate-900 border-l border-slate-700" style={{ width: 900 }}>
      {/* Header */}
      <div className="flex items-center justify-between px-4 py-2.5 border-b border-slate-700 flex-shrink-0">
        <div className="flex items-center gap-2">
          <span className="text-[13px] font-semibold text-slate-100">Control Studio</span>
          <span className="text-[11px] text-slate-400">—</span>
          <span className="text-[12px] text-cyan-300 font-medium">{nodeLabel}</span>
          <span className={`text-[10px] px-1.5 py-0.5 rounded font-medium ${runawayBadge}`}>
            {runawayText}
          </span>
        </div>
        <div className="flex items-center gap-3">
          <span className={`text-[11px] ${wsColor}`}>{wsLabel}</span>
          <button
            onClick={() => setRunning(!running)}
            disabled={wsStatus !== 'connected'}
            className={`px-3 py-1 rounded text-[11px] font-semibold transition-colors disabled:opacity-40
              ${running
                ? 'bg-red-700 hover:bg-red-600 text-white'
                : 'bg-cyan-700 hover:bg-cyan-600 text-white'}`}
          >
            {running ? 'Stop' : 'Run'}
          </button>
          <button
            onClick={reset}
            className="px-3 py-1 rounded text-[11px] font-semibold bg-slate-700
                       hover:bg-slate-600 text-slate-300 transition-colors"
          >
            Reset
          </button>
          <button onClick={onClose}
            className="w-6 h-6 flex items-center justify-center rounded text-slate-400
                       hover:text-slate-100 hover:bg-slate-700 transition-colors text-lg leading-none">
            ×
          </button>
        </div>
      </div>

      {/* Body: charts left + controls right */}
      <div className="flex flex-1 overflow-hidden">
        {/* Charts — 2/3 */}
        <div className="flex-1 overflow-y-auto p-3 grid grid-cols-2 gap-3 content-start">
          <ChartCard title="Concentration CA (mol/L)" data={history} dataKey="CA"
            spKey="CA_sp" color="#22d3ee" unit="mol/L" domain={[0, 1]} />
          <ChartCard title="Temperature T (K)" data={history} dataKey="T"
            spKey="T_sp" color="#f87171" unit="K" domain={[300, 430]} />
          <ChartCard title="Feed Flow F (L/min)" data={history} dataKey="F"
            color="#a78bfa" unit="L/min" domain={[50, 200]} />
          <ChartCard title="Coolant Temp Tc (K)" data={history} dataKey="Tc"
            color="#34d399" unit="K" domain={[250, 350]} />
        </div>

        {/* Controls — 1/3 */}
        <div className="w-72 flex-shrink-0 border-l border-slate-700 overflow-y-auto p-4">
          {/* Live metrics */}
          {currentState && (
            <div className="grid grid-cols-2 gap-2 mb-4">
              <div className="bg-slate-800 rounded p-2 text-center">
                <p className="text-[10px] text-slate-500 uppercase tracking-wide">CA</p>
                <p className="text-[13px] font-mono text-cyan-300">
                  {currentState.states[0].toFixed(4)}
                </p>
              </div>
              <div className="bg-slate-800 rounded p-2 text-center">
                <p className="text-[10px] text-slate-500 uppercase tracking-wide">T (K)</p>
                <p className="text-[13px] font-mono text-red-300">
                  {currentState.states[1].toFixed(1)}
                </p>
              </div>
              <div className="bg-slate-800 rounded p-2 text-center">
                <p className="text-[10px] text-slate-500 uppercase tracking-wide">IAE CA</p>
                <p className="text-[12px] font-mono text-slate-300">
                  {currentState.iae_ca.toFixed(3)}
                </p>
              </div>
              <div className="bg-slate-800 rounded p-2 text-center">
                <p className="text-[10px] text-slate-500 uppercase tracking-wide">IAE T</p>
                <p className="text-[12px] font-mono text-slate-300">
                  {currentState.iae_temp.toFixed(1)}
                </p>
              </div>
            </div>
          )}

          {/* Setpoints */}
          <p className="text-[10px] font-bold uppercase tracking-widest text-cyan-500 mb-2">Setpoints</p>
          <SliderRow label="CA setpoint" value={setpoints.ca} min={0.1} max={0.9} step={0.01}
            unit="mol/L" onChange={v => updateSetpoints(v, setpoints.temp)} />
          <SliderRow label="T setpoint" value={setpoints.temp} min={320} max={410} step={1}
            unit="K" onChange={v => updateSetpoints(setpoints.ca, v)} />

          {/* MPC Tuning */}
          <p className="text-[10px] font-bold uppercase tracking-widest text-cyan-500 mt-4 mb-2">MPC Tuning</p>
          <NumRow label="Q₀₀ (CA weight)" value={mpcConfig.Q00} step={1}
            onChange={v => updateMpcConfig({ Q00: v })} />
          <NumRow label="Q₁₁ (T weight)" value={mpcConfig.Q11} step={0.01}
            onChange={v => updateMpcConfig({ Q11: v })} />
          <NumRow label="R₀₀ (F effort)" value={mpcConfig.R00} step={0.0001}
            onChange={v => updateMpcConfig({ R00: v })} />
          <NumRow label="R₁₁ (Tc effort)" value={mpcConfig.R11} step={0.001}
            onChange={v => updateMpcConfig({ R11: v })} />
          <NumRow label="Prediction horizon" value={mpcConfig.prediction_horizon} step={1}
            onChange={v => updateMpcConfig({ prediction_horizon: Math.round(v) })} />
          <NumRow label="Control horizon" value={mpcConfig.control_horizon} step={1}
            onChange={v => updateMpcConfig({ control_horizon: Math.round(v) })} />

          {/* Controller type */}
          <p className="text-[10px] font-bold uppercase tracking-widest text-cyan-500 mt-4 mb-2">Controller</p>
          <div className="flex gap-2 mb-4">
            {(['NONLINEAR', 'LINEAR'] as const).map(t => (
              <button key={t}
                onClick={() => updateMpcConfig({ controller_type: t })}
                className={`flex-1 py-1.5 rounded text-[11px] font-medium transition-colors
                  ${mpcConfig.controller_type === t
                    ? 'bg-cyan-700 text-white'
                    : 'bg-slate-700 text-slate-400 hover:bg-slate-600'}`}
              >
                {t === 'NONLINEAR' ? 'Nonlinear' : 'Linear'}
              </button>
            ))}
          </div>

          {/* Estimator */}
          <p className="text-[10px] font-bold uppercase tracking-widest text-cyan-500 mb-2">Estimator</p>
          <div className="flex gap-2 mb-4">
            {(['KF', 'MHE'] as const).map(t => (
              <button key={t}
                onClick={() => updateEstimator(t)}
                className={`flex-1 py-1.5 rounded text-[11px] font-medium transition-colors
                  ${estimatorType === t
                    ? 'bg-cyan-700 text-white'
                    : 'bg-slate-700 text-slate-400 hover:bg-slate-600'}`}
              >
                {t}
              </button>
            ))}
          </div>

          {/* Steady-state seed info */}
          {ssOperatingPoint && (
            <>
              <p className="text-[10px] font-bold uppercase tracking-widest text-cyan-500 mb-2">
                SS Operating Point
              </p>
              <div className="bg-slate-800 rounded p-2 text-[11px] text-slate-400 space-y-0.5">
                <div className="flex justify-between">
                  <span>CA_ss</span>
                  <span className="font-mono text-slate-300">{ssOperatingPoint.CA_ss.toFixed(4)} mol/L</span>
                </div>
                <div className="flex justify-between">
                  <span>T_ss</span>
                  <span className="font-mono text-slate-300">{ssOperatingPoint.T_ss_K.toFixed(1)} K</span>
                </div>
                <div className="flex justify-between">
                  <span>F_ss</span>
                  <span className="font-mono text-slate-300">{ssOperatingPoint.F_ss_L_min.toFixed(1)} L/min</span>
                </div>
                <div className="flex justify-between">
                  <span>Tc_ss</span>
                  <span className="font-mono text-slate-300">{ssOperatingPoint.Tc_ss_K.toFixed(1)} K</span>
                </div>
                <div className="flex justify-between">
                  <span>Conversion</span>
                  <span className="font-mono text-slate-300">{(ssOperatingPoint.conversion * 100).toFixed(1)}%</span>
                </div>
              </div>
            </>
          )}
        </div>
      </div>
    </div>
  )
}
