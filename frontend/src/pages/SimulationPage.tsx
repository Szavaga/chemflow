import { useState } from 'react'
import { runCSTR, runFlash, runHEX } from '../api/client'
import ResultsChart from '../components/ResultsChart'
import type { CSTRResult, FlashResult, HEXResult, UnitType } from '../types'

// ── Default parameter sets ──────────────────────────────────────────────────

const FLASH_DEFAULTS = {
  components: ['benzene', 'toluene'] as string[],
  feed_flow: 100,
  feed_composition: [0.5, 0.5] as number[],
  temperature: 80,
  pressure: 1.0,
}

const CSTR_DEFAULTS = {
  reactant_name: 'A',
  feed_concentration: 2.0,
  feed_flow: 1.0,
  volume: 10.0,
  temperature: 60,
  pre_exponential: 1e6,
  activation_energy: 50000,
  reaction_order: 1.0,
}

const HEX_DEFAULTS = {
  hot_inlet_temp: 150,
  hot_outlet_temp: 90,
  hot_flow: 2.0,
  hot_Cp: 4200,
  cold_inlet_temp: 25,
  cold_flow: 3.0,
  cold_Cp: 4200,
  flow_arrangement: 'counterflow',
}

// ── Sub-forms ───────────────────────────────────────────────────────────────

type FlashFormProps = { form: typeof FLASH_DEFAULTS; setForm: (f: typeof FLASH_DEFAULTS) => void }

function FlashForm({ form, setForm }: FlashFormProps) {
  const upd = (key: keyof typeof FLASH_DEFAULTS, val: unknown) =>
    setForm({ ...form, [key]: val })
  return (
    <div className="form-section">
      <h3>Flash Drum — Isothermal VLE</h3>
      <label>Temperature (°C)
        <input type="number" value={form.temperature}
          onChange={e => upd('temperature', +e.target.value)} />
      </label>
      <label>Pressure (bar)
        <input type="number" step="0.1" min="0.01" value={form.pressure}
          onChange={e => upd('pressure', +e.target.value)} />
      </label>
      <label>Total Feed Flow (mol/s)
        <input type="number" min="0.001" value={form.feed_flow}
          onChange={e => upd('feed_flow', +e.target.value)} />
      </label>
      <p className="hint">
        Fixed binary system: <strong>benzene / toluene</strong>, 50/50 mol%.
        Raoult's law K-values via Antoine vapour pressures.
      </p>
    </div>
  )
}

type CSTRFormProps = { form: typeof CSTR_DEFAULTS; setForm: (f: typeof CSTR_DEFAULTS) => void }

function CSTRForm({ form, setForm }: CSTRFormProps) {
  const upd = (key: keyof typeof CSTR_DEFAULTS, val: unknown) =>
    setForm({ ...form, [key]: val })
  return (
    <div className="form-section">
      <h3>CSTR — Arrhenius Kinetics (A → B)</h3>
      <label>Feed Concentration Ca0 (mol/L)
        <input type="number" step="0.1" min="0.001" value={form.feed_concentration}
          onChange={e => upd('feed_concentration', +e.target.value)} />
      </label>
      <label>Volumetric Feed Flow (L/s)
        <input type="number" step="0.1" min="0.001" value={form.feed_flow}
          onChange={e => upd('feed_flow', +e.target.value)} />
      </label>
      <label>Reactor Volume (L)
        <input type="number" step="1" min="0.1" value={form.volume}
          onChange={e => upd('volume', +e.target.value)} />
      </label>
      <label>Temperature (°C)
        <input type="number" value={form.temperature}
          onChange={e => upd('temperature', +e.target.value)} />
      </label>
      <label>Pre-exponential Factor k₀ (1/s)
        <input type="number" value={form.pre_exponential}
          onChange={e => upd('pre_exponential', +e.target.value)} />
      </label>
      <label>Activation Energy Ea (J/mol)
        <input type="number" step="1000" value={form.activation_energy}
          onChange={e => upd('activation_energy', +e.target.value)} />
      </label>
      <label>Reaction Order n
        <input type="number" step="0.5" min="0" value={form.reaction_order}
          onChange={e => upd('reaction_order', +e.target.value)} />
      </label>
    </div>
  )
}

type HEXFormProps = { form: typeof HEX_DEFAULTS; setForm: (f: typeof HEX_DEFAULTS) => void }

