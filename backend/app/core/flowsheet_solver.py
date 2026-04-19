"""
FlowsheetSolver — topological steady-state solver for ChemFlow flowsheets.

Takes the node/edge graph stored in the ``Flowsheet`` ORM model and solves each
unit operation in topological order (Kahn's algorithm), propagating Stream
objects through the graph.

Node types
----------
  feed            source stream — no inlets; reads T/P/flow/composition from data
  mixer           Mixer unit op
  splitter        Splitter unit op; reads ``fractions`` list from data
  heat_exchanger  HeatExchanger (heater/cooler); reads ``mode``, ``duty_W``,
                  or ``outlet_temp_C`` from data
  pfr             PFR unit op; reads ``stoichiometry``, ``conversion``,
                  ``delta_Hrxn_J_mol`` from data
  flash_drum      Flash VLE; reads optional ``temperature_C`` / ``pressure_bar``
  pump            Pump; reads ``delta_P_bar``, ``efficiency`` from data
  product         sink — records the inlet stream, passes it through unchanged

Multi-outlet nodes (Flash, Splitter)
-------------------------------------
Edges leaving a multi-outlet node should carry a ``source_handle`` field whose
integer value selects the outlet index:
  flash_drum  → source_handle "0" = liquid,  "1" = vapour
  splitter    → source_handle "0" = first fraction, "1" = second, …

If ``source_handle`` is absent the implementation defaults to "0", which will
map all edges to the first outlet.

Return value
------------
``FlowsheetSolver.solve()`` returns a dict compatible with the
``SimulationResult`` ORM model fields::

    {
        "streams"       : {edge_id: stream_dict, ...},
        "node_summaries": {node_id: summary_dict, ...},
        "energy_balance": {"total_duty_kW": ..., "heating_kW": ..., "cooling_kW": ...},
        "warnings"      : [str, ...],
        "converged"     : bool,
    }
"""

from __future__ import annotations

from collections import deque
from typing import Any

