/**
 * Custom React Flow node for all ChemFlow unit operations.
 *
 * Visual design
 * ─────────────
 *  ┌─────────────────────────────────┐
 *  │▓▓▓▓▓▓▓▓  (type-colour top bar) │
 *  │  [icon]  Label                  │
 *  │          unit type              │
 *  └─────────────────────────────────┘
 *
 * Handles
 * ───────
 *  Blue  •  inlet / target handles  (left side)
 *  Red   •  outlet / source handles (right side)
 *
 * Multi-handle spacing: top handle at 30 %, bottom at 70 % of node height.
 */

import { Handle, Position } from '@xyflow/react'
import type { NodeProps } from '@xyflow/react'

// ── SVG icon components ───────────────────────────────────────────────────────

const iconProps = {
  viewBox: '0 0 24 24',
  fill: 'none',
  stroke: 'currentColor',
  strokeWidth: 2,
  strokeLinecap: 'round' as const,
  strokeLinejoin: 'round' as const,
  className: 'w-full h-full',
}

function FeedIcon() {
  return (
    <svg {...iconProps}>
      {/* Rightward solid triangle — "source" symbol */}
      <polygon points="4,3 20,12 4,21" fill="currentColor" stroke="none" />
    </svg>
  )
}

function ProductIcon() {
  return (
    <svg {...iconProps}>
      {/* Square with a check-mark — "collected product" */}
      <rect x="3" y="3" width="18" height="18" rx="3" />
      <polyline points="7,12 10,15 17,9" />
    </svg>
  )
}

function MixerIcon() {
  return (
    <svg {...iconProps}>
      {/* Two upper streams converging down to one */}
      <line x1="4"  y1="4"  x2="12" y2="14" />
      <line x1="20" y1="4"  x2="12" y2="14" />
      <line x1="12" y1="14" x2="12" y2="21" />
      <circle cx="12" cy="14" r="1.5" fill="currentColor" stroke="none" />
    </svg>
  )
}

function SplitterIcon() {
  return (
    <svg {...iconProps}>
      {/* One stream splitting into two */}
      <line x1="4"  y1="12" x2="12" y2="12" />
      <line x1="12" y1="12" x2="20" y2="5"  />
      <line x1="12" y1="12" x2="20" y2="19" />
      <circle cx="12" cy="12" r="1.5" fill="currentColor" stroke="none" />
    </svg>
  )
}

function HeatExchangerIcon() {
  return (
    <svg {...iconProps}>
      {/* Sine-wave coil between two vertical headers */}
      <line x1="2" y1="6" x2="2" y2="18" />
      <line x1="22" y1="6" x2="22" y2="18" />
      <path d="M2 12 C5 6 7 6 9 12 C11 18 13 18 15 12 C17 6 19 6 22 12" />
    </svg>
  )
}

function PFRIcon() {
  return (
    <svg {...iconProps}>
      {/* Horizontal cylinder (plug-flow reactor) */}
      <ellipse cx="6"  cy="12" rx="3" ry="6" />
      <ellipse cx="18" cy="12" rx="3" ry="6" />
      <line x1="6"  y1="6"  x2="18" y2="6"  />
      <line x1="6"  y1="18" x2="18" y2="18" />
      {/* Internal flow arrow */}
      <polyline points="9,12 14,12 12,10" strokeWidth="1.5" />
    </svg>
  )
}

function FlashIcon() {
  return (
    <svg {...iconProps}>
      {/* Vertical pressure vessel with liquid/vapour split dashes */}
      <path d="M8 3 L16 3 L18 7 L18 20 C18 21.1 17.1 22 16 22 L8 22 C6.9 22 6 21.1 6 20 L6 7 Z" />
      {/* Vapour nozzle top, liquid nozzle bottom */}
      <line x1="12" y1="3"  x2="12" y2="1"  strokeWidth="2.5" />
      <line x1="12" y1="22" x2="12" y2="24" strokeWidth="2.5" />
      {/* Phase boundary */}
      <line x1="6.5" y1="13" x2="17.5" y2="13" strokeDasharray="2.5 1.5" />
    </svg>
  )
}

