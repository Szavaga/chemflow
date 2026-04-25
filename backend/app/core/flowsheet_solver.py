"""
FlowsheetSolver — steady-state solver for ChemFlow flowsheets.

Supports both acyclic (feed-forward) and cyclic (recycle) flowsheets.

Acyclic path
------------
Nodes are solved in Kahn's BFS topological order.

Recycle path
------------
1. DFS detects back-edges; each becomes a "tear stream".
2. Tear streams are initialised from ``recycle`` node estimates or defaults
   (T=25 °C, P=1 bar, equal molar fractions of all components in the graph).
3. Wegstein-accelerated successive substitution iterates until every tear
   stream variable (T, P, flow, xi) changes by less than 1 × 10⁻⁴ relative.
4. ``ConvergenceError`` is raised after 100 iterations with full diagnostics.

Return value
------------
``FlowsheetSolver.solve()`` returns::

    {
        "streams"        : {edge_id: stream_dict, ...},
        "node_summaries" : {node_id: summary_dict, ...},
        "energy_balance" : {"total_duty_kW": ..., "heating_kW": ..., "cooling_kW": ...},
        "warnings"       : [str, ...],
        "converged"      : bool,
        "convergence_info": {
            "converged"    : bool,
            "iterations"   : int,
            "tear_streams" : [edge_id, ...],
            "residuals"    : [float, ...],
        },
    }

Recycle node
------------
A node of type ``recycle`` acts as a visual passthrough marker (1 in, 1 out).
Its ``data`` dict may contain an ``"estimate"`` sub-dict with optional keys
``temperature_C``, ``pressure_bar``, ``flow_mol_s``, ``vapor_fraction``, and
``composition`` to seed the initial tear-stream guess.
"""

from __future__ import annotations

from collections import deque
from typing import Any

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

NodeDict = dict[str, Any]
EdgeDict = dict[str, Any]

