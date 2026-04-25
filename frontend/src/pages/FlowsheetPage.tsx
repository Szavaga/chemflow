/**
 * FlowsheetPage — visual process flowsheet editor.
 *
 * Layout
 * ──────
 *  ┌────────────────────────────────────────────────────────────────────┐
 *  │ TOOLBAR  (white, top)                                              │
 *  ├──────────────┬───────────────────────────────────────┬────────────┤
 *  │  PALETTE     │  CANVAS  (light, React Flow)          │ CONFIG     │
 *  │  (dark,left) │                                       │ PANEL      │
 *  │  Unit ops    │  Drag nodes, draw edges               │ (dark,     │
 *  │  palette     │                        [Results] ──►  │  right,    │
 *  │              │                                       │  on-click) │
 *  └──────────────┴───────────────────────────────────────┴────────────┘
 *
 * Auto-save: every change to nodes/edges triggers a 1-second debounced
 *   PUT /simulations/{id}/flowsheet.
 *
 * Config panel (right):
 *   Opens when a node is clicked. Shows unit-specific parameters at the
 *   top, then an "Inlet Conditions" section per inlet handle. When an
 *   inlet has no upstream edge, its T/P/flow/composition fields are
 *   editable and stored on the node data.  When connected, conditions
 *   are shown read-only from the last simulation result.
 */

import {
  Background,
  BackgroundVariant,
  Controls,
  MiniMap,
  ReactFlow,
  ReactFlowProvider,
  addEdge,
  useEdgesState,
  useNodesState,
  useReactFlow,
} from '@xyflow/react'
import '@xyflow/react/dist/style.css'
import { useCallback, useEffect, useRef, useState } from 'react'
import { useNavigate, useParams } from 'react-router-dom'
import { fetchComponents, fetchSimulation, runPinchAnalysis, runSimulation, saveFlowsheet } from '../api/client'
import { UnitNode } from '../components/flowsheet/UnitNode'
import { StreamEdge } from '../components/flowsheet/StreamEdge'
import { ResultsPanel } from '../components/results/ResultsPanel'
import { ControlStudio } from '../components/mpc/ControlStudio'
import { ComponentManager } from '../components/components/ComponentManager'
import type {
  Connection,
  Edge,
  Node,
} from '@xyflow/react'
import type {
  ChemicalComponent,
  FlowsheetEdge,
  FlowsheetNode,
  MPCNodeSummary,
  SimulationResult,
  StreamState,
} from '../types'

// ── React Flow type registrations ─────────────────────────────────────────────

const NODE_TYPES = { unit: UnitNode  } as const
const EDGE_TYPES = { stream: StreamEdge } as const

// ── Sidebar palette metadata ──────────────────────────────────────────────────

const PALETTE_ITEMS = [
  { type: 'feed',           label: 'Feed',           desc: 'Feed stream source',          bar: 'bg-teal-500'    },
  { type: 'mixer',          label: 'Mixer',          desc: 'Combine multiple streams',    bar: 'bg-violet-500'  },
  { type: 'splitter',       label: 'Splitter',       desc: 'Split one stream into two',   bar: 'bg-amber-500'   },
  { type: 'heat_exchanger', label: 'Heat Exchanger', desc: 'Heater / cooler',             bar: 'bg-red-500'     },
  { type: 'flash_drum',     label: 'Flash Drum',     desc: 'Isothermal VLE flash',        bar: 'bg-sky-500'     },
  { type: 'pfr',            label: 'PFR',            desc: 'Plug-flow reactor',           bar: 'bg-lime-500'    },
  { type: 'cstr',           label: 'CSTR',           desc: 'Stirred-tank reactor + MPC',  bar: 'bg-cyan-500'    },
  { type: 'pump',           label: 'Pump',           desc: 'Pressure increase',           bar: 'bg-orange-500'  },
  { type: 'product',        label: 'Product',        desc: 'Product stream sink',         bar: 'bg-emerald-500' },
]

// ── Inlet counts per unit type (mirrors UnitNode META) ────────────────────────

const UNIT_INLETS: Record<string, number> = {
  feed: 0, product: 1, mixer: 2, splitter: 1,
  heat_exchanger: 1, flash_drum: 1, pfr: 1, pump: 1, cstr: 1,
}

function inletHandleIds(count: number): string[] {
  if (count === 0) return []
  if (count === 1) return ['in']
  return Array.from({ length: count }, (_, i) => `in${i}`)
}

// ── Node id generator ─────────────────────────────────────────────────────────

let _ctr = 1
const nextId = () => `n${_ctr++}`

// ── Shared form helpers ───────────────────────────────────────────────────────

type D = Record<string, unknown>

function labelCls() {
  return 'block text-[11px] font-medium text-slate-400 mb-0.5'
}
function inputCls() {
  return [
    'w-full rounded-md px-2 py-1.5 text-[12px] text-slate-100',
    'bg-slate-800 border border-slate-600',
    'focus:outline-none focus:border-teal-500',
    'placeholder-slate-500',
  ].join(' ')
}
function selectCls() {
  return [
    'w-full rounded-md px-2 py-1.5 text-[12px] text-slate-100',
    'bg-slate-800 border border-slate-600',
    'focus:outline-none focus:border-teal-500',
  ].join(' ')
}

function ParamField({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="mb-2.5">
      <span className={labelCls()}>{label}</span>
      {children}
    </div>
  )
}

