"""
process_metrics.py — enrichment layer between FlowsheetSolver and Phase 3 AI.

Given the raw solver output dict plus the flowsheet topology, computes:

  process_metrics     — aggregate heat/work/conversion/recycle figures
  stream_annotations  — per-stream role + phase classification
  solver_diagnostics  — timing, iteration count, structured warnings
  process_summary     — plain-English paragraph for AI grounding

All four are stored in SimulationResult and forwarded to context_builder.py.
"""

from __future__ import annotations

import re
from typing import Any


# ── Public entry point ────────────────────────────────────────────────────────

def compute_enriched_result(
    raw: dict[str, Any],
    nodes: list[dict[str, Any]],
    edges: list[dict[str, Any]],
    solve_time_ms: int,
) -> dict[str, Any]:
    """
    Enrich a FlowsheetSolver result with process metrics, stream annotations,
    solver diagnostics, and a plain-English process summary.

    Parameters
    ----------
    raw           : return value of ``FlowsheetSolver.solve()``
    nodes         : flowsheet node list (``Flowsheet.nodes``)
    edges         : flowsheet edge list (``Flowsheet.edges``)
    solve_time_ms : wall-clock time spent inside ``FlowsheetSolver.solve()``
    """
    node_map        = {n["id"]: n for n in nodes}
    streams         = raw.get("streams", {})
    node_summaries  = raw.get("node_summaries", {})
    convergence_info = raw.get("convergence_info", {})
    raw_warnings    = raw.get("warnings", [])

    tear_ids      = set(convergence_info.get("tear_streams", []))
    feed_edge_ids = _edge_ids_by_source_type(edges, node_map, "feed")
    prod_edge_ids = _edge_ids_by_target_type(edges, node_map, "product")

    process_metrics = _compute_process_metrics(
        node_map, node_summaries, streams,
        feed_edge_ids, prod_edge_ids, tear_ids,
    )

    stream_annotations = _annotate_streams(
        streams, edges, node_map, tear_ids, prod_edge_ids,
    )

    solver_diagnostics = {
        "solve_time_ms":          solve_time_ms,
        "convergence_iterations": convergence_info.get("iterations", 0),
        "converged":              convergence_info.get("converged", raw.get("converged", True)),
        "tear_streams":           sorted(tear_ids),
        "residuals":              convergence_info.get("residuals", []),
        "warnings":               _structure_warnings(raw_warnings),
    }

    process_summary = _generate_summary(
        nodes, streams, feed_edge_ids,
        process_metrics, solver_diagnostics,
    )

    return {
        "process_metrics":    process_metrics,
        "stream_annotations": stream_annotations,
        "solver_diagnostics": solver_diagnostics,
        "process_summary":    process_summary,
    }


# ── Topology helpers ──────────────────────────────────────────────────────────

def _edge_ids_by_source_type(
    edges: list[dict], node_map: dict, node_type: str
) -> set[str]:
    return {
        e["id"] for e in edges
        if node_map.get(e.get("source", ""), {}).get("type") == node_type
    }


def _edge_ids_by_target_type(
    edges: list[dict], node_map: dict, node_type: str
) -> set[str]:
    return {
        e["id"] for e in edges
        if node_map.get(e.get("target", ""), {}).get("type") == node_type
    }


# ── Process metrics ───────────────────────────────────────────────────────────

def _compute_process_metrics(
    node_map: dict,
    node_summaries: dict[str, Any],
    streams: dict[str, Any],
    feed_edge_ids: set[str],
    prod_edge_ids: set[str],
    tear_ids: set[str],
) -> dict[str, Any]:
    heat_kw  = 0.0
    cool_kw  = 0.0
    shaft_kw = 0.0

    for nid, smry in node_summaries.items():
        q = float(smry.get("duty_W") or 0.0)
        if q > 0:
            heat_kw += q / 1000.0
        elif q < 0:
            cool_kw += abs(q) / 1000.0

        w = float(smry.get("shaft_work_W") or 0.0)
        shaft_kw += w / 1000.0

    # Overall conversion: (feed_flow_i − product_flow_i) / feed_flow_i × 100
    feed_component_flow:    dict[str, float] = {}
    product_component_flow: dict[str, float] = {}

    for eid, s in streams.items():
        comp = s.get("composition", {})
        flow = float(s.get("flow", 0.0))
        if eid in feed_edge_ids:
            for c, x in comp.items():
                feed_component_flow[c] = feed_component_flow.get(c, 0.0) + x * flow
        if eid in prod_edge_ids:
            for c, x in comp.items():
                product_component_flow[c] = product_component_flow.get(c, 0.0) + x * flow

    overall_conversion: dict[str, float] = {}
    for comp, f_in in feed_component_flow.items():
        if f_in > 1e-9:
            f_out = product_component_flow.get(comp, 0.0)
            conv  = max(0.0, (f_in - f_out) / f_in * 100.0)
            if conv > 0.1:   # skip components that don't react
                overall_conversion[comp] = round(conv, 2)

    # Recycle ratio: recycle_stream_flow / total_fresh_feed_flow
    total_feed_flow = sum(
        float(streams[eid].get("flow", 0.0))
        for eid in feed_edge_ids if eid in streams
    )
    recycle_ratio: dict[str, float] = {}
    for eid in tear_ids:
        if eid in streams and total_feed_flow > 1e-9:
            recycle_ratio[eid] = round(
                float(streams[eid].get("flow", 0.0)) / total_feed_flow, 4
            )

    return {
        "total_heat_duty_kW":    round(heat_kw, 3),
        "total_cooling_duty_kW": round(cool_kw, 3),
        "total_shaft_work_kW":   round(shaft_kw, 3),
        "overall_conversion":    overall_conversion,
        "recycle_ratio":         recycle_ratio,
        "pinch_temperature":     None,   # requires pinch analysis (Phase 3+)
        "Q_H_min":               None,
        "energy_efficiency_pct": None,
    }