_MAX_RECYCLE_ITER = 100
_RECYCLE_TOL      = 1e-4
_SLOW_ITER        = 50
_WEGSTEIN_Q_MIN   = -5.0
_WEGSTEIN_Q_MAX   = 0.0


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
        self._nodes: dict[str, NodeDict] = {n["id"]: n for n in nodes}
        self._edges: list[EdgeDict] = edges
        self._warnings: list[str] = []

    # ── public ────────────────────────────────────────────────────────────────

    def solve(self) -> dict[str, Any]:
        """Solve the flowsheet and return a result dict.

        Automatically detects recycle loops and applies Wegstein convergence.
        Raises ``ConvergenceError`` if a recycle loop does not converge.
        Per-node errors in acyclic solves are captured as warnings.
        """
        tear_ids = self._find_tear_streams()

        if not tear_ids:
            result = self._solve_acyclic()
            result["convergence_info"] = {
                "converged": True,
                "iterations": 0,
                "tear_streams": [],
                "residuals": [],
            }
            return result

        return self._solve_with_recycle(tear_ids)

    # ── acyclic path ──────────────────────────────────────────────────────────

    def _solve_acyclic(self) -> dict[str, Any]:
        order = self._topological_sort_edges(self._edges)

        node_outlets: dict[str, list[Stream]] = {}
        node_summaries: dict[str, dict[str, Any]] = {}
        edge_streams: dict[str, Stream] = {}
        converged = True

        for node_id in order:
            node      = self._nodes[node_id]
            node_type = node.get("type", "unknown")
            node_data = node.get("data", {})
            inlets    = self._collect_inlets_from(node_id, node_outlets, self._edges)

            try:
                outlets, summary = self._solve_node(node_type, node_data, inlets, node_id)
            except SimulationError as exc:
                self._warnings.append(f"Node '{node_id}' ({node_type}): {exc}")
                converged = False
                try:
                    outlets = [_zero_stream(f"{node_id}_err", inlets)]
                except SimulationError:
                    outlets = []
                summary = {"error": str(exc)}

            node_outlets[node_id]   = outlets
            node_summaries[node_id] = summary

            for edge in self._edges:
                if edge.get("source") == node_id and outlets:
                    out_idx = _parse_handle(edge.get("source_handle", "0"))
                    edge_streams[edge["id"]] = outlets[min(out_idx, len(outlets) - 1)]

        return {
            "streams":         {k: v.to_dict() for k, v in edge_streams.items()},
            "node_summaries":  node_summaries,
            "energy_balance":  _aggregate_energy(node_summaries),
            "warnings":        self._warnings,
            "converged":       converged,
        }

    # ── recycle path ──────────────────────────────────────────────────────────

    def _solve_with_recycle(self, tear_ids: list[str]) -> dict[str, Any]:
        tear_id_set   = set(tear_ids)
        non_tear_edges = [e for e in self._edges if e["id"] not in tear_id_set]
        tear_edges     = [e for e in self._edges if e["id"] in tear_id_set]

        all_components = self._gather_all_components()

        guesses: dict[str, Stream] = {
            e["id"]: self._initial_tear_guess(e, all_components) for e in tear_edges
        }

        x_prev: dict[str, np.ndarray] | None = None
        g_prev: dict[str, np.ndarray] | None = None

        converged_flag = False
        iterations     = 0
        all_residuals: list[float] = []

        for k in range(_MAX_RECYCLE_ITER):
            iterations = k + 1

            node_outlets, node_summaries, edge_streams, pass_warnings = self._run_pass(
                non_tear_edges, guesses
            )

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

            x_k = {eid: _stream_to_vec(guesses[eid], all_components) for eid in tear_id_set}
            g_k = {eid: _stream_to_vec(calc[eid],    all_components) for eid in tear_id_set}

            residuals = {
                eid: float(np.max(
                    np.abs(g_k[eid] - x_k[eid]) / np.maximum(np.abs(x_k[eid]), 1e-6)
                ))
                for eid in tear_id_set
            }
            max_res = max(residuals.values()) if residuals else 0.0
            all_residuals.append(max_res)

            if max_res < _RECYCLE_TOL:
                converged_flag = True
                self._warnings.extend(pass_warnings)
                break

            # Wegstein (successive substitution on the first iteration)
            new_guesses: dict[str, Stream] = {}
            for eid in tear_id_set:
                if x_prev is None or g_prev is None:
                    new_vec = g_k[eid]
                else:
                    new_vec = _wegstein_update(x_k[eid], g_k[eid], x_prev[eid], g_prev[eid])
                new_guesses[eid] = _vec_to_stream(
                    new_vec, all_components, guesses[eid], guesses[eid].name
                )

            x_prev  = x_k
            g_prev  = g_k
            guesses = new_guesses

        if not converged_flag:
            raise ConvergenceError(
                f"Recycle convergence failed after {_MAX_RECYCLE_ITER} iterations. "
                f"Tear stream(s): {sorted(tear_id_set)}. "
                f"Final max relative residual: {all_residuals[-1]:.4e}. "
                "Provide better initial estimates via a recycle node.",
                iterations=_MAX_RECYCLE_ITER,
                residuals=all_residuals,
            )

        if iterations > _SLOW_ITER:
            self._warnings.append(
                f"Recycle convergence was slow ({iterations} iterations). "
                "Consider providing initial estimates via a recycle node."
            )

        # Final pass with converged guesses to build complete edge_streams
        _, final_summaries, final_edge_streams, final_warnings = self._run_pass(
            non_tear_edges, guesses
        )
        self._warnings.extend(final_warnings)

        for e in tear_edges:
            final_edge_streams[e["id"]] = guesses[e["id"]]

        return {
            "streams":        {k: v.to_dict() for k, v in final_edge_streams.items()},
            "node_summaries": final_summaries,
            "energy_balance": _aggregate_energy(final_summaries),
            "warnings":       self._warnings,
            "converged":      True,
            "convergence_info": {
                "converged":    True,
                "iterations":   iterations,
                "tear_streams": sorted(tear_id_set),
                "residuals":    all_residuals,
            },
        }

    def _run_pass(
        self,
        edges: list[EdgeDict],
        assumed_streams: dict[str, Stream],
    ) -> tuple[dict[str, list[Stream]], dict[str, dict], dict[str, Stream], list[str]]:
        """Single sequential solve pass using ``edges`` plus injected tear streams."""
        assumed_ids = set(assumed_streams)

        # Which nodes receive an injected tear stream?
        injected: dict[str, list[Stream]] = {}
        for edge in self._edges:
            if edge["id"] in assumed_ids:
                tgt = edge.get("target", "")
                if tgt in self._nodes:
                    injected.setdefault(tgt, []).append(assumed_streams[edge["id"]])

        order = self._topological_sort_edges(edges)

        node_outlets:   dict[str, list[Stream]]  = {}
        node_summaries: dict[str, dict[str, Any]] = {}
        edge_streams:   dict[str, Stream]         = {}
        pass_warnings:  list[str]                 = []

        for node_id in order:
            node      = self._nodes[node_id]
            node_type = node.get("type", "unknown")
            node_data = node.get("data", {})

            inlets = self._collect_inlets_from(node_id, node_outlets, edges)
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

            for edge in edges:
                if edge.get("source") == node_id and outlets:
                    out_idx = _parse_handle(edge.get("source_handle", "0"))
                    edge_streams[edge["id"]] = outlets[min(out_idx, len(outlets) - 1)]

        return node_outlets, node_summaries, edge_streams, pass_warnings

    # ── graph analysis ────────────────────────────────────────────────────────

    def _find_tear_streams(self) -> list[str]:
        """Iterative DFS; returns edge IDs of back-edges (tear streams)."""
        adjacency: dict[str, list[tuple[str, str]]] = {nid: [] for nid in self._nodes}
        for edge in self._edges:
            src = edge.get("source", "")
            tgt = edge.get("target", "")
            if src in self._nodes and tgt in self._nodes:
                adjacency[src].append((tgt, edge["id"]))
            elif src or tgt:
                self._warnings.append(
                    f"Edge '{edge.get('id')}' references unknown node(s) "
                    f"(source={src!r}, target={tgt!r}) — skipped"
                )

        WHITE, GRAY = 0, 1
        color: dict[str, int] = {nid: WHITE for nid in self._nodes}
        tear_edge_ids: list[str] = []

        for start in self._nodes:
            if color[start] != WHITE:
                continue
            # Iterative DFS with explicit path tracking
            path:     list[str]       = [start]
            in_path:  set[str]        = {start}
            iter_idx: dict[str, int]  = {start: 0}
            color[start] = GRAY

            while path:
                node = path[-1]
                neighbors = adjacency[node]
                idx = iter_idx[node]

                if idx < len(neighbors):
                    iter_idx[node] = idx + 1
                    neighbor, edge_id = neighbors[idx]

                    if neighbor in in_path:
                        tear_edge_ids.append(edge_id)
                    elif color[neighbor] == WHITE:
                        color[neighbor] = GRAY
                        path.append(neighbor)
                        in_path.add(neighbor)
                        iter_idx[neighbor] = 0
                else:
                    path.pop()
                    in_path.discard(node)

        return tear_edge_ids

    def _topological_sort_edges(self, edges: list[EdgeDict]) -> list[str]:
        """Kahn's BFS topological sort over the given edge subset."""
        in_degree: dict[str, int]       = {nid: 0 for nid in self._nodes}
        adjacency: dict[str, list[str]] = {nid: [] for nid in self._nodes}

        for edge in edges:
            src = edge.get("source", "")
            tgt = edge.get("target", "")
            if src in self._nodes and tgt in self._nodes:
                adjacency[src].append(tgt)
                in_degree[tgt] += 1

        queue: deque[str] = deque(nid for nid, deg in in_degree.items() if deg == 0)
        order: list[str] = []

        while queue:
            nid = queue.popleft()
            order.append(nid)
            for downstream in adjacency[nid]:
                in_degree[downstream] -= 1
                if in_degree[downstream] == 0:
                    queue.append(downstream)

        if len(order) != len(self._nodes):
            remaining = [nid for nid, deg in in_degree.items() if deg > 0]
            raise SimulationError(
                f"Graph still contains a cycle after removing tear streams: {remaining}. "
                "This is a bug — please report it."
            )

        return order

    def _collect_inlets_from(
        self,
        node_id: str,
        node_outlets: dict[str, list[Stream]],
        edges: list[EdgeDict],
    ) -> list[Stream]:
        inlets: list[Stream] = []
        for edge in edges:
            if edge.get("target") == node_id:
                src_id = edge.get("source", "")
                if src_id in node_outlets and node_outlets[src_id]:
                    out_idx = _parse_handle(edge.get("source_handle", "0"))
                    out = node_outlets[src_id]
                    inlets.append(out[min(out_idx, len(out) - 1)])
        return inlets

    # ── initial tear stream estimates ─────────────────────────────────────────

    def _initial_tear_guess(
        self, edge: EdgeDict, all_components: list[str]
    ) -> Stream:
        name = f"tear_{edge['id']}"

        # Look for a recycle node on either end of the tear edge
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

        # Default: equal molar fractions of every component in the flowsheet
        n = len(all_components)
        if n == 0:
            all_components = ["component"]
            n = 1
        composition = {c: 1.0 / n for c in all_components}
        return Stream(name=name, temperature=25.0, pressure=1.0, flow=1.0,
                      composition=composition, vapor_fraction=0.0)

    def _gather_all_components(self) -> list[str]:
        """Union of all component IDs appearing in feed compositions and
        reaction stoichiometries across the entire flowsheet."""
        components: set[str] = set()
        for node in self._nodes.values():
            data = node.get("data", {})
            components.update(data.get("composition", {}).keys())
            components.update(data.get("stoichiometry", {}).keys())
        return sorted(components)

    # ── unit-op dispatch ──────────────────────────────────────────────────────

    def _solve_node(
        self,
        node_type: str,
        data: dict[str, Any],
        inlets: list[Stream],
        node_id: str,
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

        summary: dict[str, Any] = {
            "flow_mol_s":    stream.flow,
            "temperature_C": stream.temperature,
            "pressure_bar":  stream.pressure,
            "composition":   dict(stream.composition),
        }
        return [stream], summary


# ── module-level helpers ──────────────────────────────────────────────────────

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
    """Flatten a Stream into a 1-D numpy array: [T, P, F, x0, x1, ...]."""
    fracs = [stream.composition.get(c, 0.0) for c in components]
    return np.array([stream.temperature, stream.pressure, stream.flow] + fracs,
                    dtype=float)


def _vec_to_stream(
    vec: np.ndarray,
    components: list[str],
    template: Stream,
    name: str,
) -> Stream:
    """Reconstruct a Stream from a state vector, renormalising composition."""
    T = float(vec[0])
    P = float(vec[1])
    F = max(float(vec[2]), 0.0)

    fracs = np.maximum(vec[3:], 0.0)
    total = fracs.sum()
    if total < 1e-15:
        fracs = np.ones(len(fracs)) / max(len(fracs), 1)
    else:
        fracs /= total

    composition = dict(zip(components, fracs.tolist()))
    return Stream(name, T, P, F, composition, template.vapor_fraction)


def _wegstein_update(
    x_k: np.ndarray,
    g_k: np.ndarray,
    x_prev: np.ndarray,
    g_prev: np.ndarray,
) -> np.ndarray:
    """Component-wise Wegstein acceleration.

    q_i = s_i / (s_i − 1),  s_i = Δg_i / Δx_i
    q_i clamped to [−5, 0] for stability.
    Falls back to successive substitution when Δx_i ≈ 0.
    """
    dx = x_k - x_prev
    dg = g_k - g_prev
    result = np.empty_like(x_k)

    for i in range(len(x_k)):
        if abs(dx[i]) < 1e-12:
            result[i] = g_k[i]
        else:
            s = dg[i] / dx[i]
            q = s / (s - 1.0)
            q = float(np.clip(q, _WEGSTEIN_Q_MIN, _WEGSTEIN_Q_MAX))
            result[i] = q * x_k[i] + (1.0 - q) * g_k[i]

    return result