// ── Feed component sub-panel (dynamic, CAS-keyed) ─────────────────────────────

function FeedComponentPanel({
  nodeId,
  data,
  onChange,
  projectId,
}: {
  nodeId: string
  data: D
  onChange: (id: string, d: D) => void
  projectId: string
}) {
  const comp = (data.composition as Record<string, number>) ?? {}
  const [allComponents, setAllComponents] = useState<ChemicalComponent[]>([])
  const [showManager, setShowManager] = useState(false)

  useEffect(() => {
    fetchComponents(undefined, 100)
      .then(setAllComponents)
      .catch(() => {/* ignore */})
  }, [])

  const setCasValue = (cas: string, val: number) =>
    onChange(nodeId, { ...data, composition: { ...comp, [cas]: val } })

  const removeCas = (cas: string) => {
    const next = { ...comp }
    delete next[cas]
    onChange(nodeId, { ...data, composition: next })
  }

  const addComponent = (c: ChemicalComponent) => {
    if (!(c.cas_number in comp)) {
      onChange(nodeId, { ...data, composition: { ...comp, [c.cas_number]: 0 } })
    }
    setShowManager(false)
  }

  const labelFor = (cas: string) => {
    const found = allComponents.find(c => c.cas_number === cas)
    return found ? found.name : cas
  }

  const missingProps = (cas: string) => {
    const found = allComponents.find(c => c.cas_number === cas)
    if (!found) return false
    return !found.tc || !found.pc || !found.omega
  }

  return (
    <>
      <p className="text-[11px] text-slate-400 mb-1 font-medium">
        Composition (mole fractions)
      </p>
      {Object.keys(comp).length === 0 && (
        <p className="text-[11px] text-slate-500 italic mb-2">
          No components added yet.
        </p>
      )}
      {Object.keys(comp).map(cas => (
        <div key={cas} className="mb-2">
          <div className="flex items-center justify-between mb-0.5">
            <span className="text-[11px] text-slate-300 truncate max-w-[130px]" title={cas}>
              {labelFor(cas)}
            </span>
            <div className="flex items-center gap-1">
              {missingProps(cas) && (
                <span className="text-[9px] bg-amber-800 text-amber-200 px-1 rounded font-bold"
                  title="Missing Tc/Pc/ω — flash calculations may fail">⚠ props</span>
              )}
              <button onClick={() => removeCas(cas)}
                className="text-slate-600 hover:text-red-400 text-[13px] leading-none">×</button>
            </div>
          </div>
          <input type="number" step="0.01" min="0" max="1" className={inputCls()}
            value={comp[cas] ?? 0}
            onChange={e => setCasValue(cas, +e.target.value)} />
        </div>
      ))}
      <button
        onClick={() => setShowManager(true)}
        className="w-full mt-1 py-1 rounded text-[11px] border border-dashed border-slate-600
                   text-slate-400 hover:border-teal-500 hover:text-teal-400 transition-colors"
      >
        + Browse components
      </button>
      <p className="text-[10px] text-slate-500 mt-1">
        Fractions are normalised by the solver. Keys are CAS numbers.
      </p>

      {showManager && (
        <ComponentManager
          projectId={projectId}
          activeCasList={Object.keys(comp)}
          onAdd={addComponent}
          onClose={() => setShowManager(false)}
        />
      )}
    </>
  )
}

// ── Unit-specific parameter fields ────────────────────────────────────────────

