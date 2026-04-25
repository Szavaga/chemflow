"""
FlowsheetSolver — steady-state solver with SCC-based recycle handling.

Graph analysis
--------------
Builds a networkx DiGraph from flowsheet nodes and edges.
Uses nx.strongly_connected_components() (via nx.condensation) to classify:
  - Singleton SCCs with no self-loop  → acyclic, solved once in condensation order
  - Singleton SCCs with a self-loop   → single-unit recycle
  - SCCs of size > 1                  → recycle loop, Wegstein-accelerated convergence

Solving order
-------------
nx.condensation() produces a DAG whose nodes represent individual SCCs.
nx.topological_sort() on the condensation DAG gives a left-to-right processing
order: every upstream SCC is fully converged before downstream SCCs are touched.
This correctly handles nested loops.

Tear stream selection (per recycle SCC)
---------------------------------------
Back-edges are found by iterative DFS on the SCC subgraph.  Among them the
heuristic selects (in priority order):
  a. Smallest estimated molar flowrate   (minimises convergence sensitivity)
  b. Tie-break: source node with the highest in-degree in the full graph
     (most-constrained node — cutting here propagates information fastest)

Convergence (per recycle SCC)
------------------------------
* Tolerance   : 1e-5   (L2-norm relative residual)
* Max iter    : 150
* Fallback    : if residual > 0.1 at iteration 50, run 10 direct-substitution
                steps and restart Wegstein history
* ConvergenceError is raised after max_iter with full diagnostics

Return value
------------
solve() returns::

    {
        "streams":          {edge_id: stream_dict, ...},
        "node_summaries":   {node_id: summary_dict, ...},
        "energy_balance":   {"total_duty_kW": ..., "heating_kW": ..., "cooling_kW": ...},
        "warnings":         [str, ...],
        "converged":        bool,
        "convergence_info": {
            "converged":     bool,
            "iterations":    int,          # sum over all recycle loops
            "tear_streams":  [edge_id],    # all tear stream IDs
            "residuals":     [float],      # per-iteration residuals (all loops)
            "recycle_loops": [             # NEW per-loop detail
                {
                    "tear_stream_id":           str,
                    "iterations":               int,
                    "final_residual":           float,
                    "method_used":              "wegstein" |
                                                "direct_substitution_fallback",
                    "slow_convergence_warning": bool,
                },
                ...
            ],
        },
    }
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from typing import Any

import networkx as nx
import numpy as np

from app.core.unit_ops import (
    CSTR,
    ConvergenceError,
    Flash,
    HeatExchanger,
    Mixer,
    PFR,
    Pump,
    SimulationError,
    Splitter,
    Stream,
)
from app.core.simulation import resolve_composition

NodeDict = dict[str, Any]
EdgeDict = dict[str, Any]

_MAX_RECYCLE_ITER = 150
_RECYCLE_TOL      = 1e-5
_SLOW_ITER        = 80
_FALLBACK_ITER    = 50    # iteration index (0-based) at which fallback triggers
_FALLBACK_RESID   = 0.1
_FALLBACK_STEPS   = 10
_WEGSTEIN_Q_MIN   = -5.0
_WEGSTEIN_Q_MAX   = 0.0


# ── Internal result container ─────────────────────────────────────────────────

@dataclass
class _LoopResult:
    """Convergence metadata + final state for one recycle SCC."""
    primary_tear_id:  str
    tear_ids:         list[str]
    iterations:       int
    final_residual:   float
    method_used:      str           # "wegstein" | "direct_substitution_fallback"
    all_residuals:    list[float]   # one entry per iteration
    node_outlets:     dict[str, list[Stream]]
    node_summaries:   dict[str, dict]
    tear_streams:     dict[str, Stream]  # converged guess for each tear edge


# ── FlowsheetSolver ───────────────────────────────────────────────────────────

class FlowsheetSolver:
    """
    Steady-state flowsheet solver.

    Parameters
    ----------
    nodes : list of node dicts from ``Flowsheet.nodes``
    edges : list of edge dicts from ``Flowsheet.edges``
    """

    def __init__(self, nodes: list[NodeDict], edges: list[EdgeDict]) -> None:
        self._nodes:      dict[str, NodeDict] = {n["id"]: n for n in nodes}
        self._edges:      list[EdgeDict]      = edges
        self._edge_by_id: dict[str, EdgeDict] = {e["id"]: e for e in edges}
        self._warnings:   list[str]           = []

    # ── Public API ────────────────────────────────────────────────────────────

    def solve(self) -> dict[str, Any]:
        """Solve the flowsheet and return a result dict.

        Uses SCC-based graph analysis: each recycle loop is converged
        independently before its downstream nodes are evaluated.
        """
        G = self._build_digraph()
        C = nx.condensation(G)                       # DAG of SCCs
        scc_order = list(nx.topological_sort(C))     # upstream SCCs first

        all_node_outlets:   dict[str, list[Stream]] = {}
        all_node_summaries: dict[str, dict]          = {}
        all_edge_streams:   dict[str, Stream]        = {}
        loop_results:       list[_LoopResult]         = []

        all_components = self._gather_all_components()

        for scc_idx in scc_order:
            scc_members: set[str] = set(C.nodes[scc_idx]["members"])

            scc_internal_edges = [
                e for e in self._edges
                if e.get("source") in scc_members and e.get("target") in scc_members
            ]
            has_self_loop = any(
                e.get("source") == e.get("target") for e in scc_internal_edges
            )
            is_cyclic = len(scc_members) > 1 or has_self_loop

            # Streams arriving from already-solved upstream SCCs
            ext_inlets = self._collect_external_inlets(scc_members, all_node_outlets)

            if not is_cyclic:
                # ── Acyclic singleton ──────────────────────────────────────
                node_id   = next(iter(scc_members))
                node      = self._nodes[node_id]
                inlets    = ext_inlets.get(node_id, [])
                try:
                    outlets, summary = self._solve_node(
                        node.get("type", "unknown"),
                        node.get("data", {}),
                        inlets,
                        node_id,
                    )
                except SimulationError as exc:
                    self._warnings.append(f"Node '{node_id}': {exc}")
                    try:
                        outlets = [_zero_stream(f"{node_id}_err", inlets)]
                    except SimulationError:
                        outlets = []
                    summary = {"error": str(exc)}

                all_node_outlets[node_id]   = outlets
                all_node_summaries[node_id] = summary

            else:
                # ── Recycle SCC ────────────────────────────────────────────
                tear_ids = self._select_tear_streams_for_scc(
                    scc_members, scc_internal_edges, G
                )
                lr = self._solve_recycle_scc(
                    scc_members, scc_internal_edges, tear_ids,
                    ext_inlets, all_components,
                )
                loop_results.append(lr)
                all_node_outlets.update(lr.node_outlets)
                all_node_summaries.update(lr.node_summaries)
                # Tear edges use the converged guess
                all_edge_streams.update(lr.tear_streams)

            # Populate edge streams for all edges leaving this SCC
            for edge in self._edges:
                src = edge.get("source", "")
                if src in scc_members and src in all_node_outlets:
                    outs = all_node_outlets[src]
                    if outs:
                        out_idx = _parse_handle(edge.get("source_handle", "0"))
                        all_edge_streams[edge["id"]] = outs[min(out_idx, len(outs) - 1)]

        # Build convergence_info (backward-compatible + new recycle_loops field)
        if not loop_results:
            conv_info: dict[str, Any] = {
                "converged":     True,
                "iterations":    0,
                "tear_streams":  [],
                "residuals":     [],
                "recycle_loops": [],
            }
        else:
            all_tear_ids  = [t for lr in loop_results for t in lr.tear_ids]
            total_iter    = sum(lr.iterations for lr in loop_results)
            all_residuals = [r for lr in loop_results for r in lr.all_residuals]
            conv_info = {
                "converged":     True,
                "iterations":    total_iter,
                "tear_streams":  all_tear_ids,
                "residuals":     all_residuals,
                "recycle_loops": [
                    {
                        "tear_stream_id":           lr.primary_tear_id,
                        "iterations":               lr.iterations,
                        "final_residual":           lr.final_residual,
                        "method_used":              lr.method_used,
                        "slow_convergence_warning": lr.iterations > _SLOW_ITER,
                    }
                    for lr in loop_results
                ],
            }

        return {
            "streams":          {k: v.to_dict() for k, v in all_edge_streams.items()},
            "node_summaries":   all_node_summaries,
            "energy_balance":   _aggregate_energy(all_node_summaries),
            "warnings":         self._warnings,
            "converged":        True,
            "convergence_info": conv_info,
        }

    # ── Backward-compat wrapper ───────────────────────────────────────────────

    def _find_tear_streams(self) -> list[str]:
        """Return tear stream edge IDs using SCC analysis.

        Kept for backward compatibility with tests that call this method
        directly.  Internally delegates to the SCC-based logic.
        """
        G = self._build_digraph()
        C = nx.condensation(G)
        tear_ids: list[str] = []
        for scc_idx in C.nodes:
            scc_members: set[str] = set(C.nodes[scc_idx]["members"])
            scc_edges = [
                e for e in self._edges
                if e.get("source") in scc_members and e.get("target") in scc_members
            ]
            has_self_loop = any(
                e.get("source") == e.get("target") for e in scc_edges
            )
            is_cyclic = len(scc_members) > 1 or has_self_loop
            if is_cyclic:
                tear_ids.extend(
                    self._select_tear_streams_for_scc(scc_members, scc_edges, G)
                )
        return tear_ids

    # ── Graph construction ────────────────────────────────────────────────────

    def _build_digraph(self) -> nx.DiGraph:
        G: nx.DiGraph = nx.DiGraph()
        G.add_nodes_from(self._nodes.keys())
        for edge in self._edges:
            src = edge.get("source", "")
            tgt = edge.get("target", "")
            if src in self._nodes and tgt in self._nodes:
                G.add_edge(src, tgt, edge_id=edge["id"])
            elif src or tgt:
                self._warnings.append(
                    f"Edge '{edge.get('id')}' references unknown node(s) "
                    f"(source={src!r}, target={tgt!r}) — skipped"
                )
        return G

    def _collect_external_inlets(
        self,
        scc_members:     set[str],
        all_node_outlets: dict[str, list[Stream]],
    ) -> dict[str, list[Stream]]:
        """For each node in the SCC, collect inlet streams from upstream SCCs."""
        ext: dict[str, list[Stream]] = {}
        for edge in self._edges:
            tgt = edge.get("target", "")
            src = edge.get("source", "")
            if tgt in scc_members and src not in scc_members and src in all_node_outlets:
                outs = all_node_outlets[src]
                if outs:
                    out_idx = _parse_handle(edge.get("source_handle", "0"))
                    ext.setdefault(tgt, []).append(outs[min(out_idx, len(outs) - 1)])
        return ext

    # ── Tear stream selection ─────────────────────────────────────────────────

    def _select_tear_streams_for_scc(
        self,
        scc_members: set[str],
        scc_edges:   list[EdgeDict],
        G:           nx.DiGraph,
    ) -> list[str]:
        """Iteratively find back-edges and apply the selection heuristic until
        the SCC subgraph is acyclic."""
        remaining = list(scc_edges)
        selected:  list[str] = []

        while True:
            back_ids = self._find_back_edge_ids(scc_members, remaining)
            if not back_ids:
                break
            chosen = self._apply_tear_heuristic(back_ids, G)
            selected.append(chosen)
            remaining = [e for e in remaining if e["id"] != chosen]

        return selected

    def _find_back_edge_ids(
        self,
        node_ids: set[str],
        edges:    list[EdgeDict],
    ) -> list[str]:
        """Iterative DFS on the subgraph; returns IDs of back-edges."""
        adjacency: dict[str, list[tuple[str, str]]] = {nid: [] for nid in node_ids}
        for edge in edges:
            src = edge.get("source", "")
            tgt = edge.get("target", "")
            if src in node_ids and tgt in node_ids:
                adjacency[src].append((tgt, edge["id"]))

        WHITE, GRAY = 0, 1
        color:    dict[str, int] = {nid: WHITE for nid in node_ids}
        back_ids: list[str]      = []

        for start in node_ids:
            if color[start] != WHITE:
                continue
            path:     list[str]      = [start]
            in_path:  set[str]       = {start}
            iter_idx: dict[str, int] = {start: 0}
            color[start] = GRAY

            while path:
                node     = path[-1]
                neighbors = adjacency[node]
                idx      = iter_idx[node]
                if idx < len(neighbors):
                    iter_idx[node] = idx + 1
                    neighbor, edge_id = neighbors[idx]
                    if neighbor in in_path:
                        back_ids.append(edge_id)
                    elif color[neighbor] == WHITE:
                        color[neighbor] = GRAY
                        path.append(neighbor)
                        in_path.add(neighbor)
                        iter_idx[neighbor] = 0
                else:
                    path.pop()
                    in_path.discard(node)

        return back_ids

    def _apply_tear_heuristic(
        self,
        candidate_ids: list[str],
        G:             nx.DiGraph,
    ) -> str:
        """Select tear stream: smallest estimated molar flow, then highest
        source in-degree (higher in-degree → more constrained node)."""
        def key(eid: str) -> tuple[float, int]:
            edge  = self._edge_by_id.get(eid, {})
            flow  = self._estimate_tear_flow(edge)
            src   = edge.get("source", "")
            indeg = G.in_degree(src) if G.has_node(src) else 0
            return (flow, -indeg)

        return min(candidate_ids, key=key)

    def _estimate_tear_flow(self, edge: EdgeDict) -> float:
        """Return estimated molar flow for a potential tear stream.
        Uses a recycle-node estimate if one exists; otherwise defaults to 1.0."""
        for nid in [edge.get("source", ""), edge.get("target", "")]:
            node = self._nodes.get(nid, {})
            if node.get("type") == "recycle":
                est = node.get("data", {}).get("estimate", {})
                if est and "flow_mol_s" in est:
                    return float(est["flow_mol_s"])
        return 1.0

    # ── Recycle SCC solver ────────────────────────────────────────────────────

    def _solve_recycle_scc(
        self,
        scc_members:    set[str],
        scc_edges:      list[EdgeDict],
        tear_ids:       list[str],
        ext_inlets:     dict[str, list[Stream]],
        all_components: list[str],
    ) -> _LoopResult:
        """Wegstein-accelerated convergence for one recycle SCC.

        Implements the spec algorithm:
          - Direct substitution on iteration 0
          - Component-wise Wegstein from iteration 1
          - At iteration 50: if residual > 0.1, run 10 direct-substitution steps
            and restart Wegstein history (fallback mode)
          - ConvergenceError after _MAX_RECYCLE_ITER iterations
        """
        tear_id_set    = set(tear_ids)
        non_tear_edges = [e for e in scc_edges if e["id"] not in tear_id_set]
        tear_edges     = [e for e in scc_edges if e["id"] in tear_id_set]

        guesses: dict[str, Stream] = {
            e["id"]: self._initial_tear_guess(e, all_components) for e in tear_edges
        }

        # History: list of (x_k, g_k) where both are dicts eid→np.ndarray
        history: list[tuple[dict[str, np.ndarray], dict[str, np.ndarray]]] = []

        all_residuals: list[float] = []
        method_used    = "wegstein"
        converged      = False
        final_residual = 1.0
        iteration      = 0

        for k in range(_MAX_RECYCLE_ITER):
            iteration = k + 1

            node_outlets, node_summaries, _, pass_warnings = self._run_scc_pass(
                scc_members, non_tear_edges, ext_inlets, guesses
            )
            self._warnings.extend(pass_warnings)

            # Calculated values at the source end of each tear edge
            calc: dict[str, Stream] = {}
            for e in tear_edges:
                src_id  = e["source"]
                out_idx = _parse_handle(e.get("source_handle", "0"))
                if src_id in node_outlets and node_outlets[src_id]:
                    outs = node_outlets[src_id]
                    calc[e["id"]] = outs[min(out_idx, len(outs) - 1)]
                else:
                    calc[e["id"]] = guesses[e["id"]]

            x_k = {eid: _stream_to_vec(guesses[eid], all_components) for eid in tear_ids}
            g_k = {eid: _stream_to_vec(calc[eid],    all_components) for eid in tear_ids}

            # L2-norm relative residual (spec §3)
            diff_vec = np.concatenate([g_k[eid] - x_k[eid] for eid in tear_ids])
            ref_vec  = np.concatenate([x_k[eid]             for eid in tear_ids])
            final_residual = float(
                np.linalg.norm(diff_vec) / (np.linalg.norm(ref_vec) + 1e-10)
            )
            all_residuals.append(final_residual)

            if final_residual < _RECYCLE_TOL:
                converged = True
                break

            # ── Fallback at iteration _FALLBACK_ITER ─────────────────────
            if k == _FALLBACK_ITER and final_residual > _FALLBACK_RESID:
                method_used = "direct_substitution_fallback"
                current_g = {eid: g_k[eid].copy() for eid in tear_ids}
                for _ in range(_FALLBACK_STEPS):
                    fb_guesses = {
                        e["id"]: _vec_to_stream(
                            current_g[e["id"]], all_components,
                            guesses[e["id"]], guesses[e["id"]].name,
                        )
                        for e in tear_edges
                    }
                    fb_outlets, _, _, fb_warnings = self._run_scc_pass(
                        scc_members, non_tear_edges, ext_inlets, fb_guesses
                    )
                    self._warnings.extend(fb_warnings)
                    for e in tear_edges:
                        src_id  = e["source"]
                        out_idx = _parse_handle(e.get("source_handle", "0"))
                        if src_id in fb_outlets and fb_outlets[src_id]:
                            outs = fb_outlets[src_id]
                            fb_calc = outs[min(out_idx, len(outs) - 1)]
                            current_g[e["id"]] = _stream_to_vec(fb_calc, all_components)
                g_k = current_g
                history.clear()  # restart Wegstein

            # ── Wegstein / direct-substitution update ─────────────────────
            if history:
                x_prev, g_prev = history[-1]
                new_guesses = {
                    eid: _vec_to_stream(
                        _wegstein_update(x_k[eid], g_k[eid], x_prev[eid], g_prev[eid]),
                        all_components, guesses[eid], guesses[eid].name,
                    )
                    for eid in tear_ids
                }
            else:
                new_guesses = {
                    eid: _vec_to_stream(
                        g_k[eid], all_components, guesses[eid], guesses[eid].name
                    )
                    for eid in tear_ids
                }

            history.append((x_k, g_k))
            guesses = new_guesses

        if not converged:
            primary = tear_ids[0] if tear_ids else "unknown"
            raise ConvergenceError(
                f"Recycle loop failed to converge after {_MAX_RECYCLE_ITER} iterations. "
                f"Final residual: {final_residual:.2e}. "
                f"Tear stream: {primary}. "
                "Try providing better initial estimates for the recycle stream.",
                iterations=_MAX_RECYCLE_ITER,
                residuals=all_residuals,
            )

        if iteration > _SLOW_ITER:
            self._warnings.append(
                f"Recycle SCC converged slowly ({iteration} iterations). "
                "Consider providing initial estimates via a recycle node."
            )

        # One final clean pass for node_outlets and consistent summaries
        final_node_outlets, final_node_summaries, _, final_warnings = self._run_scc_pass(
            scc_members, non_tear_edges, ext_inlets, guesses
        )
        self._warnings.extend(final_warnings)

        primary = tear_ids[0] if tear_ids else "unknown"
        return _LoopResult(
            primary_tear_id  = primary,
            tear_ids         = list(tear_ids),
            iterations       = iteration,
            final_residual   = final_residual,
            method_used      = method_used,
            all_residuals    = all_residuals,
            node_outlets     = final_node_outlets,
            node_summaries   = final_node_summaries,
            tear_streams     = guesses,
        )

    # ── SCC pass (single sequential solve of all nodes in an SCC) ────────────

    def _run_scc_pass(
        self,
        scc_members:     set[str],
        non_tear_edges:  list[EdgeDict],
        ext_inlets:      dict[str, list[Stream]],
        assumed_streams: dict[str, Stream],
    ) -> tuple[dict[str, list[Stream]], dict[str, dict], dict[str, Stream], list[str]]:
        """Solve all nodes in ``scc_members`` once, in topological order.

        ``ext_inlets``      — pre-computed inlet streams from upstream SCCs
        ``assumed_streams`` — tear stream guesses injected at their targets
        """
        # Inject tear streams at their target nodes
        injected: dict[str, list[Stream]] = {}
        for edge in self._edges:
            if edge["id"] in assumed_streams:
                tgt = edge.get("target", "")
                if tgt in scc_members:
                    injected.setdefault(tgt, []).append(assumed_streams[edge["id"]])

        order = self._topological_sort_nodes(scc_members, non_tear_edges)

        node_outlets:   dict[str, list[Stream]]  = {}
        node_summaries: dict[str, dict[str, Any]] = {}
        edge_streams:   dict[str, Stream]          = {}
        pass_warnings:  list[str]                  = []

        for node_id in order:
            node      = self._nodes[node_id]
            node_type = node.get("type", "unknown")
            node_data = node.get("data", {})

            inlets: list[Stream] = []
            # External inlets from upstream SCCs
            inlets.extend(ext_inlets.get(node_id, []))
            # Inlets from already-solved nodes within this SCC pass
            for edge in non_tear_edges:
                if edge.get("target") == node_id:
                    src = edge.get("source", "")
                    if src in node_outlets and node_outlets[src]:
                        out_idx = _parse_handle(edge.get("source_handle", "0"))
                        outs = node_outlets[src]
                        inlets.append(outs[min(out_idx, len(outs) - 1)])
            # Injected tear stream(s)
            inlets.extend(injected.get(node_id, []))

            try:
                outlets, summary = self._solve_node(node_type, node_data, inlets, node_id)
            except SimulationError as exc:
                pass_warnings.append(f"Node '{node_id}' ({node_type}): {exc}")
                try:
                    outlets = [_zero_stream(f"{node_id}_err", inlets)]
                except SimulationError:
                    outlets = []
                summary = {"error": str(exc)}

            node_outlets[node_id]   = outlets
            node_summaries[node_id] = summary

            for edge in non_tear_edges:
                if edge.get("source") == node_id and outlets:
                    out_idx = _parse_handle(edge.get("source_handle", "0"))
                    edge_streams[edge["id"]] = outlets[min(out_idx, len(outlets) - 1)]

        return node_outlets, node_summaries, edge_streams, pass_warnings

    # ── Topological sort (node-set scoped) ────────────────────────────────────

    def _topological_sort_nodes(
        self,
        node_ids: set[str],
        edges:    list[EdgeDict],
    ) -> list[str]:
        """Kahn's BFS topological sort restricted to ``node_ids`` and ``edges``."""
        in_degree: dict[str, int]       = {nid: 0 for nid in node_ids}
        adjacency: dict[str, list[str]] = {nid: [] for nid in node_ids}

        for edge in edges:
            src = edge.get("source", "")
            tgt = edge.get("target", "")
            if src in node_ids and tgt in node_ids:
                adjacency[src].append(tgt)
                in_degree[tgt] += 1

        queue: deque[str] = deque(nid for nid, deg in in_degree.items() if deg == 0)
        order: list[str]  = []

        while queue:
            nid = queue.popleft()
            order.append(nid)
            for downstream in adjacency[nid]:
                in_degree[downstream] -= 1
                if in_degree[downstream] == 0:
                    queue.append(downstream)

        if len(order) != len(node_ids):
            remaining = [nid for nid, deg in in_degree.items() if deg > 0]
            raise SimulationError(
                f"SCC subgraph is still cyclic after removing tear streams: {remaining}. "
                "This is a bug — please report it."
            )
        return order

    # ── Initial tear stream estimates ─────────────────────────────────────────

    def _initial_tear_guess(
        self, edge: EdgeDict, all_components: list[str]
    ) -> Stream:
        name = f"tear_{edge['id']}"

        for node_id in [edge.get("source", ""), edge.get("target", "")]:
            node = self._nodes.get(node_id, {})
            if node.get("type") == "recycle":
                est = node.get("data", {}).get("estimate", {})
                if est:
                    comp: dict[str, float] = dict(est.get("composition", {}))
                    if comp:
                        total = sum(comp.values())
                        if total > 1e-15:
                            comp = {k: v / total for k, v in comp.items()}
                        return Stream(
                            name=name,
                            temperature=float(est.get("temperature_C", 25.0)),
                            pressure=float(est.get("pressure_bar", 1.0)),
                            flow=float(est.get("flow_mol_s", 1.0)),
                            composition=comp,
                            vapor_fraction=float(est.get("vapor_fraction", 0.0)),
                        )

        n = max(len(all_components), 1)
        if not all_components:
            return Stream(name, 25.0, 1.0, 1.0, {"component": 1.0}, 0.0)
        composition = {c: 1.0 / n for c in all_components}
        return Stream(name, 25.0, 1.0, 1.0, composition, 0.0)

    def _gather_all_components(self) -> list[str]:
        """Union of all component IDs across the entire flowsheet."""
        components: set[str] = set()
        for node in self._nodes.values():
            data = node.get("data", {})
            components.update(data.get("composition", {}).keys())
            components.update(data.get("stoichiometry", {}).keys())
        return sorted(components)

    # ── Unit-op dispatch ──────────────────────────────────────────────────────

    def _solve_node(
        self,
        node_type: str,
        data:      dict[str, Any],
        inlets:    list[Stream],
        node_id:   str,
    ) -> tuple[list[Stream], dict[str, Any]]:

        if node_type == "feed":
            return self._make_feed(data, node_id)

        if node_type == "product":
            if not inlets:
                self._warnings.append(f"Product node '{node_id}' has no inlet stream")
                return [], {}
            s = inlets[0]
            return [s], {
                "inlet_flow_mol_s":    s.flow,
                "inlet_temperature_C": s.temperature,
                "inlet_pressure_bar":  s.pressure,
            }

        if node_type == "recycle":
            if not inlets:
                return [], {}
            return [inlets[0]], {"passthrough": True}

        if node_type == "mixer":
            return Mixer().solve(inlets, outlet_name=f"{node_id}_out")

        if node_type == "splitter":
            fractions = data.get("fractions", [0.5, 0.5])
            return Splitter().solve(inlets, fractions=fractions, outlet_names=None)

        if node_type == "heat_exchanger":
            mode     = data.get("mode", "duty")
            duty_raw = data.get("duty_W") or (data.get("duty_kW", 0.0) * 1000.0)
            return HeatExchanger().solve(
                inlets,
                mode=mode,
                duty_W=float(duty_raw) if duty_raw is not None else None,
                outlet_temp_C=(
                    float(data["outlet_temp_C"])
                    if data.get("outlet_temp_C") is not None else None
                ),
                outlet_name=f"{node_id}_out",
            )

        if node_type == "pfr":
            return PFR().solve(
                inlets,
                stoichiometry=data.get("stoichiometry", {}),
                conversion=float(data.get("conversion", 0.8)),
                delta_Hrxn_J_mol=float(data.get("delta_Hrxn_J_mol", 0.0)),
                outlet_name=f"{node_id}_out",
            )

        if node_type in ("flash_drum", "flash"):
            return Flash().solve(
                inlets,
                temperature_C=(
                    float(data["temperature_C"])
                    if data.get("temperature_C") is not None else None
                ),
                pressure_bar=(
                    float(data["pressure_bar"])
                    if data.get("pressure_bar") is not None else None
                ),
                liquid_name=f"{node_id}_liquid",
                vapor_name=f"{node_id}_vapor",
            )

        if node_type == "pump":
            return Pump().solve(
                inlets,
                delta_P_bar=float(data.get("delta_P_bar", 1.0)),
                efficiency=float(data.get("efficiency", 0.75)),
                outlet_name=f"{node_id}_out",
            )

        if node_type == "cstr":
            return CSTR().solve(
                inlets,
                volume_L=float(data.get("volume_L", 100.0)),
                temperature_C=float(data.get("temperature_C", 76.85)),
                coolant_temp_K=float(data.get("coolant_temp_K", 300.0)),
                pre_exponential=float(data.get("pre_exponential", 7.2e10 / 60.0)),
                activation_energy_J_mol=float(data.get("activation_energy_J_mol", 72681.0)),
                outlet_name=f"{node_id}_out",
            )

        self._warnings.append(
            f"Unknown node type '{node_type}' for node '{node_id}' — skipped"
        )
        return inlets, {"skipped": True, "type": node_type}

    @staticmethod
    def _make_feed(
        data: dict[str, Any], node_id: str
    ) -> tuple[list[Stream], dict[str, Any]]:
        composition: dict[str, float] = data.get("composition", {})
        if not composition:
            raise SimulationError(
                f"Feed node '{node_id}' has no 'composition' in its data dict"
            )
        composition = resolve_composition(composition)
        try:
            stream = Stream(
                name=data.get("label", f"feed_{node_id}"),
                temperature=float(data.get("temperature_C", 25.0)),
                pressure=float(data.get("pressure_bar", 1.0)),
                flow=float(data.get("flow_mol_s", 1.0)),
                composition=composition,
                vapor_fraction=float(data.get("vapor_fraction", 0.0)),
            )
        except (KeyError, ValueError, TypeError) as exc:
            raise SimulationError(
                f"Feed node '{node_id}' data error: {exc}"
            ) from exc

        return [stream], {
            "flow_mol_s":    stream.flow,
            "temperature_C": stream.temperature,
            "pressure_bar":  stream.pressure,
            "composition":   dict(stream.composition),
        }


