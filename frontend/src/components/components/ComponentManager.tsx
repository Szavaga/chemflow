/**
 * ComponentManager — modal for searching and adding chemical components.
 *
 * Opened from the Feed node config panel via the "Browse Components" button.
 * Displays a debounced search over GET /api/components and lets the user
 * pick components to add to the active feed stream.
 * Also exposes a "Create custom" form for project-scoped components.
 */

import { useCallback, useEffect, useRef, useState } from 'react'
import {
  createComponent,
  fetchComponents,
  validateAntoine,
} from '../../api/client'
import type { ChemicalComponent, ComponentCreate } from '../../types'

// ── Small helpers ─────────────────────────────────────────────────────────────

function Pill({ label, value }: { label: string; value: string | number | null | undefined }) {
  if (value == null) return null
  return (
    <span className="inline-flex items-center gap-1 text-[10px] bg-slate-700 rounded px-1.5 py-0.5">
      <span className="text-slate-400">{label}</span>
      <span className="text-slate-200 font-mono">{typeof value === 'number' ? value.toFixed(2) : value}</span>
    </span>
  )
}

function MissingBadge({ comp }: { comp: ChemicalComponent }) {
  const missing = [
    !comp.tc    && 'Tc',
    !comp.pc    && 'Pc',
    !comp.omega && 'ω',
  ].filter(Boolean)
  if (missing.length === 0) return null
  return (
    <span className="text-[9px] font-bold bg-amber-800 text-amber-200 px-1.5 py-0.5 rounded ml-1">
      missing {missing.join(', ')}
    </span>
  )
}

// ── Create-custom form ────────────────────────────────────────────────────────

interface CreateFormProps {
  projectId: string
  onCreated: (comp: ChemicalComponent) => void
  onCancel: () => void
}

function CreateForm({ projectId, onCreated, onCancel }: CreateFormProps) {
  const [form, setForm] = useState<Partial<ComponentCreate>>({
    project_id: projectId,
    antoine_units: 'mmHg',
  })
  const [antoineFull, setAntoineFull] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [saving, setSaving] = useState(false)

  const u = (k: keyof ComponentCreate, v: unknown) =>
    setForm(prev => ({ ...prev, [k]: v }))

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault()
    setError(null)
    setSaving(true)
    try {
      const created = await createComponent(form as ComponentCreate)
      onCreated(created)
    } catch (err: unknown) {
      const msg = (err as { response?: { data?: { detail?: string } } })
        ?.response?.data?.detail ?? 'Create failed'
      setError(typeof msg === 'string' ? msg : JSON.stringify(msg))
    } finally {
      setSaving(false)
    }
  }

  const inp = 'w-full rounded px-2 py-1 text-[12px] bg-slate-800 border border-slate-600 text-slate-100 focus:outline-none focus:border-teal-500'
  const lbl = 'block text-[11px] text-slate-400 mb-0.5'

  return (
    <form onSubmit={handleSubmit} className="space-y-3 p-4 bg-slate-900 rounded-lg border border-slate-700">
      <p className="text-[12px] font-semibold text-slate-100 mb-2">Create custom component</p>

      <div className="grid grid-cols-2 gap-3">
        <div>
          <label className={lbl}>Name *</label>
          <input required className={inp} placeholder="e.g. My Compound"
            value={form.name ?? ''}
            onChange={e => u('name', e.target.value)} />
        </div>
        <div>
          <label className={lbl}>CAS number *</label>
          <input required className={inp} placeholder="e.g. 123-45-6"
            value={form.cas_number ?? ''}
            onChange={e => u('cas_number', e.target.value)} />
        </div>
        <div>
          <label className={lbl}>Formula</label>
          <input className={inp} placeholder="e.g. C2H6O"
            value={form.formula ?? ''}
            onChange={e => u('formula', e.target.value)} />
        </div>
        <div>
          <label className={lbl}>MW (g/mol) *</label>
          <input required type="number" step="0.01" min="1" className={inp}
            value={form.mw ?? ''}
            onChange={e => u('mw', parseFloat(e.target.value))} />
        </div>
        <div>
          <label className={lbl}>Tc (K) *</label>
          <input required type="number" step="1" className={inp}
            value={form.tc ?? ''}
            onChange={e => u('tc', parseFloat(e.target.value))} />
        </div>
        <div>
          <label className={lbl}>Pc (Pa) *</label>
          <input required type="number" step="1000" className={inp}
            value={form.pc ?? ''}
            onChange={e => u('pc', parseFloat(e.target.value))} />
        </div>
        <div>
          <label className={lbl}>ω (acentric factor) *</label>
          <input required type="number" step="0.001" min="0" max="2" className={inp}
            value={form.omega ?? ''}
            onChange={e => u('omega', parseFloat(e.target.value))} />
        </div>
      </div>

      {/* Antoine — optional but all-or-none */}
      <div>
        <label className="flex items-center gap-2 text-[11px] text-slate-400 cursor-pointer">
          <input type="checkbox" checked={antoineFull}
            onChange={e => setAntoineFull(e.target.checked)}
            className="accent-teal-500" />
          Add Antoine vapour-pressure coefficients
        </label>
      </div>

      {antoineFull && (
        <div className="grid grid-cols-3 gap-2 p-3 bg-slate-800 rounded border border-slate-700">
          {(['A', 'B', 'C'] as const).map(coeff => (
            <div key={coeff}>
              <label className={lbl}>Antoine {coeff}</label>
              <input type="number" step="any" className={inp}
                value={(form as Record<string, unknown>)[`antoine_${coeff.toLowerCase()}`] as number ?? ''}
                onChange={e => u(`antoine_${coeff.toLowerCase()}` as keyof ComponentCreate, parseFloat(e.target.value))} />
            </div>
          ))}
          <div>
            <label className={lbl}>Tmin (K)</label>
            <input type="number" step="1" className={inp}
              value={form.antoine_tmin ?? ''}
              onChange={e => u('antoine_tmin', parseFloat(e.target.value))} />
          </div>
          <div>
            <label className={lbl}>Tmax (K)</label>
            <input type="number" step="1" className={inp}
              value={form.antoine_tmax ?? ''}
              onChange={e => u('antoine_tmax', parseFloat(e.target.value))} />
          </div>
          <div>
            <label className={lbl}>Units</label>
            <select className={inp}
              value={form.antoine_units ?? 'mmHg'}
              onChange={e => u('antoine_units', e.target.value as 'mmHg' | 'Pa')}>
              <option value="mmHg">mmHg</option>
              <option value="Pa">Pa</option>
            </select>
          </div>
        </div>
      )}

      {error && (
        <p className="text-[11px] text-red-400 bg-red-950 rounded px-2 py-1">{error}</p>
      )}

      <div className="flex gap-2 justify-end pt-1">
        <button type="button" onClick={onCancel}
          className="px-3 py-1.5 rounded text-[11px] bg-slate-700 text-slate-300 hover:bg-slate-600">
          Cancel
        </button>
        <button type="submit" disabled={saving}
          className="px-3 py-1.5 rounded text-[11px] bg-teal-600 hover:bg-teal-500 text-white disabled:opacity-50">
          {saving ? 'Saving…' : 'Create component'}
        </button>
      </div>
    </form>
  )
}