function UnitParams({
  node,
  onChange,
  projectId,
}: {
  node: Node
  onChange: (id: string, data: D) => void
  projectId: string
}) {
  const data = node.data as D
  const type = data.nodeType as string
  const u    = (k: string, v: unknown) => onChange(node.id, { ...data, [k]: v })

  return (
    <div className="px-4 py-3">
      {/* Label — all types */}
      <ParamField label="Label">
        <input
          className={inputCls()}
          value={data.label as string}
          onChange={e => u('label', e.target.value)}
        />
      </ParamField>

      {/* ── Feed ── */}
      {type === 'feed' && (
        <>
          <ParamField label="Flow (mol/s)">
            <input type="number" step="0.1" min="0.001" className={inputCls()}
              value={(data.flow_mol_s as number) ?? 1}
              onChange={e => u('flow_mol_s', +e.target.value)} />
          </ParamField>
          <ParamField label="Temperature (°C)">
            <input type="number" className={inputCls()}
              value={(data.temperature_C as number) ?? 25}
              onChange={e => u('temperature_C', +e.target.value)} />
          </ParamField>
          <ParamField label="Pressure (bar)">
            <input type="number" step="0.1" min="0.01" className={inputCls()}
              value={(data.pressure_bar as number) ?? 1}
              onChange={e => u('pressure_bar', +e.target.value)} />
          </ParamField>
          <FeedComponentPanel
            nodeId={node.id}
            data={data}
            onChange={onChange}
            projectId={projectId}
          />
        </>
      )}

      {/* ── Heat Exchanger ── */}
      {type === 'heat_exchanger' && (
        <>
          <ParamField label="Mode">
            <select className={selectCls()}
              value={(data.mode as string) ?? 'duty'}
              onChange={e => u('mode', e.target.value)}>
              <option value="duty">Fixed duty (W)</option>
              <option value="outlet_temp">Fixed outlet T (°C)</option>
            </select>
          </ParamField>
          {(data.mode ?? 'duty') === 'duty' ? (
            <ParamField label="Duty (W, + = heating)">
              <input type="number" className={inputCls()}
                value={(data.duty_W as number) ?? 0}
                onChange={e => u('duty_W', +e.target.value)} />
            </ParamField>
          ) : (
            <ParamField label="Outlet temperature (°C)">
              <input type="number" className={inputCls()}
                value={(data.outlet_temp_C as number) ?? 25}
                onChange={e => u('outlet_temp_C', +e.target.value)} />
            </ParamField>
          )}
        </>
      )}

      {/* ── Flash Drum ── */}
      {type === 'flash_drum' && (
        <>
          <ParamField label="Temperature (°C)">
            <input type="number" className={inputCls()}
              value={(data.temperature_C as number) ?? 80}
              onChange={e => u('temperature_C', +e.target.value)} />
          </ParamField>
          <ParamField label="Pressure (bar)">
            <input type="number" step="0.1" min="0.01" className={inputCls()}
              value={(data.pressure_bar as number) ?? 1}
              onChange={e => u('pressure_bar', +e.target.value)} />
          </ParamField>
          <ParamField label="Property package">
            <select className={selectCls()}
              value={(data.property_package as string) ?? 'ideal'}
              onChange={e => u('property_package', e.target.value)}>
              <option value="ideal">Ideal (Raoult's Law)</option>
              <option value="peng_robinson">Peng-Robinson</option>
            </select>
          </ParamField>
          {(data.property_package as string) === 'peng_robinson' && (
            <p className="text-[10px] text-amber-400 mt-1">
              Requires Tc, Pc, ω for all components.
            </p>
          )}
          <p className="text-[10px] text-slate-500 mt-1">
            Outlet 0 = vapour (top) · Outlet 1 = liquid (bottom)
          </p>
        </>
      )}

      {/* ── Splitter ── */}
      {type === 'splitter' && (
        <>
          <ParamField label="Split fraction to outlet 0">
            <input type="number" step="0.05" min="0" max="1" className={inputCls()}
              value={((data.fractions as number[])?.[0]) ?? 0.5}
              onChange={e => u('fractions', [+e.target.value, +(1 - +e.target.value).toFixed(6)])} />
          </ParamField>
          <p className="text-[10px] text-slate-500 mt-1">Outlet 1 gets the remainder.</p>
        </>
      )}

      {/* ── PFR ── */}
      {type === 'pfr' && (
        <>
          <ParamField label="Reactant component">
            <select className={selectCls()}
              value={(data.reactant as string) ?? 'benzene'}
              onChange={e => u('reactant', e.target.value)}>
              {['benzene', 'toluene', 'ethanol', 'water', 'methanol', 'acetone',
                'n_hexane', 'n_heptane', 'methane', 'propane'].map(c => <option key={c}>{c}</option>)}
            </select>
          </ParamField>
          <ParamField label="Product component">
            <select className={selectCls()}
              value={(data.product_comp as string) ?? 'toluene'}
              onChange={e => u('product_comp', e.target.value)}>
              {['benzene', 'toluene', 'ethanol', 'water', 'methanol', 'acetone',
                'n_hexane', 'n_heptane', 'methane', 'propane'].map(c => <option key={c}>{c}</option>)}
            </select>
          </ParamField>
          <ParamField label="Conversion (0–1)">
            <input type="number" step="0.05" min="0" max="1" className={inputCls()}
              value={(data.conversion as number) ?? 0.5}
              onChange={e => u('conversion', +e.target.value)} />
          </ParamField>
          <ParamField label="ΔH_rxn (J/mol, + = endothermic)">
            <input type="number" step="1000" className={inputCls()}
              value={(data.delta_Hrxn_J_mol as number) ?? 0}
              onChange={e => u('delta_Hrxn_J_mol', +e.target.value)} />
          </ParamField>
        </>
      )}

      {/* ── CSTR ── */}
      {type === 'cstr' && (
        <>
          <ParamField label="Volume (L)">
            <input type="number" step="10" min="1" className={inputCls()}
              value={(data.volume_L as number) ?? 100}
              onChange={e => u('volume_L', +e.target.value)} />
          </ParamField>
          <ParamField label="Temperature (°C)">
            <input type="number" step="1" className={inputCls()}
              value={(data.temperature_C as number) ?? 76.85}
              onChange={e => u('temperature_C', +e.target.value)} />
          </ParamField>
          <ParamField label="Coolant temp (K)">
            <input type="number" step="1" min="200" max="400" className={inputCls()}
              value={(data.coolant_temp_K as number) ?? 300}
              onChange={e => u('coolant_temp_K', +e.target.value)} />
          </ParamField>
        </>
      )}

      {/* ── Pump ── */}
      {type === 'pump' && (
        <>
          <ParamField label="ΔP (bar)">
            <input type="number" step="0.5" min="0" className={inputCls()}
              value={(data.delta_P_bar as number) ?? 1}
              onChange={e => u('delta_P_bar', +e.target.value)} />
          </ParamField>
          <ParamField label="Efficiency (0–1)">
            <input type="number" step="0.05" min="0.01" max="1" className={inputCls()}
              value={(data.efficiency as number) ?? 0.75}
              onChange={e => u('efficiency', +e.target.value)} />
          </ParamField>
        </>
      )}

      {/* Mixer / Product — no extra parameters */}
      {(type === 'mixer' || type === 'product') && (
        <p className="text-[11px] text-slate-500 italic">
          No additional parameters for this unit.
        </p>
      )}
    </div>
  )
}

// ── Inlet Conditions section ──────────────────────────────────────────────────

interface InletState {
  temperature_C: number
  pressure_bar:  number
  flow_mol_s:    number
  composition:   Record<string, number>
}

const DEFAULT_INLET: InletState = {
  temperature_C: 25,
  pressure_bar:  1,
  flow_mol_s:    1,
  composition:   {},
}

function InletConditions({
  node,
  edges,
  streams,
  onChange,
}: {
  node:     Node
  edges:    Edge[]
  streams:  Record<string, StreamState>
  onChange: (id: string, data: D) => void
}) {
  const data       = node.data as D
  const type       = data.nodeType as string
  const inletCount = UNIT_INLETS[type] ?? 0
  const hids       = inletHandleIds(inletCount)

  if (hids.length === 0) return null

  const inletData = (data.inlet_data as Record<string, InletState>) ?? {}

  const nodeEdges = edges.filter(e => e.target === node.id)

  // Find the edge connected to a given handle id (exact or fallback for single inlet)
  const edgeForHandle = (hid: string): Edge | undefined => {
    const exact = nodeEdges.find(e => e.targetHandle === hid)
    if (exact) return exact
    // fallback: single-inlet node, any edge is the inlet
    if (hids.length === 1 && nodeEdges.length === 1) return nodeEdges[0]
    return undefined
  }

  const updateInlet = (hid: string, patch: Partial<InletState>) => {
    const current = inletData[hid] ?? { ...DEFAULT_INLET }
    onChange(node.id, {
      ...data,
      inlet_data: { ...inletData, [hid]: { ...current, ...patch } },
    })
  }

  const updateInletComp = (hid: string, comp: string, val: number) => {
    const current = inletData[hid] ?? { ...DEFAULT_INLET }
    updateInlet(hid, { composition: { ...current.composition, [comp]: val } })
  }

  return (
    <div className="border-t border-slate-700 px-4 py-3">
      <p className="text-[10px] font-bold uppercase tracking-widest text-teal-400 mb-3">
        Inlet Conditions
      </p>

      {hids.map((hid, idx) => {
        const connEdge   = edgeForHandle(hid)
        const streamData = connEdge ? streams[connEdge.id] : undefined
        const edit       = inletData[hid] ?? { ...DEFAULT_INLET }

        const title = hids.length === 1
          ? 'Inlet'
          : `Inlet ${idx + 1}`

        return (
          <div key={hid} className="mb-4">
            {/* Handle label + connectivity badge */}
            <div className="flex items-center gap-2 mb-2">
              <p className="text-[12px] font-semibold text-slate-200">{title}</p>
              <span className={[
                'text-[9px] font-bold uppercase tracking-wide px-1.5 py-0.5 rounded',
                connEdge
                  ? 'bg-teal-900 text-teal-300'
                  : 'bg-slate-700 text-slate-400',
              ].join(' ')}>
                {connEdge ? 'connected' : 'no source'}
              </span>
            </div>

            {/* ── Connected + result available → read-only ── */}
            {connEdge && streamData && (
              <dl className="grid grid-cols-2 gap-x-3 gap-y-1">
                <dt className="text-[11px] text-slate-400">T</dt>
                <dd className="text-[11px] text-slate-100 font-mono text-right">
                  {streamData.temperature.toFixed(2)} °C
                </dd>
                <dt className="text-[11px] text-slate-400">P</dt>
                <dd className="text-[11px] text-slate-100 font-mono text-right">
                  {streamData.pressure.toFixed(3)} bar
                </dd>
                <dt className="text-[11px] text-slate-400">Flow</dt>
                <dd className="text-[11px] text-slate-100 font-mono text-right">
                  {streamData.flow.toFixed(4)} mol/s
                </dd>
                {streamData.vapor_fraction != null && (
                  <>
                    <dt className="text-[11px] text-slate-400">Vapour ψ</dt>
                    <dd className="text-[11px] text-slate-100 font-mono text-right">
                      {streamData.vapor_fraction.toFixed(4)}
                    </dd>
                  </>
                )}
                {Object.entries(streamData.composition)
                  .sort(([, a], [, b]) => b - a)
                  .map(([comp, frac]) => (
                    <div key={comp} className="contents">
                      <dt className="text-[11px] text-slate-400 capitalize">{comp}</dt>
                      <dd className="text-[11px] text-slate-100 font-mono text-right">
                        {frac.toFixed(4)}
                      </dd>
                    </div>
                  ))}
              </dl>
            )}

            {/* ── Connected but no result yet ── */}
            {connEdge && !streamData && (
              <p className="text-[11px] text-slate-500 italic">
                Run simulation to see stream conditions.
              </p>
            )}

            {/* ── No upstream source → editable fields ── */}
            {!connEdge && (
              <>
                <ParamField label="Temperature (°C)">
                  <input type="number" className={inputCls()}
                    value={edit.temperature_C}
                    onChange={e => updateInlet(hid, { temperature_C: +e.target.value })} />
                </ParamField>
                <ParamField label="Pressure (bar)">
                  <input type="number" step="0.1" min="0.01" className={inputCls()}
                    value={edit.pressure_bar}
                    onChange={e => updateInlet(hid, { pressure_bar: +e.target.value })} />
                </ParamField>
                <ParamField label="Flow (mol/s)">
                  <input type="number" step="0.1" min="0.001" className={inputCls()}
                    value={edit.flow_mol_s}
                    onChange={e => updateInlet(hid, { flow_mol_s: +e.target.value })} />
                </ParamField>
                <p className="text-[10px] text-slate-400 mb-1 font-medium">
                  Composition (mole fractions)
                </p>
                {Object.keys(edit.composition).length === 0 && (
                  <p className="text-[11px] text-slate-500 italic mb-1">
                    No components — connect a Feed node to set composition.
                  </p>
                )}
                {Object.keys(edit.composition).map(c => (
                  <ParamField key={c} label={c}>
                    <input type="number" step="0.01" min="0" max="1" className={inputCls()}
                      value={edit.composition[c] ?? 0}
                      onChange={e => updateInletComp(hid, c, +e.target.value)} />
                  </ParamField>
                ))}
                <p className="text-[10px] text-slate-500">
                  Stored on node · connect a Feed to override.
                </p>
              </>
            )}
          </div>
        )
      })}
    </div>
  )
}

// ── Right-side configuration panel ───────────────────────────────────────────

function ConfigPanel({
  node,
  edges,
  streams,
  onChange,
  onClose,
  onDelete,
  onOpenControlStudio,
  projectId,
}: {
  node:     Node
  edges:    Edge[]
  streams:  Record<string, StreamState>
  onChange: (id: string, data: D) => void
  onClose:  () => void
  onDelete: (id: string) => void
  onOpenControlStudio?: () => void
  projectId: string
}) {
  const data = node.data as D
  const type = data.nodeType as string

  return (
    <aside
      className="flex-shrink-0 bg-slate-900 border-l border-slate-700 flex flex-col overflow-y-auto"
      style={{ width: 284 }}
    >
      {/* Header */}
      <div className="flex items-start justify-between px-4 py-3 border-b border-slate-700 flex-shrink-0">
        <div className="min-w-0 flex-1">
          <p className="text-[13px] font-semibold text-slate-100 leading-tight truncate">
            {data.label as string}
          </p>
          <p className="text-[11px] text-slate-400 capitalize leading-tight mt-0.5">
            {type.replace(/_/g, ' ')}
          </p>
        </div>
        <div className="ml-3 flex-shrink-0 flex items-center gap-1">
          <button
            onClick={() => onDelete(node.id)}
            title="Delete operation"
            className="w-6 h-6 flex items-center justify-center rounded text-slate-500 hover:text-red-400 hover:bg-red-950 transition-colors text-sm leading-none"
          >
            🗑
          </button>
          <button
            onClick={onClose}
            className="w-6 h-6 flex items-center justify-center rounded text-slate-400 hover:text-slate-100 hover:bg-slate-700 transition-colors text-lg leading-none"
          >
            ×
          </button>
        </div>
      </div>

      {/* Control Studio button — CSTR only, when callback is provided */}
      {type === 'cstr' && onOpenControlStudio && (
        <div className="px-4 py-2.5 border-b border-slate-700">
          <button
            onClick={onOpenControlStudio}
            className="w-full py-1.5 rounded text-[11px] font-semibold
                       bg-cyan-700 hover:bg-cyan-600 text-white transition-colors"
          >
            Open Control Studio
          </button>
        </div>
      )}

      {/* Unit params section */}
      <div className="border-b border-slate-700">
        <p className="text-[10px] font-bold uppercase tracking-widest text-teal-400 px-4 pt-3 pb-1">
          Parameters
        </p>
        <UnitParams node={node} onChange={onChange} projectId={projectId} />
      </div>

      {/* Inlet conditions section */}
      <InletConditions
        node={node}
        edges={edges}
        streams={streams}
        onChange={onChange}
      />
    </aside>
  )
}

// ── Results pane components ───────────────────────────────────────────────────


// ── Inner canvas (must be child of ReactFlowProvider to use useReactFlow) ─────

function Canvas({ simId }: { simId: string }) {
  const navigate = useNavigate()
  const { screenToFlowPosition } = useReactFlow()

  const [simName, setSimName]            = useState('')
  const [projectId, setProjectId]        = useState('')
  const [nodes, setNodes, onNodesChange] = useNodesState<Node>([])
  const [edges, setEdges, onEdgesChange] = useEdgesState<Edge>([])
  const [sel, setSel]                    = useState<Node | null>(null)
  const [result, setResult]              = useState<SimulationResult | null>(null)
  const [running, setRunning]            = useState(false)
  const [runError, setRunError]          = useState<string | null>(null)
  const [warnings, setWarnings]          = useState<string[]>([])
  const [saveStatus, setSaveStatus]      = useState<'saved' | 'saving' | 'unsaved'>('saved')
  const [resultsHeight, setResultsHeight] = useState(0)   // 0 = hidden
  const [controlStudioNodeId, setControlStudioNodeId] = useState<string | null>(null)

  // Stable refs for auto-save (avoids stale closures)
  const nodesRef = useRef(nodes)
  const edgesRef = useRef(edges)
  nodesRef.current = nodes
  edgesRef.current = edges

  const isInitialized   = useRef(false)
  const saveTimer       = useRef<ReturnType<typeof setTimeout>>()
  const isDragging      = useRef(false)
  const dragStartY      = useRef(0)
  const dragStartH      = useRef(0)

  // ── Load existing simulation ──────────────────────────────────────────────

  useEffect(() => {
    fetchSimulation(simId)
      .then(sim => {
        setSimName(sim.name)
        if (sim.flowsheet?.nodes.length) {
          setNodes(sim.flowsheet.nodes.map(n => ({
            id: n.id,
            type: 'unit',
            position: n.position,
            data: { ...n.data, label: n.label, nodeType: n.type },
          })))
          setEdges(sim.flowsheet.edges.map(e => ({
            id: e.id,
            type: 'stream',
            source: e.source,
            target: e.target,
            sourceHandle: e.source_handle ?? '0',
            label: e.label,
            data: {},
          })))
        }
        setProjectId(sim.project_id)
        if (sim.result) {
          setResult(sim.result)
          setWarnings(sim.result.warnings ?? [])
        }
        setTimeout(() => { isInitialized.current = true }, 100)
      })
      .catch(() => navigate('/'))
  }, [simId, navigate, setNodes, setEdges])

  // ── Auto-save (debounced 1 s) ─────────────────────────────────────────────

  const doSave = useCallback(async () => {
    const currentNodes = nodesRef.current
    const currentEdges = edgesRef.current

    const fsNodes: FlowsheetNode[] = currentNodes.map(n => {
      const d = n.data as D
      const extra: D = {}
      if (d.nodeType === 'pfr' && d.reactant && d.product_comp) {
        extra.stoichiometry = { [d.reactant as string]: -1, [d.product_comp as string]: 1 }
      }
      return {
        id: n.id,
        type: d.nodeType as string,
        label: d.label as string,
        data: { ...d, ...extra },
        position: n.position,
      }
    })

    const fsEdges: FlowsheetEdge[] = currentEdges.map(e => ({
      id: e.id,
      source: e.source,
      target: e.target,
      label: e.label as string | undefined,
      source_handle: e.sourceHandle ?? '0',
    }))

    await saveFlowsheet(simId, fsNodes, fsEdges)
  }, [simId])

  useEffect(() => {
    if (!isInitialized.current) return
    setSaveStatus('unsaved')
    clearTimeout(saveTimer.current)
    saveTimer.current = setTimeout(async () => {
      setSaveStatus('saving')
      try {
        await doSave()
        setSaveStatus('saved')
      } catch {
        setSaveStatus('unsaved')
      }
    }, 1000)
    return () => clearTimeout(saveTimer.current)
  }, [nodes, edges, doSave])

  // ── Draggable splitter ────────────────────────────────────────────────────

  useEffect(() => {
    const onMove = (e: MouseEvent) => {
      if (!isDragging.current) return
      const delta  = dragStartY.current - e.clientY   // up = taller panel
      const newH   = Math.max(80, Math.min(680, dragStartH.current + delta))
      setResultsHeight(newH)
    }
    const onUp = () => { isDragging.current = false }
    window.addEventListener('mousemove', onMove)
    window.addEventListener('mouseup',   onUp)
    return () => {
      window.removeEventListener('mousemove', onMove)
      window.removeEventListener('mouseup',   onUp)
    }
  }, [])

  const onSplitterMouseDown = useCallback((e: React.MouseEvent) => {
    isDragging.current  = true
    dragStartY.current  = e.clientY
    dragStartH.current  = resultsHeight
    e.preventDefault()
  }, [resultsHeight])

  // ── Edge connections ──────────────────────────────────────────────────────

  const onConnect = useCallback(
    (params: Connection) =>
      setEdges(eds => addEdge({ ...params, type: 'stream', data: {} }, eds)),
    [setEdges]
  )

  // ── Node data update (from ConfigPanel) ───────────────────────────────────

  const updNode = (id: string, data: D) => {
    setNodes(nds => nds.map(n => n.id === id ? { ...n, data } : n))
    setSel(prev => prev?.id === id ? { ...prev, data } : prev)
  }

  // ── Node deletion ─────────────────────────────────────────────────────────

  const deleteNode = useCallback((id: string) => {
    setNodes(nds => nds.filter(n => n.id !== id))
    setEdges(eds => eds.filter(e => e.source !== id && e.target !== id))
    setSel(null)
  }, [setNodes, setEdges])

  // ── Drag-and-drop ─────────────────────────────────────────────────────────

  const onDragOver = useCallback((e: React.DragEvent) => {
    e.preventDefault()
    e.dataTransfer.dropEffect = 'move'
  }, [])

  const onDrop = useCallback(
    (e: React.DragEvent) => {
      e.preventDefault()
      const nodeType  = e.dataTransfer.getData('nodeType')
      const nodeLabel = e.dataTransfer.getData('nodeLabel')
      if (!nodeType) return

      const position = screenToFlowPosition({ x: e.clientX, y: e.clientY })
      const newNode: Node = {
        id: nextId(),
        type: 'unit',
        position,
        data: {
          nodeType,
          label: nodeLabel,
          composition: {},
          inlet_data: {},
          ...(nodeType === 'feed'           && { flow_mol_s: 1, temperature_C: 25, pressure_bar: 1 }),
          ...(nodeType === 'flash_drum'     && { temperature_C: 80, pressure_bar: 1, property_package: 'ideal' }),
          ...(nodeType === 'heat_exchanger' && { mode: 'duty', duty_W: 0 }),
          ...(nodeType === 'splitter'       && { fractions: [0.5, 0.5] }),
          ...(nodeType === 'pfr'            && { reactant: 'benzene', product_comp: 'toluene', conversion: 0.5, delta_Hrxn_J_mol: 0 }),
          ...(nodeType === 'cstr'           && { volume_L: 100, temperature_C: 76.85, coolant_temp_K: 300 }),
          ...(nodeType === 'pump'           && { delta_P_bar: 1, efficiency: 0.75 }),
        },
      }
      setNodes(nds => [...nds, newNode])
    },
    [screenToFlowPosition, setNodes]
  )

  // ── Run simulation ────────────────────────────────────────────────────────

  const handleRun = async () => {
    setRunning(true)
    setRunError(null)
    setWarnings([])
    try {
      await doSave()
      setSaveStatus('saved')
      const res = await runSimulation(simId)
      setResult(res)
      setWarnings(res.warnings ?? [])
      // Auto-open results panel on first run; preserve user-set height thereafter
      setResultsHeight(h => h > 0 ? h : 320)
      // Inject stream data into edges for hover tooltips
      setEdges(eds =>
        eds.map(e => ({
          ...e,
          data: {
            ...(e.data as D ?? {}),
            stream: (res.streams as Record<string, StreamState>)[e.id],
          },
        }))
      )
    } catch (err: unknown) {
      const e = err as { response?: { data?: { detail?: { message?: string } | string } } }
      const d = e.response?.data?.detail
      setRunError((typeof d === 'object' ? d?.message : d) ?? 'Simulation failed')
    } finally {
      setRunning(false)
    }
  }

  // ── Helpers ───────────────────────────────────────────────────────────────

  const saveIndicator =
    saveStatus === 'saving'  ? '● Saving…' :
    saveStatus === 'unsaved' ? '○ Unsaved' :
                               '✓ Saved'

  const saveIndicatorCls =
    saveStatus === 'saving'  ? 'text-amber-500' :
    saveStatus === 'unsaved' ? 'text-slate-400'  :
                               'text-teal-500'

  const streams = (result?.streams ?? {}) as Record<string, StreamState>

  const cstrSsPoint: MPCNodeSummary | null = controlStudioNodeId
    ? ((result?.node_summaries?.[controlStudioNodeId] as MPCNodeSummary) ?? null)
    : null

  // ── Render ────────────────────────────────────────────────────────────────

  return (
    <div style={{ position: 'fixed', inset: '56px 0 0 0' }} className="flex flex-col overflow-hidden">

      {/* ── Toolbar ── */}
      <div className="flex items-center gap-3 px-4 py-2 bg-white border-b border-slate-200 flex-shrink-0 z-10">
        <button
          onClick={() => navigate('/')}
          className="flex items-center gap-1.5 text-sm text-slate-500 hover:text-slate-800 transition-colors"
        >
          ← Projects
        </button>

        <div className="w-px h-5 bg-slate-200 mx-1" />

        <h1 className="text-sm font-semibold text-slate-800 truncate max-w-xs">{simName}</h1>

        {/* Results toggle — only shown once a result exists */}
        {result && (
          <button
            onClick={() => setResultsHeight(h => h > 0 ? 0 : 320)}
            className={[
              'ml-2 px-3 py-1 rounded-lg text-xs font-medium transition-colors border',
              resultsHeight > 0
                ? 'bg-teal-500 text-white border-teal-500'
                : 'bg-white text-slate-500 border-slate-200 hover:bg-slate-50',
            ].join(' ')}
          >
            {resultsHeight > 0 ? '▼ Results' : '▲ Results ✓'}
          </button>
        )}

        <div className="flex-1" />

        <span className={`text-[11px] font-medium ${saveIndicatorCls}`}>
          {saveIndicator}
        </span>

        <button
          onClick={handleRun}
          disabled={running}
          className={[
            'flex items-center gap-2 px-4 py-1.5 rounded-lg text-sm font-semibold',
            'transition-all duration-150 shadow-sm',
            running
              ? 'bg-teal-400 text-white cursor-not-allowed opacity-80'
              : 'bg-teal-500 hover:bg-teal-600 text-white',
          ].join(' ')}
        >
          {running ? (
            <>
              <span className="inline-block w-3.5 h-3.5 border-2 border-white/30 border-t-white rounded-full animate-spin" />
              Running…
            </>
          ) : (
            <><span>▶</span> Run Simulation</>
          )}
        </button>
      </div>

      {/* ── Error banner ── */}
      {runError && (
        <div className="px-4 py-2 bg-red-50 border-b border-red-200 text-sm text-red-700 flex-shrink-0">
          {runError}
        </div>
      )}

      {/* ── Body: palette | (canvas + results) | config ── */}
      <div className="flex flex-1 overflow-hidden">

        {/* ── Left sidebar — unit op palette only ── */}
        <aside className="w-48 flex-shrink-0 bg-slate-900 overflow-y-auto">
          <div className="px-3 pt-4 pb-4">
            <p className="text-[10px] font-bold uppercase tracking-widest text-teal-400 mb-3">
              Unit Operations
            </p>
            <div className="flex flex-col gap-1.5">
              {PALETTE_ITEMS.map(item => (
                <div
                  key={item.type}
                  draggable
                  onDragStart={e => {
                    e.dataTransfer.setData('nodeType', item.type)
                    e.dataTransfer.setData('nodeLabel', item.label)
                    e.dataTransfer.effectAllowed = 'move'
                  }}
                  className={[
                    'flex items-center gap-2.5 rounded-lg px-2.5 py-2',
                    'border border-slate-700 bg-slate-800',
                    'cursor-grab active:cursor-grabbing select-none',
                    'hover:bg-slate-700 hover:border-slate-600 transition-colors duration-100',
                  ].join(' ')}
                >
                  <div className={`${item.bar} w-1 self-stretch rounded-full flex-shrink-0`} />
                  <div className="min-w-0">
                    <p className="text-[12px] font-medium text-slate-200 leading-tight">
                      {item.label}
                    </p>
                    <p className="text-[10px] text-slate-500 leading-tight truncate">
                      {item.desc}
                    </p>
                  </div>
                </div>
              ))}
            </div>
            <p className="text-[10px] text-slate-600 mt-4 text-center leading-tight">
              Drag onto canvas<br/>Click node to configure
            </p>
          </div>
        </aside>

        {/* ── Centre column: canvas (flex-1) + splitter + results panel ── */}
        <div className="flex-1 flex flex-col overflow-hidden">

          {/* Canvas — fills remaining height above results panel */}
          <div className="flex-1 overflow-hidden flex">
            <div className="flex-1 overflow-hidden bg-slate-50">
              <ReactFlow
                nodes={nodes}
                edges={edges}
                onNodesChange={onNodesChange}
                onEdgesChange={onEdgesChange}
                onConnect={onConnect}
                nodeTypes={NODE_TYPES}
                edgeTypes={EDGE_TYPES}
                onNodeClick={(_, node) => setSel(node)}
                onPaneClick={() => setSel(null)}
                onDrop={onDrop}
                onDragOver={onDragOver}
                deleteKeyCode="Delete"
                fitView
                defaultEdgeOptions={{ type: 'stream', data: {} }}
              >
                <Background variant={BackgroundVariant.Dots} gap={20} size={1} color="#cbd5e1" />
                <Controls />
                <MiniMap
                  nodeColor={n => {
                    const colourMap: Record<string, string> = {
                      feed: '#14b8a6', product: '#10b981', mixer: '#8b5cf6',
                      splitter: '#f59e0b', heat_exchanger: '#ef4444',
                      flash_drum: '#0ea5e9', pfr: '#84cc16', pump: '#f97316',
                      cstr: '#06b6d4',
                    }
                    return colourMap[(n.data as D).nodeType as string] ?? '#94a3b8'
                  }}
                  style={{ background: '#1e293b', border: '1px solid #334155' }}
                />
              </ReactFlow>
            </div>

            {/* Control Studio — full-height panel, replaces config panel when open */}
            {controlStudioNodeId && (
              <ControlStudio
                simId={simId}
                nodeId={controlStudioNodeId}
                nodeLabel={
                  (nodes.find(n => n.id === controlStudioNodeId)?.data as D | undefined)
                    ?.label as string ?? controlStudioNodeId
                }
                ssOperatingPoint={cstrSsPoint}
                onClose={() => setControlStudioNodeId(null)}
              />
            )}

            {/* Right config panel — inside the canvas row so it stays canvas-height */}
            {sel && !controlStudioNodeId && (
              <ConfigPanel
                node={sel}
                edges={edges}
                streams={streams}
                onChange={updNode}
                onClose={() => setSel(null)}
                onDelete={deleteNode}
                projectId={projectId}
                onOpenControlStudio={
                  (sel.data as D).nodeType === 'cstr'
                    ? () => { setControlStudioNodeId(sel.id); setSel(null) }
                    : undefined
                }
              />
            )}
          </div>

          {/* ── Draggable splitter (only when results exist) ── */}
          {result && (
            <div
              onMouseDown={onSplitterMouseDown}
              className={[
                'flex-shrink-0 flex items-center justify-center',
                'h-3 bg-slate-100 border-y border-slate-200',
                'cursor-row-resize select-none hover:bg-teal-50 transition-colors',
                'group',
              ].join(' ')}
              title="Drag to resize results panel"
            >
              {/* Grip dots */}
              <div className="flex gap-1">
                {[0, 1, 2].map(i => (
                  <div
                    key={i}
                    className="w-1 h-1 rounded-full bg-slate-300 group-hover:bg-teal-400 transition-colors"
                  />
                ))}
              </div>
            </div>
          )}

          {/* ── Results panel ── */}
          {result && resultsHeight > 0 && (
            <ResultsPanel
              result={result}
              nodes={nodes}
              edges={edges}
              warnings={warnings}
              height={resultsHeight}
              onRunPinch={runPinchAnalysis}
            />
          )}
        </div>

      </div>
    </div>
  )
}

// ── Public page component ─────────────────────────────────────────────────────

export default function FlowsheetPage() {
  const { simId } = useParams<{ simId: string }>()
  const navigate  = useNavigate()

  if (!simId) {
    navigate('/')
    return null
  }

  return (
    <ReactFlowProvider>
      <Canvas simId={simId} />
    </ReactFlowProvider>
  )
}
