/**
 * Custom React Flow edge for process streams.
 *
 * Features
 * ────────
 *  • Renders a smooth Bezier path in slate-400
 *  • Shows a small dot at the midpoint as a hover target
 *  • Hover reveals an × delete button and (if set) the stream label
 *  • Double-click the label area to rename the stream inline
 *  • On hover, displays a tooltip with T, P, flow, vapour fraction
 *    (data is populated after a successful simulation run)
 */

import {
  BaseEdge,
  EdgeLabelRenderer,
  getBezierPath,
  useReactFlow,
} from '@xyflow/react'
import type { EdgeProps } from '@xyflow/react'
import { useEffect, useRef, useState } from 'react'
import type { StreamState } from '../../types'

export function StreamEdge({
  id,
  label,
  sourceX,
  sourceY,
  targetX,
  targetY,
  sourcePosition,
  targetPosition,
  markerEnd,
  data,
}: EdgeProps) {
  const { deleteElements, setEdges } = useReactFlow()

  const [edgePath, labelX, labelY] = getBezierPath({
    sourceX,
    sourceY,
    sourcePosition,
    targetX,
    targetY,
    targetPosition,
  })

  const [hovered,  setHovered]  = useState(false)
  const [editing,  setEditing]  = useState(false)
  const [draft,    setDraft]    = useState(String(label ?? ''))
  const inputRef = useRef<HTMLInputElement>(null)

  const stream = (data as { stream?: StreamState } | undefined)?.stream

  // Keep draft in sync if label changes externally (e.g. undo)
  useEffect(() => { setDraft(String(label ?? '')) }, [label])

  // Focus the input when entering edit mode
  useEffect(() => { if (editing) inputRef.current?.select() }, [editing])

  const commitLabel = () => {
    setEdges(eds => eds.map(e => e.id === id ? { ...e, label: draft.trim() } : e))
    setEditing(false)
  }

  const cancelEdit = () => {
    setDraft(String(label ?? ''))
    setEditing(false)
  }

  const handleDelete = (e: React.MouseEvent) => {
    e.stopPropagation()
    deleteElements({ edges: [{ id }] })
  }

  const currentLabel = String(label ?? '').trim()

  return (
    <>
      {/* The line itself */}
      <BaseEdge
        path={edgePath}
        markerEnd={markerEnd}
        style={{ stroke: '#94a3b8', strokeWidth: 2 }}
      />

      {/* Midpoint interactive zone */}
      <EdgeLabelRenderer>
        <div
          style={{
            position: 'absolute',
            transform: `translate(-50%, -50%) translate(${labelX}px,${labelY}px)`,
            pointerEvents: 'all',
          }}
          className="nodrag nopan flex flex-col items-center"
          onMouseEnter={() => setHovered(true)}
          onMouseLeave={() => { setHovered(false) }}
        >
          {/* ── Label row (shown when label exists or hovered) ── */}
          {!editing && (currentLabel || hovered) && (
            <div
              className={[
                'mb-1 px-1.5 py-0.5 rounded text-[10px] leading-none cursor-text select-none',
                'bg-white/90 border border-slate-200 text-slate-500',
                'transition-opacity duration-100',
                currentLabel ? 'opacity-100' : 'opacity-40 italic',
              ].join(' ')}
              onDoubleClick={() => setEditing(true)}
              title="Double-click to rename"
            >
              {currentLabel || 'name…'}
            </div>
          )}

          {/* ── Inline rename input ── */}
          {editing && (
            <input
              ref={inputRef}
              className="mb-1 px-1.5 py-0.5 rounded text-[10px] leading-none w-24 text-center border border-teal-400 bg-white text-slate-700 outline-none shadow-sm"
              value={draft}
              onChange={e => setDraft(e.target.value)}
              onBlur={commitLabel}
              onKeyDown={e => {
                if (e.key === 'Enter')  { e.preventDefault(); commitLabel() }
                if (e.key === 'Escape') { e.preventDefault(); cancelEdit()  }
              }}
            />
          )}

          {/* ── Dot + delete button row ── */}
          <div className="flex items-center gap-1">
            <div
              className="flex items-center justify-center w-5 h-5 cursor-default"
              onMouseEnter={() => setHovered(true)}
            >
              <div
                className={[
                  'rounded-full transition-all duration-150',
                  hovered
                    ? 'w-3 h-3 bg-teal-400 shadow shadow-teal-400/60'
                    : 'w-2 h-2 bg-slate-400',
                ].join(' ')}
              />
            </div>

            {/* × delete button — visible on hover */}
            {hovered && !editing && (
              <button
                className="flex items-center justify-center w-4 h-4 rounded-full text-slate-400 hover:text-red-500 hover:bg-red-50 transition-colors duration-100 text-[13px] leading-none"
                onClick={handleDelete}
                title="Delete stream"
              >
                ×
              </button>
            )}
          </div>

          {/* ── Tooltip — only while hovered and not editing ── */}
          {hovered && !editing && (
            <div
              className={[
                'absolute top-9 left-1/2 -translate-x-1/2 z-50',
                'rounded-xl border border-slate-700 bg-slate-900',
                'px-3 py-2.5 shadow-2xl shadow-black/40',
                'whitespace-nowrap',
              ].join(' ')}
            >
              {stream ? (
                <>
                  <p className="text-[11px] font-semibold text-teal-400 mb-2 tracking-wide">
                    {stream.name ?? id}
                  </p>

                  <dl className="grid grid-cols-2 gap-x-4 gap-y-1">
                    <dt className="text-[11px] text-slate-400">T</dt>
                    <dd className="text-[11px] text-slate-100 font-mono text-right">
                      {stream.temperature.toFixed(1)} °C
                    </dd>

                    <dt className="text-[11px] text-slate-400">P</dt>
                    <dd className="text-[11px] text-slate-100 font-mono text-right">
                      {stream.pressure.toFixed(2)} bar
                    </dd>

                    <dt className="text-[11px] text-slate-400">Flow</dt>
                    <dd className="text-[11px] text-slate-100 font-mono text-right">
                      {stream.flow.toFixed(4)} mol/s
                    </dd>

                    {stream.vapor_fraction != null && (
                      <>
                        <dt className="text-[11px] text-slate-400">Vapour ψ</dt>
                        <dd className="text-[11px] text-slate-100 font-mono text-right">
                          {stream.vapor_fraction.toFixed(4)}
                        </dd>
                      </>
                    )}
                  </dl>

                  {Object.keys(stream.composition).length > 0 && (
                    <div className="mt-2 pt-2 border-t border-slate-700">
                      {Object.entries(stream.composition)
                        .sort(([, a], [, b]) => b - a)
                        .slice(0, 4)
                        .map(([comp, frac]) => (
                          <div key={comp} className="flex justify-between gap-4">
                            <span className="text-[10px] text-slate-400">{comp}</span>
                            <span className="text-[10px] text-slate-200 font-mono">
                              {frac.toFixed(4)}
                            </span>
                          </div>
                        ))}
                    </div>
                  )}
                </>
              ) : (
                <p className="text-[11px] text-slate-400 italic">
                  Run simulation to see stream data
                </p>
              )}
            </div>
          )}
        </div>
      </EdgeLabelRenderer>
    </>
  )
}
