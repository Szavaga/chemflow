import {
  Bar,
  BarChart,
  CartesianGrid,
  Legend,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts'
import type { CSTRResult, FlashResult, HEXResult, UnitType } from '../types'

interface Props {
  unitType: UnitType
  result: FlashResult | CSTRResult | HEXResult
  componentNames?: string[]
}

function Stat({
  label, value, highlight = false,
}: { label: string; value: string; highlight?: boolean }) {
  return (
    <div className={`stat${highlight ? ' stat-highlight' : ''}`}>
      <div className="stat-label">{label}</div>
      <div className="stat-value">{value}</div>
    </div>
  )
}

function FlashResults({ result, names }: { result: FlashResult; names: string[] }) {
  const data = result.liquid_composition.map((_, i) => ({
    name: names[i] ?? `Comp ${i + 1}`,
    'Liquid (mol%)': parseFloat((result.liquid_composition[i] * 100).toFixed(2)),
    'Vapor (mol%)': parseFloat((result.vapor_composition[i] * 100).toFixed(2)),
  }))

  return (
    <div className="results card">
      <h2>Flash Drum Results</h2>
      <div className="result-stats">
        <Stat label="Vapor Fraction" value={(result.vapor_fraction * 100).toFixed(2) + ' %'} highlight />
        <Stat label="Vapor Flow" value={result.vapor_flow.toFixed(3) + ' mol/s'} />
        <Stat label="Liquid Flow" value={result.liquid_flow.toFixed(3) + ' mol/s'} />
        <Stat label="Status" value={result.converged ? 'Converged' : 'Failed'} />
      </div>
      <h3>Phase Compositions (mol%)</h3>
      <ResponsiveContainer width="100%" height={220}>
        <BarChart data={data} margin={{ top: 4, right: 8, bottom: 4, left: 0 }}>
          <CartesianGrid strokeDasharray="3 3" stroke="#e2e8f0" />
          <XAxis dataKey="name" tick={{ fontSize: 12 }} />
          <YAxis unit="%" tick={{ fontSize: 12 }} />
          <Tooltip formatter={(v: number) => v.toFixed(2) + ' %'} />
          <Legend wrapperStyle={{ fontSize: 12 }} />
          <Bar dataKey="Liquid (mol%)" fill="#6366f1" radius={[3, 3, 0, 0]} />
          <Bar dataKey="Vapor (mol%)" fill="#22c55e" radius={[3, 3, 0, 0]} />
        </BarChart>
      </ResponsiveContainer>
      <p className="hint">{result.message}</p>
    </div>
  )
}

function CSTRResults({ result }: { result: CSTRResult }) {
  const convPct = result.conversion * 100
  return (
    <div className="results card">
      <h2>CSTR Results</h2>
      <div className="result-stats">
        <Stat label="Conversion" value={convPct.toFixed(2) + ' %'} highlight />
        <Stat label="Outlet [A]" value={result.outlet_concentration.toFixed(4) + ' mol/L'} />
        <Stat label="Residence Time" value={result.residence_time.toFixed(2) + ' s'} />
        <Stat label="Reaction Rate" value={result.reaction_rate.toExponential(3) + ' mol/(L·s)'} />
        <Stat label="Space-Time Yield" value={result.space_time_yield.toFixed(4) + ' mol/s'} />
        <Stat label="Status" value={result.converged ? 'Converged' : 'Failed'} />
      </div>
      <h3>Conversion</h3>
      <div className="conversion-bar">
        <div
          className="conversion-fill"
          style={{ width: `${Math.max(convPct, 4)}%` }}
        >
          {convPct.toFixed(1)}%
        </div>
      </div>
      <p className="hint">{result.message}</p>
    </div>
  )
}

function HEXResults({ result }: { result: HEXResult }) {
  const data = [
    { name: 'Hot In', T: undefined },
    { name: 'Cold Out', T: result.cold_outlet_temp },
  ]
  return (
    <div className="results card">
      <h2>Heat Exchanger Results</h2>
      <div className="result-stats">
        <Stat label="Cold Outlet Temp" value={result.cold_outlet_temp.toFixed(2) + ' °C'} highlight />
        <Stat label="Heat Duty" value={(result.heat_duty / 1000).toFixed(3) + ' kW'} />
        <Stat label="LMTD" value={result.lmtd.toFixed(2) + ' K'} />
        <Stat label="UA" value={(result.UA / 1000).toFixed(3) + ' kW/K'} />
        <Stat label="Effectiveness" value={(result.effectiveness * 100).toFixed(1) + ' %'} />
        <Stat label="Status" value={result.converged ? 'Converged' : 'Failed'} />
      </div>
      <p className="hint">{result.message}</p>
    </div>
  )
}

export default function ResultsChart({ unitType, result, componentNames = [] }: Props) {
  if (unitType === 'flash_drum') return <FlashResults result={result as FlashResult} names={componentNames} />
  if (unitType === 'cstr') return <CSTRResults result={result as CSTRResult} />
  if (unitType === 'heat_exchanger') return <HEXResults result={result as HEXResult} />
  return null
}