from app.core.unit_ops import (
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

_FALLBACK_COMPOSITION = {"water": 1.0}
_FALLBACK_T, _FALLBACK_P = 25.0, 1.0


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
        """
        Solve the flowsheet sequentially and return a result dict.

        Raises ``SimulationError`` only if the graph contains a cycle (acyclic
        graphs are required).  Per-node errors are captured as warnings and a
        zero-flow placeholder stream propagates downstream so the solver can
        continue.
        """
        order = self._topological_sort()

        # node_id → list[Stream]  (outlets produced by that node)
        node_outlets: dict[str, list[Stream]] = {}
        node_summaries: dict[str, dict[str, Any]] = {}

        # edge_id → Stream
        edge_streams: dict[str, Stream] = {}

        converged = True

        for node_id in order:
            node      = self._nodes[node_id]
            node_type = node.get("type", "unknown")
            node_data = node.get("data", {})

            inlets = self._collect_inlets(node_id, node_outlets)

            try:
                outlets, summary = self._solve_node(
                    node_type, node_data, inlets, node_id
                )
            except SimulationError as exc:
                self._warnings.append(
                    f"Node '{node_id}' ({node_type}): {exc}"
                )
                converged = False
                outlets  = [_zero_stream(f"{node_id}_err", inlets)]
                summary  = {"error": str(exc)}

            node_outlets[node_id]   = outlets
            node_summaries[node_id] = summary

            # Register each outlet stream under the id of its leaving edge.
            # ``source_handle`` (int-as-string) selects the outlet index.
            for edge in self._edges:
                if edge.get("source") == node_id:
                    out_idx = _parse_handle(edge.get("source_handle", "0"))
                    out_idx = min(out_idx, len(outlets) - 1)
                    edge_streams[edge["id"]] = outlets[out_idx]

        # --- aggregate energy balance ----------------------------------------
        total_duty = heating = cooling = 0.0
        for smry in node_summaries.values():
            q = smry.get("duty_W") or smry.get("heat_released_W") or 0.0
            total_duty += q
            (heating if q > 0 else cooling).__add__   # keep linter happy
            if q > 0:
                heating += q
            else:
                cooling += abs(q)

        return {
            "streams": {k: v.to_dict() for k, v in edge_streams.items()},
            "node_summaries": node_summaries,
            "energy_balance": {
                "total_duty_kW": total_duty / 1000.0,
                "heating_kW":    heating    / 1000.0,
                "cooling_kW":    cooling    / 1000.0,
            },
            "warnings":  self._warnings,
            "converged": converged,
        }

    # ── private helpers ───────────────────────────────────────────────────────

    def _topological_sort(self) -> list[str]:
        """Kahn's BFS topological sort.  Raises SimulationError on cycles."""
        in_degree:  dict[str, int]        = {nid: 0 for nid in self._nodes}
        adjacency:  dict[str, list[str]]  = {nid: [] for nid in self._nodes}

        for edge in self._edges:
            src = edge.get("source", "")
            tgt = edge.get("target", "")
            if src not in self._nodes or tgt not in self._nodes:
                self._warnings.append(
                    f"Edge '{edge.get('id')}' references unknown node(s) "
                    f"(source={src!r}, target={tgt!r}) — skipped"
                )
                continue
            adjacency[src].append(tgt)
            in_degree[tgt] += 1

        queue: deque[str] = deque(
            nid for nid, deg in in_degree.items() if deg == 0
        )
        order: list[str] = []

        while queue:
            nid = queue.popleft()
            order.append(nid)
            for downstream in adjacency[nid]:
                in_degree[downstream] -= 1
                if in_degree[downstream] == 0:
                    queue.append(downstream)

        if len(order) != len(self._nodes):
            cycle_nodes = [nid for nid, deg in in_degree.items() if deg > 0]
            raise SimulationError(
                f"Flowsheet contains a cycle involving nodes: {cycle_nodes}. "
                "ChemFlow currently solves only acyclic (feed-forward) flowsheets."
            )

        return order

    def _collect_inlets(
        self,
        node_id: str,
        node_outlets: dict[str, list[Stream]],
    ) -> list[Stream]:
        """Gather all Streams that arrive at ``node_id`` via edges."""
        inlets: list[Stream] = []
        for edge in self._edges:
            if edge.get("target") == node_id:
                src_id = edge.get("source", "")
                if src_id in node_outlets:
                    out_idx = _parse_handle(edge.get("source_handle", "0"))
                    outlets = node_outlets[src_id]
                    out_idx = min(out_idx, len(outlets) - 1)
                    inlets.append(outlets[out_idx])
        return inlets

    def _solve_node(
        self,
        node_type: str,
        data: dict[str, Any],
        inlets: list[Stream],
        node_id: str,
    ) -> tuple[list[Stream], dict[str, Any]]:
        """Dispatch to the correct unit op."""

        # -- source / sink ----------------------------------------------------
        if node_type == "feed":
            return self._make_feed(data, node_id)

        if node_type == "product":
            if not inlets:
                self._warnings.append(
                    f"Product node '{node_id}' has no inlet stream"
                )
                return [], {}
            s = inlets[0]
            return [s], {
                "inlet_flow_mol_s":    s.flow,
                "inlet_temperature_C": s.temperature,
                "inlet_pressure_bar":  s.pressure,
            }

        # -- unit operations --------------------------------------------------
        if node_type == "mixer":
            return Mixer().solve(inlets, outlet_name=f"{node_id}_out")

        if node_type == "splitter":
            fractions = data.get("fractions", [0.5, 0.5])
            return Splitter().solve(
                inlets, fractions=fractions, outlet_names=None
            )

        if node_type == "heat_exchanger":
            mode = data.get("mode", "duty")
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

        # -- unknown type -----------------------------------------------------
        self._warnings.append(
            f"Unknown node type '{node_type}' for node '{node_id}' — skipped"
        )
        return inlets, {"skipped": True, "type": node_type}

    # -------------------------------------------------------------------------

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
    """Convert a source_handle value to an integer outlet index."""
    try:
        return int(raw)
    except (TypeError, ValueError):
        return 0


def _zero_stream(name: str, inlets: list[Stream]) -> Stream:
    """Return a zero-flow placeholder that inherits composition from the first
    inlet (used for error recovery so downstream nodes receive a valid Stream)."""
    if inlets and inlets[0].composition:
        comp = dict(inlets[0].composition)
        T, P = inlets[0].temperature, inlets[0].pressure
    else:
        comp, T, P = _FALLBACK_COMPOSITION, _FALLBACK_T, _FALLBACK_P
    return Stream(name, T, P, 0.0, comp, 0.0)