# ── Stream annotations ────────────────────────────────────────────────────────

def _annotate_streams(
    streams: dict[str, Any],
    edges: list[dict],
    node_map: dict,
    tear_ids: set[str],
    prod_edge_ids: set[str],
) -> dict[str, dict]:
    edge_map = {e["id"]: e for e in edges}
    annotations: dict[str, dict] = {}

    for eid, s in streams.items():
        vf    = float(s.get("vapor_fraction") or 0.0)
        phase = "liquid" if vf <= 0.01 else "vapor" if vf >= 0.99 else "mixed"

        edge        = edge_map.get(eid, {})
        target_type = node_map.get(edge.get("target", ""), {}).get("type", "")

        annotations[eid] = {
            "is_recycle":          eid in tear_ids,
            "is_product":          eid in prod_edge_ids,
            "is_waste":            target_type == "waste",
            "phase":               phase,
            "distance_from_pinch": None,   # requires pinch analysis
        }

    return annotations


# ── Structured warnings ───────────────────────────────────────────────────────

# (pattern, code, severity) — first match wins
_WARNING_PATTERNS: list[tuple[str, str, str]] = [
    (r"Node '([^']+)' \([^)]+\):",   "node_error",          "error"),
    (r"still contains a cycle",       "residual_cycle",       "error"),
    (r"references unknown node",      "unknown_edge",         "warning"),
    (r"has no inlet stream",          "missing_inlet",        "warning"),
    (r"[Uu]nknown node type",         "unknown_node_type",    "warning"),
    (r"vapor_fraction",               "vapor_inlet",          "warning"),
    (r"[Ss]low \(|convergence was slow", "slow_convergence", "info"),
]


def _structure_warnings(raw_warnings: list[str]) -> list[dict[str, Any]]:
    out = []
    for msg in raw_warnings:
        code, severity = "solver_warning", "warning"
        node_id: str | None = None

        m = re.search(r"Node '([^']+)'", msg)
        if m:
            node_id = m.group(1)

        for pattern, pat_code, pat_sev in _WARNING_PATTERNS:
            if re.search(pattern, msg):
                code, severity = pat_code, pat_sev
                break

        out.append({"code": code, "severity": severity, "message": msg, "node_id": node_id})
    return out


# ── Plain-English summary ─────────────────────────────────────────────────────

def _generate_summary(
    nodes: list[dict],
    streams: dict[str, Any],
    feed_edge_ids: set[str],
    pm: dict[str, Any],
    diag: dict[str, Any],
) -> str:
    passive = {"feed", "product", "recycle"}
    n_ops   = sum(1 for n in nodes if n.get("type") not in passive)

    # Feed rate in kmol/hr
    total_mol_s = sum(
        float(streams[eid].get("flow", 0.0))
        for eid in feed_edge_ids if eid in streams
    )
    feed_str = (
        f"{total_mol_s * 3.6:.1f} kmol/hr"
        if total_mol_s > 0 else "an unknown feed rate"
    )

    # Conversion
    conv = pm.get("overall_conversion", {})
    if conv:
        top_comp, top_pct = max(conv.items(), key=lambda kv: kv[1])
        conv_note = f"Overall conversion of {top_comp} is {top_pct:.1f}%"
    else:
        conv_note = "No reactive components detected"

    # Energy
    Q_H = pm.get("total_heat_duty_kW", 0.0) or 0.0
    Q_C = pm.get("total_cooling_duty_kW", 0.0) or 0.0
    W_s = pm.get("total_shaft_work_kW", 0.0) or 0.0
    energy_parts = []
    if Q_H > 0:
        energy_parts.append(f"{Q_H:.1f} kW of heating")
    if Q_C > 0:
        energy_parts.append(f"{Q_C:.1f} kW of cooling")
    if W_s > 0:
        energy_parts.append(f"{W_s:.1f} kW of shaft work")
    energy_note = (
        "The process requires " + " and ".join(energy_parts)
        if energy_parts else "No utility requirements detected"
    )

    # Recycle
    rr    = pm.get("recycle_ratio", {}) or {}
    n_rec = len(rr)
    if n_rec:
        avg_r       = sum(rr.values()) / n_rec
        recycle_note = (
            f"The flowsheet includes {n_rec} recycle loop(s) "
            f"with an average recycle-to-feed ratio of {avg_r:.2f}"
        )
    else:
        recycle_note = "No recycle streams"

    # Warnings
    structured = diag.get("warnings", [])
    n_err  = sum(1 for w in structured if w.get("severity") == "error")
    n_warn = len(structured)
    if n_err:
        warn_note = f"{n_err} solver error(s) — results may be incomplete"
    elif n_warn:
        warn_note = f"{n_warn} solver warning(s) issued"
    else:
        warn_note = "Solver completed without warnings"

    return (
        f"The flowsheet contains {n_ops} unit operation(s) processing "
        f"{feed_str} of feed. "
        f"{conv_note}. "
        f"{energy_note}. "
        f"{recycle_note}. "
        f"Pinch analysis not yet performed. "
        f"{warn_note}."
    )