# ── Module-level helpers ──────────────────────────────────────────────────────

def _parse_handle(raw: Any) -> int:
    try:
        return int(raw)
    except (TypeError, ValueError):
        return 0


def _zero_stream(name: str, inlets: list[Stream]) -> Stream:
    if not inlets or not inlets[0].composition:
        raise SimulationError(
            f"Cannot create placeholder stream '{name}': no inlet stream available"
        )
    src = inlets[0]
    return Stream(name, src.temperature, src.pressure, 0.0, dict(src.composition), 0.0)


def _aggregate_energy(node_summaries: dict[str, dict[str, Any]]) -> dict[str, float]:
    total_duty = heating = cooling = 0.0
    for smry in node_summaries.values():
        q = smry.get("duty_W") or smry.get("heat_released_W") or 0.0
        total_duty += q
        if q > 0:
            heating += q
        else:
            cooling += abs(q)
    return {
        "total_duty_kW": total_duty / 1000.0,
        "heating_kW":    heating    / 1000.0,
        "cooling_kW":    cooling    / 1000.0,
    }


def _stream_to_vec(stream: Stream, components: list[str]) -> np.ndarray:
    """Flatten a Stream to a 1-D array: [T, P, F, x_comp0, x_comp1, ...]."""
    fracs = [stream.composition.get(c, 0.0) for c in components]
    return np.array([stream.temperature, stream.pressure, stream.flow] + fracs,
                    dtype=float)