// ── Main modal ────────────────────────────────────────────────────────────────

interface ComponentManagerProps {
  projectId: string
  /** CAS numbers already in the active feed stream */
  activeCasList: string[]
  onAdd: (comp: ChemicalComponent) => void
  onClose: () => void
}

export function ComponentManager({
  projectId,
  activeCasList,
  onAdd,
  onClose,
}: ComponentManagerProps) {
  const [query, setQuery] = useState('')
  const [results, setResults] = useState<ChemicalComponent[]>([])
  const [loading, setLoading] = useState(false)
  const [showCreate, setShowCreate] = useState(false)
  const [antoineTip, setAntoineTip] = useState<Record<string, string>>({})
  const debounceRef = useRef<ReturnType<typeof setTimeout>>()

  const doSearch = useCallback(async (q: string) => {
    setLoading(true)
    try {
      const data = await fetchComponents(q || undefined, 30)
      setResults(data)
    } catch {
      // ignore
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    clearTimeout(debounceRef.current)
    debounceRef.current = setTimeout(() => doSearch(query), 300)
    return () => clearTimeout(debounceRef.current)
  }, [query, doSearch])

  // Initial load
  useEffect(() => { doSearch('') }, [doSearch])

  const checkAntoine = async (cas: string, T = 350) => {
    try {
      const res = await validateAntoine(cas, T)
      setAntoineTip(prev => ({ ...prev, [cas]: res.message }))
    } catch { /* ignore */ }
  }

  const handleCreated = (comp: ChemicalComponent) => {
    setShowCreate(false)
    setResults(prev => [comp, ...prev])
    onAdd(comp)
  }

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/60"
      onClick={e => { if (e.target === e.currentTarget) onClose() }}
    >
      <div className="w-[680px] max-h-[80vh] bg-slate-900 border border-slate-700 rounded-xl shadow-2xl flex flex-col overflow-hidden">
        {/* Header */}
        <div className="flex items-center justify-between px-5 py-3 border-b border-slate-700 flex-shrink-0">
          <span className="text-[14px] font-semibold text-slate-100">Component Library</span>
          <div className="flex items-center gap-2">
            <button
              onClick={() => setShowCreate(s => !s)}
              className="px-3 py-1 rounded text-[11px] font-medium bg-teal-700 hover:bg-teal-600 text-white transition-colors"
            >
              {showCreate ? 'Back to search' : '+ Create custom'}
            </button>
            <button
              onClick={onClose}
              className="w-6 h-6 flex items-center justify-center rounded text-slate-400 hover:text-slate-100 hover:bg-slate-700 text-lg leading-none"
            >
              ×
            </button>
          </div>
        </div>

        {/* Body */}
        <div className="flex-1 overflow-y-auto p-4">
          {showCreate ? (
            <CreateForm
              projectId={projectId}
              onCreated={handleCreated}
              onCancel={() => setShowCreate(false)}
            />
          ) : (
            <>
              {/* Search bar */}
              <input
                autoFocus
                type="text"
                placeholder="Search by name or CAS number…"
                value={query}
                onChange={e => setQuery(e.target.value)}
                className="w-full rounded-lg px-3 py-2 text-[13px] bg-slate-800 border border-slate-600 text-slate-100 placeholder-slate-500 focus:outline-none focus:border-teal-500 mb-4"
              />

              {loading && (
                <p className="text-[12px] text-slate-500 text-center py-4">Searching…</p>
              )}

              {!loading && results.length === 0 && (
                <p className="text-[12px] text-slate-500 text-center py-4">
                  No components found{query ? ` for "${query}"` : ''}.
                </p>
              )}

              {/* Results list */}
              <div className="space-y-1.5">
                {results.map(comp => {
                  const alreadyAdded = activeCasList.includes(comp.cas_number)
                  return (
                    <div
                      key={comp.id}
                      className={[
                        'flex items-start justify-between gap-3 p-3 rounded-lg border transition-colors',
                        alreadyAdded
                          ? 'border-teal-700 bg-teal-950/30'
                          : 'border-slate-700 bg-slate-800 hover:border-slate-600',
                      ].join(' ')}
                    >
                      <div className="min-w-0 flex-1">
                        <div className="flex items-center gap-2 flex-wrap">
                          <span className="text-[13px] font-medium text-slate-100">{comp.name}</span>
                          {comp.formula && (
                            <span className="text-[11px] text-slate-400 font-mono">{comp.formula}</span>
                          )}
                          <span className="text-[10px] text-slate-500 font-mono">{comp.cas_number}</span>
                          <MissingBadge comp={comp} />
                          {!comp.is_global && (
                            <span className="text-[9px] bg-violet-800 text-violet-200 px-1.5 py-0.5 rounded font-bold">
                              custom
                            </span>
                          )}
                        </div>
                        <div className="flex flex-wrap gap-1 mt-1.5">
                          <Pill label="Tc" value={comp.tc ? `${comp.tc.toFixed(1)} K` : null} />
                          <Pill label="Pc" value={comp.pc ? `${(comp.pc / 1e5).toFixed(2)} bar` : null} />
                          <Pill label="ω" value={comp.omega} />
                          <Pill label="MW" value={comp.mw ? `${comp.mw.toFixed(2)} g/mol` : null} />
                          {comp.antoine_tmin != null && comp.antoine_tmax != null && (
                            <button
                              onClick={() => checkAntoine(comp.cas_number)}
                              className="text-[10px] bg-slate-700 hover:bg-slate-600 text-slate-300 px-1.5 py-0.5 rounded"
                            >
                              Antoine @ 350 K?
                            </button>
                          )}
                        </div>
                        {antoineTip[comp.cas_number] && (
                          <p className="text-[10px] text-amber-400 mt-1">{antoineTip[comp.cas_number]}</p>
                        )}
                      </div>

                      <button
                        disabled={alreadyAdded}
                        onClick={() => onAdd(comp)}
                        className={[
                          'flex-shrink-0 px-3 py-1 rounded text-[11px] font-medium transition-colors',
                          alreadyAdded
                            ? 'bg-teal-800 text-teal-300 cursor-default'
                            : 'bg-teal-600 hover:bg-teal-500 text-white',
                        ].join(' ')}
                      >
                        {alreadyAdded ? '✓ Added' : 'Add'}
                      </button>
                    </div>
                  )
                })}
              </div>
            </>
          )}
        </div>
      </div>
    </div>
  )
}