function PumpIcon() {
  return (
    <svg {...iconProps}>
      {/* Centrifugal pump: outer ring + impeller vanes */}
      <circle cx="12" cy="12" r="9" />
      <line x1="12" y1="12" x2="8"  y2="8"  />
      <line x1="12" y1="12" x2="16" y2="8"  />
      <line x1="12" y1="12" x2="17" y2="14" />
      <line x1="12" y1="12" x2="8"  y2="17" />
    </svg>
  )
}

function CSTRIcon() {
  return (
    <svg {...iconProps}>
      {/* Vertical tank with agitator shaft + cooling jacket lines */}
      <rect x="5" y="4" width="14" height="16" rx="2" />
      {/* Agitator shaft */}
      <line x1="12" y1="4" x2="12" y2="14" />
      {/* Agitator blades */}
      <line x1="8"  y1="13" x2="16" y2="13" />
      <line x1="8"  y1="15" x2="16" y2="15" />
      {/* Cooling jacket wavy line */}
      <path d="M5 8 C3 9 3 11 5 12 C3 13 3 15 5 16" strokeWidth="1.5" />
    </svg>
  )
}

function RecycleIcon() {
  return (
    <svg {...iconProps}>
      {/* Dashed circular arc with arrowhead — marks the tear stream cut point */}
      <path
        d="M 12 3 A 9 9 0 1 1 3.5 16"
        strokeDasharray="4 2.5"
        fill="none"
      />
      {/* Arrowhead at the open end of the arc */}
      <polyline points="1,13 3.5,16.5 6.5,14" />
    </svg>
  )
}

function DistillationIcon() {
  return (
    <svg {...iconProps}>
      {/* Column shell — tall vertical rectangle */}
      <rect x="7" y="2" width="10" height="20" rx="1" />
      {/* Condenser — small triangle top-left of column */}
      <polygon points="2,2 7,2 7,7" fill="currentColor" stroke="none" opacity="0.7" />
      {/* Reboiler — trapezoid at bottom */}
      <polygon points="6,22 18,22 16,24 8,24" fill="currentColor" stroke="none" opacity="0.7" />
      {/* Dashed tray lines inside column body */}
      <line x1="7" y1="8"  x2="17" y2="8"  strokeDasharray="2 1.5" strokeWidth="1" />
      <line x1="7" y1="13" x2="17" y2="13" strokeDasharray="2 1.5" strokeWidth="1" />
      <line x1="7" y1="18" x2="17" y2="18" strokeDasharray="2 1.5" strokeWidth="1" />
    </svg>
  )
}

// ── Node metadata ─────────────────────────────────────────────────────────────

interface NodeMeta {
  Icon: React.FC
  bar: string      // Tailwind bg class for the top colour bar
  icon: string     // Tailwind text class for the icon
  inlets: number
  outlets: number
  outletLabels?: string[]  // hover titles for handle dots
}

const META: Record<string, NodeMeta> = {
  feed: {
    Icon: FeedIcon,
    bar: 'bg-teal-500',
    icon: 'text-teal-600',
    inlets: 0,
    outlets: 1,
  },
  product: {
    Icon: ProductIcon,
    bar: 'bg-emerald-500',
    icon: 'text-emerald-600',
    inlets: 1,
    outlets: 0,
  },
  mixer: {
    Icon: MixerIcon,
    bar: 'bg-violet-500',
    icon: 'text-violet-600',
    inlets: 2,
    outlets: 1,
  },
  splitter: {
    Icon: SplitterIcon,
    bar: 'bg-amber-500',
    icon: 'text-amber-600',
    inlets: 1,
    outlets: 2,
    outletLabels: ['frac 0', 'frac 1'],
  },
  heat_exchanger: {
    Icon: HeatExchangerIcon,
    bar: 'bg-red-500',
    icon: 'text-red-600',
    inlets: 1,
    outlets: 1,
  },
  flash_drum: {
    Icon: FlashIcon,
    bar: 'bg-sky-500',
    icon: 'text-sky-600',
    inlets: 1,
    outlets: 2,
    outletLabels: ['vapour', 'liquid'],
  },
  pfr: {
    Icon: PFRIcon,
    bar: 'bg-lime-500',
    icon: 'text-lime-700',
    inlets: 1,
    outlets: 1,
  },
  pump: {
    Icon: PumpIcon,
    bar: 'bg-orange-500',
    icon: 'text-orange-600',
    inlets: 1,
    outlets: 1,
  },
  cstr: {
    Icon: CSTRIcon,
    bar: 'bg-cyan-500',
    icon: 'text-cyan-600',
    inlets: 1,
    outlets: 1,
  },
  recycle: {
    Icon: RecycleIcon,
    bar: 'bg-purple-400',
    icon: 'text-purple-600',
    inlets: 1,
    outlets: 1,
  },
  distillation_shortcut: {
    Icon: DistillationIcon,
    bar: 'bg-blue-500',
    icon: 'text-blue-600',
    inlets: 1,
    outlets: 2,
    outletLabels: ['distillate', 'bottoms'],
  },
}

