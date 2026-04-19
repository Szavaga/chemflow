/**
 * Custom React Flow edge for process streams.
 *
 * Features
 * ────────
 *  • Renders a smooth Bezier path in slate-400
 *  • Shows a small dot at the midpoint as a hover target
 *  • On hover, displays a tooltip with T, P, flow, vapour fraction
 *    (data is populated after a successful simulation run)
 */

import {
  BaseEdge,
  EdgeLabelRenderer,
  getBezierPath,
} from '@xyflow/react'
import type { EdgeProps } from '@xyflow/react'
import { useState } from 'react'
import type { StreamState } from '../../types'

export function StreamEdge({
  id,
  sourceX,
  sourceY,
  targetX,
  targetY,
  sourcePosition,
  targetPosition,
  markerEnd,
  data,
}: EdgeProps) {
  const [edgePath, labelX, labelY] = getBezierPath({
    sourceX,
    sourceY,
    sourcePosition,
    targetX,
    targetY,
    targetPosition,
  })

  const [hovered, setHovered] = useState(false)
  const stream = (data as { stream?: StreamState } | undefined)?.stream

  return (
    <>
      {/* The line itself */}
      <BaseEdge
        path={edgePath}
        markerEnd={markerEnd}
        style={{ stroke: '#94a3b8', strokeWidth: 2 }}
      />

      {/* Midpoint hover zone + tooltip */}
      <EdgeLabelRenderer>
        <div
          style={{
            position: 'absolute',
            transform: `translate(-50%, -50%) translate(${labelX}px,${labelY}px)`,
            pointerEvents: 'all',
          }}
          className="nodrag nopan"
          onMouseEnter={() => setHovered(true)}
          onMouseLeave={() => setHovered(false)}
        >
          {/* Visible midpoint dot */}
          <div className="flex items-center justify-center w-5 h-5 cursor-default">
            <div
              className={[
                'rounded-full transition-all duration-150',
                hovered
                  ? 'w-3 h-3 bg-teal-400 shadow shadow-teal-400/60'
                  : 'w-2 h-2 bg-slate-400',
              ].join(' ')}
            />
          </div>

          {/* Tooltip — only while hovered */}
          {hovered && (
            <div
              className={[
                'absolute bottom-7 left-1/2 -translate-x-1/2 z-50',
                'rounded-xl border border-slate-700 bg-slate-900',
                'px-3 py-2.5 shadow-2xl shadow-black/40',
                'whitespace-nowrap',
              ].join(' ')}
            >
              {stream ? (
                <>
                  {/* Stream name / edge id */}
                  <p className="text-[11px] font-semibold text-teal-400 mb-2 tracking-wide">
                    {stream.name ?? id}
                  </p>

                  {/* Conditions grid */}
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

                  {/* Top components */}
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
