/**
 * ResultsPanel — comprehensive simulation results view.
 *
 * Sections (stacked, scrollable)
 * ───────────────────────────────
 *  1. Header bar      — title, run timestamp, Export-to-Excel button
 *  2. Stream table    — name, T (K), P (bar), flow (kmol/hr), composition columns
 *  3. Energy summary  — heating / cooling / net duty cards
 *  4. Unit flow chart — Recharts horizontal bar: kmol/hr through each unit op
 *  5. Warnings panel  — solver warning messages
 */

import * as XLSX from 'xlsx'
import {
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts'
import type { Edge, Node } from '@xyflow/react'
import type { SimulationResult, StreamState } from '../../types'

// ── Conversion helpers ────────────────────────────────────────────────────────

const toK       = (c: number) => c + 273.15
const toKmolHr  = (molS: number) => molS * 3.6

// ── Unit-op colour palette (mirrors sidebar bar colours) ──────────────────────

const OP_COLOURS: Record<string, string> = {
  feed:           '#14b8a6',
  product:        '#10b981',
  mixer:          '#8b5cf6',
  splitter:       '#f59e0b',
  heat_exchanger: '#ef4444',
  flash_drum:     '#0ea5e9',
  pfr:            '#84cc16',
  pump:           '#f97316',
}

// ── Type helpers ──────────────────────────────────────────────────────────────

type D = Record<string, unknown>

// ── Excel export ──────────────────────────────────────────────────────────────

function exportToExcel(
  result: SimulationResult,
  streams: Record<string, StreamState>,
) {
  const wb = XLSX.utils.book_new()

  // ── Sheet 1: Streams ──
  const allComps = [...new Set(
    Object.values(streams).flatMap(s => Object.keys(s.composition))
  )].sort()

  const streamRows = Object.entries(streams).map(([, s]) => {
    const row: Record<string, string | number> = {
      'Stream':        s.name ?? '—',
      'T (K)':         +toK(s.temperature).toFixed(2),
      'P (bar)':       +s.pressure.toFixed(4),
      'Flow (kmol/hr)':+toKmolHr(s.flow).toFixed(4),
      'Vapour ψ':      s.vapor_fraction != null ? +s.vapor_fraction.toFixed(6) : '—',
    }
    for (const c of allComps) {
      row[c] = +(s.composition[c] ?? 0).toFixed(6)
    }
    return row
  })
  XLSX.utils.book_append_sheet(wb, XLSX.utils.json_to_sheet(streamRows), 'Streams')

  // ── Sheet 2: Energy Balance ──
  const eb = result.energy_balance as Record<string, number>
  const ebRows = Object.entries(eb).map(([k, v]) => ({
    Metric:    k.replace(/_/g, ' '),
    'Value (kW)': +v.toFixed(4),
  }))
  XLSX.utils.book_append_sheet(wb, XLSX.utils.json_to_sheet(ebRows), 'Energy Balance')

  // ── Sheet 3: Warnings (only if any) ──
  if (result.warnings.length > 0) {
    const wRows = result.warnings.map((w, i) => ({ '#': i + 1, Warning: w }))
    XLSX.utils.book_append_sheet(wb, XLSX.utils.json_to_sheet(wRows), 'Warnings')
  }

  XLSX.writeFile(wb, 'chemflow_results.xlsx')
}

// ── Stream results table ──────────────────────────────────────────────────────

function StreamResultsTable({ streams }: { streams: Record<string, StreamState> }) {
  const entries = Object.entries(streams)
  if (!entries.length) return <p className="text-sm text-slate-400 italic">No stream data.</p>

  const allComps = [...new Set(
    entries.flatMap(([, s]) => Object.keys(s.composition))
  )].sort()

  return (
    <div className="overflow-x-auto rounded-lg border border-slate-200">
      <table className="w-full text-xs border-collapse">
        <thead>
          <tr className="bg-slate-100 text-slate-600 text-left">
            <th className="px-3 py-2 border-b border-slate-200 font-semibold whitespace-nowrap">
              Stream
            </th>
            <th className="px-3 py-2 border-b border-slate-200 font-semibold text-right whitespace-nowrap">
              T (K)
            </th>
            <th className="px-3 py-2 border-b border-slate-200 font-semibold text-right whitespace-nowrap">
              P (bar)
            </th>
            <th className="px-3 py-2 border-b border-slate-200 font-semibold text-right whitespace-nowrap">
              Flow (kmol/hr)
            </th>
            <th className="px-3 py-2 border-b border-slate-200 font-semibold text-right whitespace-nowrap">
              Vapour ψ
            </th>
            {allComps.map(c => (
              <th
                key={c}
                className="px-3 py-2 border-b border-slate-200 font-semibold text-right whitespace-nowrap capitalize"
              >
                {c}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {entries.map(([edgeId, s], ri) => (
            <tr key={edgeId} className={ri % 2 === 0 ? 'bg-white' : 'bg-slate-50'}>
              <td className="px-3 py-1.5 border-b border-slate-100 font-semibold text-teal-700 whitespace-nowrap">
                {s.name ?? edgeId}
              </td>
              <td className="px-3 py-1.5 border-b border-slate-100 text-right font-mono">
                {toK(s.temperature).toFixed(2)}
              </td>
              <td className="px-3 py-1.5 border-b border-slate-100 text-right font-mono">
                {s.pressure.toFixed(4)}
              </td>
              <td className="px-3 py-1.5 border-b border-slate-100 text-right font-mono">
                {toKmolHr(s.flow).toFixed(4)}
              </td>
              <td className="px-3 py-1.5 border-b border-slate-100 text-right font-mono">
                {s.vapor_fraction != null ? s.vapor_fraction.toFixed(4) : '—'}
              </td>
              {allComps.map(c => (
                <td
                  key={c}
                  className="px-3 py-1.5 border-b border-slate-100 text-right font-mono"
                >
                  {(s.composition[c] ?? 0).toFixed(4)}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

// ── Energy summary cards ──────────────────────────────────────────────────────

const ENERGY_LABELS: Record<string, { label: string; hint: string }> = {
  heating_kW:    { label: 'Heating',  hint: 'Total heat added to process' },
  cooling_kW:    { label: 'Cooling',  hint: 'Total heat removed from process' },
  total_duty_kW: { label: 'Net duty', hint: 'Algebraic sum (positive = net heating)' },
}

function EnergySummary({ energyBalance }: { energyBalance: Record<string, number> }) {
  return (
    <div className="flex flex-wrap gap-3">
      {Object.entries(energyBalance).map(([key, value]) => {
        const meta  = ENERGY_LABELS[key]
        const label = meta?.label ?? key.replace(/_/g, ' ')
        const hint  = meta?.hint  ?? ''

        const isNeg  = value < 0
        const isPos  = value > 0
        const sign   = isPos ? '+' : ''
        const colour = isPos ? 'text-red-600' : isNeg ? 'text-blue-600' : 'text-slate-700'
        const bg     = isPos ? 'bg-red-50 border-red-200'
                     : isNeg ? 'bg-blue-50 border-blue-200'
                     :         'bg-slate-50 border-slate-200'

        return (
          <div
            key={key}
            title={hint}
            className={`rounded-xl border px-5 py-3 shadow-sm min-w-[160px] ${bg}`}
          >
            <p className="text-[11px] uppercase tracking-wider text-slate-500 mb-0.5 font-medium">
              {label}
            </p>
            <p className={`text-xl font-bold font-mono ${colour}`}>
              {sign}{value.toFixed(3)}
            </p>
            <p className="text-[10px] text-slate-400 mt-0.5">kW</p>
          </div>
        )
      })}
    </div>
  )
}

// ── Unit flow bar chart ───────────────────────────────────────────────────────

interface ChartDatum {
  name:    string
  flow:    number
  colour:  string
}

function buildChartData(
  nodes: Node[],
  edges: Edge[],
  streams: Record<string, StreamState>,
): ChartDatum[] {
  return nodes
    .map(n => {
      const d       = n.data as D
      const outFlow = edges
        .filter(e => e.source === n.id)
        .reduce((sum, e) => {
          const s = streams[e.id]
          return sum + (s ? toKmolHr(s.flow) : 0)
        }, 0)
      return {
        name:   (d.label as string) || (d.nodeType as string),
        flow:   +outFlow.toFixed(3),
        colour: OP_COLOURS[(d.nodeType as string)] ?? '#94a3b8',
      }
    })
    .filter(d => d.flow > 0)
    .sort((a, b) => b.flow - a.flow)
}

interface CustomTooltipProps {
  active?:  boolean
  payload?: Array<{ value: number }>
  label?:   string
}

function ChartTooltip({ active, payload, label }: CustomTooltipProps) {
  if (!active || !payload?.length) return null
  return (
    <div className="rounded-lg border border-slate-200 bg-white px-3 py-2 shadow-lg text-xs">
      <p className="font-semibold text-slate-700 mb-1">{label}</p>
      <p className="font-mono text-teal-600">{payload[0].value.toFixed(3)} kmol/hr</p>
    </div>
  )
}

function UnitFlowChart({ data }: { data: ChartDatum[] }) {
  if (!data.length) {
    return (
      <p className="text-sm text-slate-400 italic py-4">
        No flow data — run simulation first.
      </p>
    )
  }

  // Clamp chart height: 40 px per bar, min 120, max 360
  const chartHeight = Math.min(360, Math.max(120, data.length * 44))

  return (
    <ResponsiveContainer width="100%" height={chartHeight}>
      <BarChart
        data={data}
        layout="vertical"
        margin={{ top: 4, right: 32, bottom: 4, left: 16 }}
      >
        <CartesianGrid strokeDasharray="3 3" horizontal={false} stroke="#e2e8f0" />
        <XAxis
          type="number"
          unit=" kmol/hr"
          tick={{ fontSize: 11, fill: '#64748b' }}
          axisLine={false}
          tickLine={false}
        />
        <YAxis
          type="category"
          dataKey="name"
          width={110}
          tick={{ fontSize: 11, fill: '#475569' }}
          axisLine={false}
          tickLine={false}
        />
        <Tooltip content={<ChartTooltip />} cursor={{ fill: '#f1f5f9' }} />
        <Bar dataKey="flow" radius={[0, 4, 4, 0]} maxBarSize={28}>
          {data.map((d, i) => (
            <Cell key={i} fill={d.colour} />
          ))}
        </Bar>
      </BarChart>
    </ResponsiveContainer>
  )
}

// ── Warnings panel ────────────────────────────────────────────────────────────

function WarningsPanel({ warnings }: { warnings: string[] }) {
  if (!warnings.length) return null
  return (
    <div className="rounded-xl border border-amber-200 bg-amber-50 p-4">
      <p className="text-xs font-bold text-amber-800 uppercase tracking-wide mb-2">
        Solver warnings ({warnings.length})
      </p>
      <ul className="space-y-1">
        {warnings.map((w, i) => (
          <li key={i} className="flex gap-2 text-xs text-amber-700">
            <span className="mt-px text-amber-500 flex-shrink-0">⚠</span>
            {w}
          </li>
        ))}
      </ul>
    </div>
  )
}

// ── Section heading ───────────────────────────────────────────────────────────

function SectionHeading({ children }: { children: React.ReactNode }) {
  return (
    <h3 className="text-xs font-bold uppercase tracking-wider text-slate-500 mb-2">
      {children}
    </h3>
  )
}

// ── Public component ──────────────────────────────────────────────────────────

export interface ResultsPanelProps {
  result:   SimulationResult
  nodes:    Node[]
  edges:    Edge[]
  warnings: string[]
  height:   number          // controlled by draggable splitter
}

export function ResultsPanel({
  result,
  nodes,
  edges,
  warnings,
  height,
}: ResultsPanelProps) {
  const streams     = result.streams as Record<string, StreamState>
  const eb          = result.energy_balance as Record<string, number>
  const chartData   = buildChartData(nodes, edges, streams)
  const runAt       = new Date(result.created_at).toLocaleTimeString()

  return (
    <div
      className="flex flex-col overflow-hidden border-t border-slate-200 bg-white"
      style={{ height }}
    >
      {/* Header */}
      <div className="flex items-center justify-between px-5 py-2 border-b border-slate-200 bg-slate-50 flex-shrink-0">
        <div className="flex items-center gap-3">
          <span className="text-xs font-bold text-slate-700 uppercase tracking-wide">
            Simulation Results
          </span>
          <span className="text-[11px] text-slate-400">run at {runAt}</span>
          {warnings.length > 0 && (
            <span className="text-[10px] bg-amber-100 text-amber-700 font-semibold px-2 py-0.5 rounded-full">
              {warnings.length} warning{warnings.length > 1 ? 's' : ''}
            </span>
          )}
        </div>

        <button
          onClick={() => exportToExcel(result, streams)}
          className={[
            'flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-semibold',
            'bg-emerald-600 hover:bg-emerald-700 text-white',
            'transition-colors duration-150 shadow-sm',
          ].join(' ')}
        >
          <span>⬇</span> Export Excel
        </button>
      </div>

      {/* Scrollable body */}
      <div className="flex-1 overflow-y-auto overflow-x-hidden">
        {/* Two-column layout for table + charts on wide screens */}
        <div className="p-5 space-y-6">

          {/* ── Stream table ── */}
          <section>
            <SectionHeading>Stream Table</SectionHeading>
            <StreamResultsTable streams={streams} />
          </section>

          {/* ── Energy summary ── */}
          <section>
            <SectionHeading>Energy Balance</SectionHeading>
            <EnergySummary energyBalance={eb} />
          </section>

          {/* ── Unit flow chart ── */}
          <section>
            <SectionHeading>Mass flow by unit operation (kmol/hr)</SectionHeading>
            <UnitFlowChart data={chartData} />
          </section>

          {/* ── Warnings ── */}
          {warnings.length > 0 && (
            <section>
              <SectionHeading>Warnings</SectionHeading>
              <WarningsPanel warnings={warnings} />
            </section>
          )}

        </div>
      </div>
    </div>
  )
}