const FALLBACK_META: NodeMeta = {
  Icon: FeedIcon,
  bar: 'bg-slate-500',
  icon: 'text-slate-600',
  inlets: 1,
  outlets: 1,
}

// ── Helper: position for nth handle on a side with `total` handles ────────────
function handleTop(index: number, total: number): string {
  if (total === 1) return '50%'
  // evenly spread between 25 % and 75 %
  const step = 50 / (total - 1)
  return `${25 + index * step}%`
}

// ── The node component ────────────────────────────────────────────────────────

type UnitNodeData = {
  nodeType: string
  label: string
  [key: string]: unknown
}

export function UnitNode({ data, selected }: NodeProps) {
  const d   = data as UnitNodeData
  const meta = META[d.nodeType] ?? FALLBACK_META
  const { Icon, bar, icon, inlets, outlets, outletLabels } = meta

  const typeLabel = (d.nodeType ?? '').replace(/_/g, ' ')

  const isRecycle = d.nodeType === 'recycle'

  return (
    <div
      className={[
        'relative rounded-lg bg-white overflow-hidden select-none',
        'border-2 transition-shadow duration-150',
        isRecycle
          ? selected
            ? 'border-purple-500 shadow-lg shadow-purple-200/60 border-dashed'
            : 'border-purple-300 shadow-md border-dashed'
          : selected
            ? 'border-teal-500 shadow-lg shadow-teal-200/60'
            : 'border-slate-200 shadow-md',
      ].join(' ')}
      style={{ width: 168 }}
    >
      {/* Type-coloured top bar */}
      <div className={`${bar} h-1.5 w-full`} />

      {/* Body */}
      <div className="flex items-center gap-2.5 px-3 py-2">
        {/* Icon */}
        <div className={`${icon} w-7 h-7 flex-shrink-0`}>
          <Icon />
        </div>

        {/* Label + type */}
        <div className="min-w-0 flex-1">
          <p className="text-[13px] font-semibold text-slate-800 leading-tight truncate">
            {d.label}
          </p>
          <p className="text-[11px] text-slate-400 leading-tight capitalize">
            {typeLabel}
          </p>
        </div>
      </div>

      {/* ── Inlet handles (blue, left) ── */}
      {Array.from({ length: inlets }, (_, i) => (
        <Handle
          key={`in-${i}`}
          type="target"
          position={Position.Left}
          id={inlets === 1 ? 'in' : `in${i}`}
          title="inlet"
          style={{ top: handleTop(i, inlets) }}
          className="!bg-blue-500 !border-2 !border-white !w-3 !h-3 !rounded-full"
        />
      ))}

      {/* ── Outlet handles (red, right) ── */}
      {Array.from({ length: outlets }, (_, i) => (
        <Handle
          key={`out-${i}`}
          type="source"
          position={Position.Right}
          id={String(i)}
          title={outletLabels?.[i] ?? 'outlet'}
          style={{ top: handleTop(i, outlets) }}
          className="!bg-red-500 !border-2 !border-white !w-3 !h-3 !rounded-full"
        />
      ))}
    </div>
  )
}
