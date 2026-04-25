"""
context_builder.py — bridge between Phase 2 (simulation) and Phase 3 (AI).

Takes a fully-enriched SimulationResult dict and renders it as a structured
text block ready for direct inclusion in a Claude API prompt.

Usage
-----
    from app.core.context_builder import build_prompt_context

    context = build_prompt_context(result_dict)

    # Claude API call (anthropic SDK):
    import anthropic
    client = anthropic.Anthropic()
    response = client.messages.create(
        model="claude-opus-4-7",
        max_tokens=1024,
        system="You are an expert chemical process engineer...",
        messages=[
            {
                "role": "user",
                "content": (
                    f"{context}\\n\\n"
                    "What are the main opportunities to reduce energy consumption "
                    "in this process?"
                ),
            }
        ],
    )

Input schema (all fields optional — builder degrades gracefully)
----------------------------------------------------------------
result = {
    "streams":            {edge_id: StreamState, ...},
    "energy_balance":     {total_duty_kW, heating_kW, cooling_kW},
    "warnings":           [str, ...],          # legacy string warnings
    "converged":          bool,

    # enriched fields (populated by process_metrics.compute_enriched_result)
    "process_summary":    str,
    "process_metrics":    {
        total_heat_duty_kW, total_cooling_duty_kW, total_shaft_work_kW,
        overall_conversion: {component: pct},
        recycle_ratio:      {edge_id: ratio},
        pinch_temperature, Q_H_min, energy_efficiency_pct,
    },
    "stream_annotations": {
        edge_id: {is_recycle, is_product, is_waste, phase, distance_from_pinch}
    },
    "solver_diagnostics": {
        solve_time_ms, convergence_iterations, converged,
        tear_streams, residuals,
        warnings: [{code, severity, message, node_id}, ...],
    },
}
"""

from __future__ import annotations

from typing import Any


# ── Public API ────────────────────────────────────────────────────────────────

def build_prompt_context(result: dict[str, Any]) -> str:
    """
    Format an enriched SimulationResult into a structured text block for a
    Claude API message.  The output is plain text with Markdown-style headers
    so Claude can reference specific sections.
    """
    sections: list[str] = []

    sections.append(_section_overview(result))
    sections.append(_section_metrics(result))
    sections.append(_section_streams(result))
    sections.append(_section_energy(result))
    sections.append(_section_diagnostics(result))

    body = "\n".join(s for s in sections if s)
    return f"=== ChemFlow Process Context ===\n\n{body}\n=== End of Process Context ==="


# ── Section builders ──────────────────────────────────────────────────────────

def _section_overview(result: dict[str, Any]) -> str:
    summary = result.get("process_summary")
    if not summary:
        return ""
    return f"## Process Overview\n\n{summary}\n"


def _section_metrics(result: dict[str, Any]) -> str:
    pm = result.get("process_metrics") or {}
    if not pm:
        return ""

    lines = ["## Process Metrics\n"]

    _append_kv(lines, "Heating duty",      pm.get("total_heat_duty_kW"),    "kW")
    _append_kv(lines, "Cooling duty",      pm.get("total_cooling_duty_kW"), "kW")
    _append_kv(lines, "Shaft work",        pm.get("total_shaft_work_kW"),   "kW")

    conv = pm.get("overall_conversion") or {}
    if conv:
        pairs = ", ".join(f"{c}: {v:.1f}%" for c, v in sorted(conv.items()))
        lines.append(f"- Overall conversion: {pairs}")

    rr = pm.get("recycle_ratio") or {}
    if rr:
        pairs = ", ".join(f"{eid}: {v:.3f}" for eid, v in sorted(rr.items()))
        lines.append(f"- Recycle ratio (stream / fresh feed): {pairs}")

    if pm.get("pinch_temperature") is not None:
        _append_kv(lines, "Pinch temperature",    pm["pinch_temperature"],    "°C")
    if pm.get("Q_H_min") is not None:
        _append_kv(lines, "Minimum heating (pinch)", pm["Q_H_min"],           "kW")
    if pm.get("energy_efficiency_pct") is not None:
        _append_kv(lines, "Energy efficiency",    pm["energy_efficiency_pct"], "%")

    return "\n".join(lines) + "\n"


