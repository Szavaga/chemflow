/**
 * PinchPanel — Energy Targets tab in the results panel.
 *
 * Shows:
 *  • Q_H_min, Q_C_min, pinch temperature as metric cards
 *  • Energy saving potential vs. current utility
 *  • Composite curves (T-H diagram) using Recharts
 *  • Temperature interval table
 */

import { useState } from 'react'
import {
  CartesianGrid,
  Legend,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts'
import type {
  CompositeCurvePoint,
  PinchRequest,
  PinchResult,
  StreamInput,
} from '../../types'

// ── Metric card ───────────────────────────────────────────────────────────────

interface MetricCardProps {
  label: string
  value: string
  unit: string
  accent: string   // tailwind bg+border classes
  textColour: string
  hint?: string
}

function MetricCard({ label, value, unit, accent, textColour, hint }: MetricCardProps) {
  return (
    <div
      title={hint}
      className={`rounded-xl border px-5 py-3 shadow-sm min-w-[160px] ${accent}`}
    >
      <p className="text-[11px] uppercase tracking-wider text-slate-500 mb-0.5 font-medium">
        {label}
      </p>
      <p className={`text-xl font-bold font-mono ${textColour}`}>{value}</p>
      <p className="text-[10px] text-slate-400 mt-0.5">{unit}</p>
    </div>
  )
}

// ── Composite curve chart ─────────────────────────────────────────────────────

interface ChartPoint {
  H: number
  hotT?: number
  coldT?: number
}

function buildChartData(
  hot: CompositeCurvePoint[],
  cold: CompositeCurvePoint[],
  qHMin: number,
): ChartPoint[] {
  // Merge hot and cold onto a shared H axis.
  // Cold composite is shifted right by qHMin so the curves are positioned
  // correctly for the standard T-H diagram (gap at top = Q_H_min).
  const pointsMap = new Map<number, ChartPoint>()

  const upsert = (H: number, patch: Partial<ChartPoint>) => {
    const existing = pointsMap.get(H) ?? { H }
    pointsMap.set(H, { ...existing, ...patch })
  }

  hot.forEach(p => upsert(p.H, { hotT: p.T }))
  cold.forEach(p => upsert(p.H + qHMin, { coldT: p.T }))

  return Array.from(pointsMap.values()).sort((a, b) => a.H - b.H)
}

interface CCTooltipProps {
  active?: boolean
  payload?: Array<{ name: string; value: number; color: string }>
  label?: number
}

function CCTooltip({ active, payload, label }: CCTooltipProps) {
  if (!active || !payload?.length) return null
  return (
    <div className="rounded-lg border border-slate-200 bg-white px-3 py-2 shadow-lg text-xs">
      <p className="font-semibold text-slate-600 mb-1">H = {label?.toFixed(1)} kW</p>
      {payload.map(p => (
        <p key={p.name} style={{ color: p.color }}>
          {p.name}: {p.value.toFixed(1)} °C
        </p>
      ))}
    </div>
  )
}

function CompositeChart({
  hot,
  cold,
  qHMin,
  pinchT,
}: {
  hot: CompositeCurvePoint[]
  cold: CompositeCurvePoint[]
  qHMin: number
  pinchT: number
}) {
  const data = buildChartData(hot, cold, qHMin)
  if (!data.length) return null

  return (
    <ResponsiveContainer width="100%" height={280}>
      <LineChart data={data} margin={{ top: 8, right: 24, bottom: 8, left: 8 }}>
        <CartesianGrid strokeDasharray="3 3" stroke="#e2e8f0" />
        <XAxis
          dataKey="H"
          type="number"
          domain={['dataMin', 'dataMax']}
          tickFormatter={v => v.toFixed(0)}
          label={{ value: 'Enthalpy (kW)', position: 'insideBottomRight', offset: -4, fontSize: 11 }}
          tick={{ fontSize: 10, fill: '#64748b' }}
          axisLine={false}
          tickLine={false}
        />
        <YAxis
          tickFormatter={v => `${v}°C`}
          tick={{ fontSize: 10, fill: '#64748b' }}
          axisLine={false}
          tickLine={false}
        >
        </YAxis>
        <Tooltip content={<CCTooltip />} />
        <Legend
          wrapperStyle={{ fontSize: 11 }}
          formatter={(value) => value === 'hotT' ? 'Hot composite' : 'Cold composite'}
        />
        <Line
          type="linear"
          dataKey="hotT"
          name="hotT"
          stroke="#f87171"
          strokeWidth={2}
          dot={false}
          connectNulls
        />
        <Line
          type="linear"
          dataKey="coldT"
          name="coldT"
          stroke="#60a5fa"
          strokeWidth={2}
          dot={false}
          connectNulls
        />
      </LineChart>
    </ResponsiveContainer>
  )
}

// ── Interval table ────────────────────────────────────────────────────────────

function IntervalTable({ intervals, pinchT }: { intervals: PinchResult['temperature_intervals']; pinchT: number }) {
  if (!intervals.length) return null
  return (
    <div className="overflow-x-auto rounded-lg border border-slate-200">
      <table className="w-full text-xs border-collapse">
        <thead>
          <tr className="bg-slate-100 text-slate-600 text-right">
            <th className="px-3 py-2 border-b border-slate-200 font-semibold text-left">Interval (shifted °C)</th>
            <th className="px-3 py-2 border-b border-slate-200 font-semibold">ΣHCP (kW/K)</th>
            <th className="px-3 py-2 border-b border-slate-200 font-semibold">ΣCCP (kW/K)</th>
            <th className="px-3 py-2 border-b border-slate-200 font-semibold">ΔH (kW)</th>
            <th className="px-3 py-2 border-b border-slate-200 font-semibold">Cascade out (kW)</th>
          </tr>
        </thead>
        <tbody>
          {intervals.map((iv, i) => {
            const isPinch = Math.abs(iv.cascade_out) < 1e-4
            return (
              <tr
                key={i}
                className={[
                  i % 2 === 0 ? 'bg-white' : 'bg-slate-50',
                  isPinch ? 'ring-1 ring-inset ring-amber-300' : '',
                ].join(' ')}
              >
                <td className="px-3 py-1.5 border-b border-slate-100 font-mono text-slate-700">
                  {iv.t_high.toFixed(1)} → {iv.t_low.toFixed(1)}
                  {isPinch && (
                    <span className="ml-2 text-[10px] bg-amber-100 text-amber-700 font-bold px-1.5 py-0.5 rounded">
                      PINCH
                    </span>
                  )}
                </td>
                <td className="px-3 py-1.5 border-b border-slate-100 text-right font-mono">{iv.hcp_sum.toFixed(3)}</td>
                <td className="px-3 py-1.5 border-b border-slate-100 text-right font-mono">{iv.ccp_sum.toFixed(3)}</td>
                <td className={`px-3 py-1.5 border-b border-slate-100 text-right font-mono ${iv.delta_h >= 0 ? 'text-red-600' : 'text-blue-600'}`}>
                  {iv.delta_h >= 0 ? '+' : ''}{iv.delta_h.toFixed(3)}
                </td>
                <td className="px-3 py-1.5 border-b border-slate-100 text-right font-mono">
                  {iv.cascade_out.toFixed(3)}
                </td>
              </tr>
            )
          })}
        </tbody>
      </table>
    </div>
  )
}

// ── Stream form for manual entry ──────────────────────────────────────────────

interface StreamFormProps {
  onSubmit: (req: PinchRequest) => void
  loading: boolean
}

function StreamForm({ onSubmit, loading }: StreamFormProps) {
  const [deltaT, setDeltaT] = useState('10')
  const [rows, setRows] = useState<StreamInput[]>([
    { name: 'H1', supply_temp: 150, target_temp: 60, cp: 3, stream_type: 'hot' },
    { name: 'C1', supply_temp: 20, target_temp: 125, cp: 2, stream_type: 'cold' },
  ])

  const addRow = () =>
    setRows(r => [...r, { name: '', supply_temp: 0, target_temp: 0, cp: 1, stream_type: 'hot' }])

  const removeRow = (i: number) => setRows(r => r.filter((_, idx) => idx !== i))

  const updateRow = (i: number, patch: Partial<StreamInput>) =>
    setRows(r => r.map((row, idx) => (idx === i ? { ...row, ...patch } : row)))

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault()
    onSubmit({ delta_T_min: parseFloat(deltaT) || 10, streams: rows })
  }

  return (
    <form onSubmit={handleSubmit} className="space-y-3">
      <div className="flex items-center gap-3">
        <label className="text-xs font-semibold text-slate-600">ΔT_min (K)</label>
        <input
          type="number"
          value={deltaT}
          onChange={e => setDeltaT(e.target.value)}
          className="w-20 text-xs border border-slate-300 rounded px-2 py-1 font-mono"
          min="0.1"
          step="0.5"
        />
      </div>

      <div className="overflow-x-auto rounded-lg border border-slate-200">
        <table className="w-full text-xs border-collapse">
          <thead>
            <tr className="bg-slate-100 text-slate-600">
              <th className="px-2 py-1.5 border-b border-slate-200 font-semibold text-left">Name</th>
              <th className="px-2 py-1.5 border-b border-slate-200 font-semibold text-left">Type</th>
              <th className="px-2 py-1.5 border-b border-slate-200 font-semibold text-right">T_supply (°C)</th>
              <th className="px-2 py-1.5 border-b border-slate-200 font-semibold text-right">T_target (°C)</th>
              <th className="px-2 py-1.5 border-b border-slate-200 font-semibold text-right">CP (kW/K)</th>
              <th className="px-2 py-1.5 border-b border-slate-200" />
            </tr>
          </thead>
          <tbody>
            {rows.map((row, i) => (
              <tr key={i} className={i % 2 === 0 ? 'bg-white' : 'bg-slate-50'}>
                <td className="px-2 py-1">
                  <input
                    className="w-16 text-xs border border-slate-300 rounded px-1.5 py-0.5 font-mono"
                    value={row.name}
                    onChange={e => updateRow(i, { name: e.target.value })}
                    placeholder="H1"
                  />
                </td>
                <td className="px-2 py-1">
                  <select
                    className="text-xs border border-slate-300 rounded px-1.5 py-0.5"
                    value={row.stream_type}
                    onChange={e => updateRow(i, { stream_type: e.target.value as 'hot' | 'cold' })}
                  >
                    <option value="hot">Hot</option>
                    <option value="cold">Cold</option>
                  </select>
                </td>
                {(['supply_temp', 'target_temp', 'cp'] as const).map(field => (
                  <td key={field} className="px-2 py-1">
                    <input
                      type="number"
                      className="w-20 text-xs border border-slate-300 rounded px-1.5 py-0.5 font-mono text-right"
                      value={row[field]}
                      onChange={e => updateRow(i, { [field]: parseFloat(e.target.value) })}
                      step="any"
                    />
                  </td>
                ))}
                <td className="px-2 py-1">
                  <button
                    type="button"
                    onClick={() => removeRow(i)}
                    className="text-slate-400 hover:text-red-500 text-sm leading-none"
                  >
                    ×
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <div className="flex gap-2">
        <button
          type="button"
          onClick={addRow}
          className="text-xs px-3 py-1.5 rounded-lg border border-slate-300 hover:bg-slate-50 text-slate-600 font-semibold"
        >
          + Add stream
        </button>
        <button
          type="submit"
          disabled={loading || rows.length === 0}
          className={[
            'flex items-center gap-1.5 px-4 py-1.5 rounded-lg text-xs font-semibold',
            'bg-teal-600 hover:bg-teal-700 text-white transition-colors shadow-sm',
            'disabled:opacity-50 disabled:cursor-not-allowed',
          ].join(' ')}
        >
          {loading ? 'Running…' : 'Run Pinch Analysis'}
        </button>
      </div>
    </form>
  )
}

// ── Section heading ───────────────────────────────────────────────────────────

function SH({ children }: { children: React.ReactNode }) {
  return (
    <h3 className="text-xs font-bold uppercase tracking-wider text-slate-500 mb-2">
      {children}
    </h3>
  )
}

// ── Public component ──────────────────────────────────────────────────────────

export interface PinchPanelProps {
  simId: string
  onRunPinch: (simId: string, req: PinchRequest) => Promise<PinchResult>
}

export function PinchPanel({ simId, onRunPinch }: PinchPanelProps) {
  const [result, setResult] = useState<PinchResult | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const handleRun = async (req: PinchRequest) => {
    setLoading(true)
    setError(null)
    try {
      const r = await onRunPinch(simId, req)
      setResult(r)
    } catch (err: unknown) {
      const msg =
        (err as { response?: { data?: { detail?: string } } })?.response?.data?.detail ??
        'Pinch analysis failed'
      setError(typeof msg === 'string' ? msg : JSON.stringify(msg))
    } finally {
      setLoading(false)
    }
  }

  return (
    <div className="space-y-6">
      {/* Stream input form */}
      <section>
        <SH>Stream Definitions</SH>
        <StreamForm onSubmit={handleRun} loading={loading} />
      </section>

      {error && (
        <div className="rounded-xl border border-red-200 bg-red-50 p-4 text-xs text-red-700">
          {error}
        </div>
      )}

      {result && (
        <>
          {/* ── Key targets ── */}
          <section>
            <SH>Minimum Energy Targets</SH>
            <div className="flex flex-wrap gap-3">
              <MetricCard
                label="Pinch Temperature"
                value={result.pinch_temperature.toFixed(1)}
                unit="°C (hot side)"
                accent="bg-amber-50 border-amber-200"
                textColour="text-amber-700"
                hint={`Cold-side pinch = ${(result.pinch_temperature - result.delta_T_min).toFixed(1)} °C`}
              />
              <MetricCard
                label="Q_H_min"
                value={result.q_h_min.toFixed(2)}
                unit="kW — min hot utility"
                accent="bg-red-50 border-red-200"
                textColour="text-red-600"
                hint="Minimum external heating required"
              />
              <MetricCard
                label="Q_C_min"
                value={result.q_c_min.toFixed(2)}
                unit="kW — min cold utility"
                accent="bg-blue-50 border-blue-200"
                textColour="text-blue-600"
                hint="Minimum external cooling required"
              />
              {result.energy_saving_kw != null && result.energy_saving_kw > 0 && (
                <MetricCard
                  label="Energy Saving Potential"
                  value={result.energy_saving_kw.toFixed(2)}
                  unit="kW vs. current design"
                  accent="bg-emerald-50 border-emerald-200"
                  textColour="text-emerald-700"
                  hint="How much hot utility can be recovered with optimal HEN design"
                />
              )}
            </div>
          </section>

          {/* ── Composite curves ── */}
          {(result.hot_composite.length > 0 || result.cold_composite.length > 0) && (
            <section>
              <SH>Composite Curves (T-H Diagram)</SH>
              <p className="text-[11px] text-slate-400 mb-3">
                Hot composite (coral) and cold composite (blue) plotted on the
                same enthalpy axis. The cold composite is shifted right by Q_H_min
                ({result.q_h_min.toFixed(2)} kW) so the curves touch at the pinch.
              </p>
              <CompositeChart
                hot={result.hot_composite}
                cold={result.cold_composite}
                qHMin={result.q_h_min}
                pinchT={result.pinch_temperature}
              />
            </section>
          )}

          {/* ── Temperature intervals ── */}
          {result.temperature_intervals.length > 0 && (
            <section>
              <SH>Problem Table (Temperature Intervals)</SH>
              <IntervalTable
                intervals={result.temperature_intervals}
                pinchT={result.pinch_temperature}
              />
            </section>
          )}

          {/* ── Above / below pinch ── */}
          <section>
            <SH>Stream Classification</SH>
            <div className="grid grid-cols-2 gap-4 text-xs">
              {(['above', 'below'] as const).map(side => {
                const data = side === 'above' ? result.above_pinch_streams : result.below_pinch_streams
                const label = side === 'above'
                  ? `Above pinch (T > ${result.pinch_temperature.toFixed(1)} °C)`
                  : `Below pinch (T < ${result.pinch_temperature.toFixed(1)} °C)`
                return (
                  <div key={side} className="rounded-xl border border-slate-200 p-3">
                    <p className="font-bold text-slate-600 mb-2">{label}</p>
                    {(['hot', 'cold'] as const).map(kind => (
                      data[kind]?.length > 0 && (
                        <div key={kind} className="mb-1.5">
                          <p className={`text-[10px] uppercase font-semibold mb-1 ${kind === 'hot' ? 'text-red-500' : 'text-blue-500'}`}>
                            {kind} streams
                          </p>
                          {data[kind].map((s, i) => (
                            <p key={i} className="font-mono text-slate-600">
                              {s.name || `${kind[0].toUpperCase()}${i + 1}`}:&nbsp;
                              {s.supply_temp.toFixed(1)} → {s.target_temp.toFixed(1)} °C,&nbsp;
                              CP = {s.cp.toFixed(3)} kW/K
                            </p>
                          ))}
                        </div>
                      )
                    ))}
                  </div>
                )
              })}
            </div>
          </section>
        </>
      )}
    </div>
  )
}