function HEXForm({ form, setForm }: HEXFormProps) {
  const upd = (key: keyof typeof HEX_DEFAULTS, val: unknown) =>
    setForm({ ...form, [key]: val })
  return (
    <div className="form-section">
      <h3>Heat Exchanger — LMTD Method</h3>
      <label>Hot Inlet Temp (°C)
        <input type="number" value={form.hot_inlet_temp}
          onChange={e => upd('hot_inlet_temp', +e.target.value)} />
      </label>
      <label>Hot Outlet Temp (°C)
        <input type="number" value={form.hot_outlet_temp}
          onChange={e => upd('hot_outlet_temp', +e.target.value)} />
      </label>
      <label>Hot Stream Flow (kg/s)
        <input type="number" step="0.1" min="0.001" value={form.hot_flow}
          onChange={e => upd('hot_flow', +e.target.value)} />
      </label>
      <label>Hot Stream Cp (J/kg·K)
        <input type="number" step="100" value={form.hot_Cp}
          onChange={e => upd('hot_Cp', +e.target.value)} />
      </label>
      <label>Cold Inlet Temp (°C)
        <input type="number" value={form.cold_inlet_temp}
          onChange={e => upd('cold_inlet_temp', +e.target.value)} />
      </label>
      <label>Cold Stream Flow (kg/s)
        <input type="number" step="0.1" min="0.001" value={form.cold_flow}
          onChange={e => upd('cold_flow', +e.target.value)} />
      </label>
      <label>Cold Stream Cp (J/kg·K)
        <input type="number" step="100" value={form.cold_Cp}
          onChange={e => upd('cold_Cp', +e.target.value)} />
      </label>
      <label>Flow Arrangement
        <select value={form.flow_arrangement}
          onChange={e => upd('flow_arrangement', e.target.value)}>
          <option value="counterflow">Counterflow</option>
          <option value="parallel">Parallel flow</option>
        </select>
      </label>
    </div>
  )
}

// ── Main page ────────────────────────────────────────────────────────────────

const UNIT_LABELS: Record<UnitType, string> = {
  flash_drum: 'Flash Drum',
  cstr: 'CSTR',
  heat_exchanger: 'Heat Exchanger',
}

export default function SimulationPage() {
  const [unitType, setUnitType] = useState<UnitType>('flash_drum')
  const [flashForm, setFlashForm] = useState(FLASH_DEFAULTS)
  const [cstrForm, setCstrForm] = useState(CSTR_DEFAULTS)
  const [hexForm, setHexForm] = useState(HEX_DEFAULTS)

  const [result, setResult] = useState<FlashResult | CSTRResult | HEXResult | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const handleRun = async () => {
    setLoading(true)
    setError(null)
    try {
      let res: FlashResult | CSTRResult | HEXResult
      if (unitType === 'flash_drum') res = await runFlash(flashForm)
      else if (unitType === 'cstr') res = await runCSTR(cstrForm)
      else res = await runHEX(hexForm)
      setResult(res)
    } catch (e: unknown) {
      const err = e as { response?: { data?: { detail?: string } }; message?: string }
      setError(err.response?.data?.detail ?? err.message ?? 'Simulation failed')
    } finally {
      setLoading(false)
    }
  }

  const switchUnit = (t: UnitType) => {
    setUnitType(t)
    setResult(null)
    setError(null)
  }

  return (
    <div className="page">
      <div className="page-header">
        <h1>Process Simulator</h1>
      </div>

      <div className="sim-layout">
        {/* Left panel — inputs */}
        <div className="sim-panel card">
          <div className="unit-tabs">
            {(Object.keys(UNIT_LABELS) as UnitType[]).map(t => (
              <button
                key={t}
                className={`tab${unitType === t ? ' active' : ''}`}
                onClick={() => switchUnit(t)}
              >
                {UNIT_LABELS[t]}
              </button>
            ))}
          </div>

          {unitType === 'flash_drum' && <FlashForm form={flashForm} setForm={setFlashForm} />}
          {unitType === 'cstr' && <CSTRForm form={cstrForm} setForm={setCstrForm} />}
          {unitType === 'heat_exchanger' && <HEXForm form={hexForm} setForm={setHexForm} />}

          <button
            className="btn btn-primary btn-run"
            onClick={handleRun}
            disabled={loading}
          >
            {loading ? 'Solving…' : 'Run Simulation'}
          </button>

          {error && <div className="error-banner">{error}</div>}
        </div>

        {/* Right panel — results */}
        <div className="results-panel">
          {result ? (
            <ResultsChart
              unitType={unitType}
              result={result}
              componentNames={unitType === 'flash_drum' ? flashForm.components : []}
            />
          ) : (
            <div className="card empty-state">
              <p>
                Configure the unit operation on the left and click{' '}
                <strong>Run Simulation</strong> to see results here.
              </p>
            </div>
          )}
        </div>
      </div>
    </div>
  )
}