def _section_streams(result: dict[str, Any]) -> str:
    streams     = result.get("streams") or {}
    annotations = result.get("stream_annotations") or {}
    if not streams:
        return ""

    lines = ["## Stream Table\n"]

    # Header row
    lines.append(
        f"{'Stream ID':<26} {'Flow mol/s':>10} {'T °C':>7} "
        f"{'P bar':>7} {'Phase':<7} {'Role'}"
    )
    lines.append("-" * 72)

    for eid, s in sorted(streams.items()):
        ann   = annotations.get(eid, {})
        role  = _role_label(ann)
        phase = ann.get("phase") or _phase_from_vf(float(s.get("vapor_fraction") or 0.0))
        lines.append(
            f"{eid:<26} {s.get('flow', 0.0):>10.4f} "
            f"{s.get('temperature', 0.0):>7.1f} "
            f"{s.get('pressure', 0.0):>7.3f} "
            f"{phase:<7} {role}"
        )

    # Composition block for product and recycle streams
    notable = {
        eid: s for eid, s in streams.items()
        if annotations.get(eid, {}).get("is_product")
        or annotations.get(eid, {}).get("is_recycle")
    }
    if notable:
        lines.append("\n### Compositions of notable streams\n")
        for eid, s in sorted(notable.items()):
            ann  = annotations.get(eid, {})
            role = _role_label(ann)
            comp = s.get("composition") or {}
            comp_str = ", ".join(
                f"{c}: {x:.4f}" for c, x in sorted(comp.items())
            )
            lines.append(f"- **{eid}** ({role}): {comp_str}")

    return "\n".join(lines) + "\n"


def _section_energy(result: dict[str, Any]) -> str:
    eb = result.get("energy_balance") or {}
    if not eb:
        return ""
    lines = ["## Energy Balance\n"]
    for k, v in sorted(eb.items()):
        if v is not None:
            lines.append(f"- {k}: {v:.3f}")
    return "\n".join(lines) + "\n"


def _section_diagnostics(result: dict[str, Any]) -> str:
    diag = result.get("solver_diagnostics") or {}
    lines = ["## Solver Diagnostics\n"]

    _append_kv(lines, "Solve time",          diag.get("solve_time_ms"),          "ms")
    _append_kv(lines, "Iterations",          diag.get("convergence_iterations"),  "")

    converged = diag.get("converged", result.get("converged"))
    if converged is not None:
        label = "yes" if converged else "NO — results may be unreliable"
        lines.append(f"- Converged: {label}")

    tear = diag.get("tear_streams") or []
    if tear:
        lines.append(f"- Tear streams: {', '.join(sorted(tear))}")

    residuals = diag.get("residuals") or []
    if residuals:
        lines.append(f"- Final residual: {residuals[-1]:.2e}")

    structured_warnings = diag.get("warnings") or []
    if structured_warnings:
        lines.append("\n### Solver warnings\n")
        for w in structured_warnings:
            sev  = (w.get("severity") or "warning").upper()
            code = w.get("code", "")
            msg  = w.get("message", "")
            nid  = w.get("node_id")
            suffix = f"  [node: {nid}]" if nid else ""
            lines.append(f"- [{sev}] {code}: {msg}{suffix}")
    else:
        lines.append("- No solver warnings")

    return "\n".join(lines) + "\n"


# ── Helpers ───────────────────────────────────────────────────────────────────

def _append_kv(lines: list[str], label: str, value: Any, unit: str) -> None:
    if value is None:
        return
    unit_str = f" {unit}" if unit else ""
    lines.append(f"- {label}: {value}{unit_str}")


def _role_label(ann: dict) -> str:
    if ann.get("is_recycle"):
        return "recycle"
    if ann.get("is_product"):
        return "product"
    if ann.get("is_waste"):
        return "waste"
    return "internal"


def _phase_from_vf(vf: float) -> str:
    if vf <= 0.01:
        return "liquid"
    if vf >= 0.99:
        return "vapor"
    return "mixed"