def _vec_to_stream(
    vec:        np.ndarray,
    components: list[str],
    template:   Stream,
    name:       str,
) -> Stream:
    """Reconstruct a Stream from a state vector, renormalising composition."""
    T = float(vec[0])
    P = float(vec[1])
    F = max(float(vec[2]), 0.0)

    fracs = np.maximum(vec[3:], 0.0)
    total = fracs.sum()
    fracs = fracs / total if total > 1e-15 else np.ones(len(fracs)) / max(len(fracs), 1)

    return Stream(name, T, P, F, dict(zip(components, fracs.tolist())),
                  template.vapor_fraction)


def _wegstein_update(
    x_k:    np.ndarray,
    g_k:    np.ndarray,
    x_prev: np.ndarray,
    g_prev: np.ndarray,
) -> np.ndarray:
    """Component-wise Wegstein acceleration.

    q_i = s_i / (s_i − 1),   s_i = Δg_i / Δx_i
    q_i clamped to [−5, 0].  Falls back to successive substitution when Δx_i ≈ 0.
    """
    dx     = x_k - x_prev
    dg     = g_k - g_prev
    result = np.empty_like(x_k)

    with np.errstate(divide="ignore", invalid="ignore"):
        s = np.where(np.abs(dx) > 1e-12, dg / dx, np.inf)
        q = np.where(np.isfinite(s), s / (s - 1.0), 0.0)
        q = np.clip(q, _WEGSTEIN_Q_MIN, _WEGSTEIN_Q_MAX)
        result = q * x_k + (1.0 - q) * g_k

    return result
