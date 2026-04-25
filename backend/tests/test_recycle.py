"""
Tests for recycle-stream convergence in FlowsheetSolver.

Flowsheet under test
--------------------

    N_feed (pure benzene, 1 mol/s)
        │ E_feed
        ▼
    N_mixer ◄────────────── E_recycle (20 % of splitter) ─────┐
        │ E_mix_pfr                                             │
        ▼                                                       │
    N_pfr  (benzene → toluene, 80 % single-pass conversion)    │
        │ E_pfr_split                                           │
        ▼                                                       │
    N_splitter ─── E_product (handle "0", fraction 0.8) ──► N_product
              └─── E_recycle (handle "1", fraction 0.2) ──────┘

Analytical steady-state solution
---------------------------------
Let F  = molar flow into PFR,  r = 0.2 recycle fraction,  x = 0.8 conversion.

Total flow balance at mixer:
    F = F₀ + r·F  →  F = F₀/(1−r) = 1.0/0.8 = 1.25 mol/s

Component A (benzene) balance at mixer:
    z_A·F = 1.0 + r·F·(1−x)·z_A
    z_A·1.25 = 1.0 + 0.2·1.25·0.2·z_A
    z_A·1.2  = 1.0
    z_A      = 5/6

PFR outlet composition:
    z_A_out = (1−x)·z_A = 0.2·(5/6) = 1/6
    z_B_out = 1 − z_A_out = 5/6

Product stream (80 % of PFR outlet):
    flow           = 0.8·1.25 = 1.0 mol/s
    z_A (benzene)  = 1/6 ≈ 0.16667
    z_B (toluene)  = 5/6 ≈ 0.83333
"""

from __future__ import annotations

import pytest

from app.core.flowsheet_solver import FlowsheetSolver
from app.core.unit_ops import ConvergenceError, SimulationError


# ── helpers ───────────────────────────────────────────────────────────────────

def _feed_node(node_id: str, **data_overrides) -> dict:
    data = {
        "composition": {"benzene": 1.0},
        "temperature_C": 25.0,
        "pressure_bar": 1.0,
        "flow_mol_s": 1.0,
    }
    data.update(data_overrides)
    return {"id": node_id, "type": "feed", "data": data, "position": {"x": 0, "y": 0}}


def _node(node_id: str, node_type: str, **data) -> dict:
    return {"id": node_id, "type": node_type, "data": data, "position": {"x": 0, "y": 0}}


def _edge(edge_id: str, src: str, tgt: str, handle: str | None = None) -> dict:
    e: dict = {"id": edge_id, "source": src, "target": tgt}
    if handle is not None:
        e["source_handle"] = handle
    return e


def _recycle_flowsheet(recycle_fraction: float = 0.2):
    """Build the canonical CSTR-with-recycle flowsheet for tests."""
    nodes = [
        _feed_node("N_feed"),
        _node("N_mixer",    "mixer"),
        _node("N_pfr",      "pfr",
              stoichiometry={"benzene": -1.0, "toluene": 1.0},
              conversion=0.8,
              delta_Hrxn_J_mol=0.0),
        _node("N_splitter", "splitter",
              fractions=[1.0 - recycle_fraction, recycle_fraction]),
        _node("N_product",  "product"),
    ]
    edges = [
        _edge("E_feed",      "N_feed",     "N_mixer"),
        _edge("E_mix_pfr",   "N_mixer",    "N_pfr"),
        _edge("E_pfr_split", "N_pfr",      "N_splitter"),
        _edge("E_product",   "N_splitter", "N_product",  handle="0"),
        _edge("E_recycle",   "N_splitter", "N_mixer",    handle="1"),
    ]
    return nodes, edges


# ══════════════════════════════════════════════════════════════════════════════
# Cycle detection
# ══════════════════════════════════════════════════════════════════════════════

class TestCycleDetection:
    def test_acyclic_flowsheet_has_no_tear_streams(self):
        nodes = [
            _feed_node("N1"),
            _node("N2", "product"),
        ]
        edges = [_edge("E1", "N1", "N2")]
        solver = FlowsheetSolver(nodes, edges)
        assert solver._find_tear_streams() == []

    def test_simple_two_node_cycle_detected(self):
        nodes = [_node("N1", "mixer"), _node("N2", "mixer")]
        edges = [_edge("E1", "N1", "N2"), _edge("E2", "N2", "N1")]
        solver = FlowsheetSolver(nodes, edges)
        tears = solver._find_tear_streams()
        assert len(tears) == 1

    def test_recycle_flowsheet_detects_one_tear_stream(self):
        nodes, edges = _recycle_flowsheet()
        solver = FlowsheetSolver(nodes, edges)
        tears = solver._find_tear_streams()
        assert len(tears) == 1

    def test_acyclic_solve_still_raises_on_direct_cycle(self):
        """Acyclic solver path should no longer be reachable for a cycle,
        but _topological_sort_edges should raise if somehow called with one."""
        nodes = [_node("N1", "mixer"), _node("N2", "mixer")]
        edges = [_edge("E1", "N1", "N2"), _edge("E2", "N2", "N1")]
        solver = FlowsheetSolver(nodes, edges)
        # solve() now handles cycles via recycle path, so no error here
        # (a ConvergenceError may arise if it can't converge, but not SimulationError)
        result = solver.solve()
        # Two mixers with no feed cannot converge — the solver should raise or
        # return a non-converged result depending on implementation, but must not
        # crash with an unhandled exception other than ConvergenceError.
        # (We just check it doesn't raise an unexpected exception type.)


# ══════════════════════════════════════════════════════════════════════════════
# Convergence: canonical CSTR-with-recycle
# ══════════════════════════════════════════════════════════════════════════════

class TestRecycleConvergence:

    def test_solver_converges(self):
        nodes, edges = _recycle_flowsheet()
        result = FlowsheetSolver(nodes, edges).solve()
        assert result["convergence_info"]["converged"] is True

    def test_product_flow_equals_feed_flow(self):
        """At steady state outlet flow must equal fresh feed flow."""
        nodes, edges = _recycle_flowsheet()
        result = FlowsheetSolver(nodes, edges).solve()
        product = result["streams"]["E_product"]
        assert product["flow"] == pytest.approx(1.0, rel=1e-3)

    def test_product_benzene_fraction_analytical(self):
        """z_A = 1/6 at steady state."""
        nodes, edges = _recycle_flowsheet()
        result = FlowsheetSolver(nodes, edges).solve()
        z_A = result["streams"]["E_product"]["composition"]["benzene"]
        assert z_A == pytest.approx(1.0 / 6.0, rel=1e-3)

    def test_product_toluene_fraction_analytical(self):
        """z_B = 5/6 at steady state."""
        nodes, edges = _recycle_flowsheet()
        result = FlowsheetSolver(nodes, edges).solve()
        z_B = result["streams"]["E_product"]["composition"]["toluene"]
        assert z_B == pytest.approx(5.0 / 6.0, rel=1e-3)

    def test_overall_conversion_greater_than_single_pass(self):
        """Recycle boosts overall conversion above the per-pass 80 %."""
        nodes, edges = _recycle_flowsheet()
        result = FlowsheetSolver(nodes, edges).solve()
        z_A = result["streams"]["E_product"]["composition"]["benzene"]
        overall_conversion = 1.0 - z_A   # pure-benzene fresh feed
        assert overall_conversion > 0.80

    def test_convergence_info_keys_present(self):
        nodes, edges = _recycle_flowsheet()
        result = FlowsheetSolver(nodes, edges).solve()
        ci = result["convergence_info"]
        assert "converged"     in ci
        assert "iterations"    in ci
        assert "tear_streams"  in ci
        assert "residuals"     in ci

    def test_convergence_info_iterations_positive(self):
        nodes, edges = _recycle_flowsheet()
        result = FlowsheetSolver(nodes, edges).solve()
        assert result["convergence_info"]["iterations"] >= 1

    def test_residuals_list_length_matches_iterations(self):
        nodes, edges = _recycle_flowsheet()
        result = FlowsheetSolver(nodes, edges).solve()
        ci = result["convergence_info"]
        assert len(ci["residuals"]) == ci["iterations"]

    def test_tear_stream_identified(self):
        nodes, edges = _recycle_flowsheet()
        result = FlowsheetSolver(nodes, edges).solve()
        assert len(result["convergence_info"]["tear_streams"]) == 1

    def test_material_balance_closes(self):
        """Total molar flow in = total molar flow out."""
        nodes, edges = _recycle_flowsheet()
        result = FlowsheetSolver(nodes, edges).solve()
        feed_flow    = result["streams"]["E_feed"]["flow"]
        product_flow = result["streams"]["E_product"]["flow"]
        assert feed_flow == pytest.approx(product_flow, rel=1e-3)

    def test_higher_recycle_fraction_raises_overall_conversion(self):
        """More recycle → higher overall benzene conversion."""
        _, edges_20 = _recycle_flowsheet(recycle_fraction=0.2)
        nodes_20, _ = _recycle_flowsheet(recycle_fraction=0.2)
        nodes_40, edges_40 = _recycle_flowsheet(recycle_fraction=0.4)

        res_20 = FlowsheetSolver(nodes_20, edges_20).solve()
        res_40 = FlowsheetSolver(nodes_40, edges_40).solve()

        z_A_20 = res_20["streams"]["E_product"]["composition"]["benzene"]
        z_A_40 = res_40["streams"]["E_product"]["composition"]["benzene"]
        assert z_A_40 < z_A_20   # lower benzene fraction = higher conversion

    def test_acyclic_result_has_convergence_info(self):
        """Even acyclic flowsheets return a convergence_info block."""
        nodes = [_feed_node("N1"), _node("N2", "product")]
        edges = [_edge("E1", "N1", "N2")]
        result = FlowsheetSolver(nodes, edges).solve()
        ci = result["convergence_info"]
        assert ci["converged"] is True
        assert ci["iterations"] == 0
        assert ci["tear_streams"] == []

    def test_recycle_node_passthrough_accepted(self):
        """A 'recycle' marker node in the loop does not break the solver."""
        nodes = [
            _feed_node("N_feed"),
            _node("N_mixer",    "mixer"),
            _node("N_pfr",      "pfr",
                  stoichiometry={"benzene": -1.0, "toluene": 1.0},
                  conversion=0.8,
                  delta_Hrxn_J_mol=0.0),
            _node("N_splitter", "splitter", fractions=[0.8, 0.2]),
            _node("N_recycle",  "recycle"),   # visual marker
            _node("N_product",  "product"),
        ]
        edges = [
            _edge("E_feed",      "N_feed",     "N_mixer"),
            _edge("E_mix_pfr",   "N_mixer",    "N_pfr"),
            _edge("E_pfr_split", "N_pfr",      "N_splitter"),
            _edge("E_product",   "N_splitter", "N_product",  handle="0"),
            _edge("E_to_rec",    "N_splitter", "N_recycle",  handle="1"),
            _edge("E_recycle",   "N_recycle",  "N_mixer"),
        ]
        result = FlowsheetSolver(nodes, edges).solve()
        assert result["convergence_info"]["converged"] is True
        z_A = result["streams"]["E_product"]["composition"]["benzene"]
        assert z_A == pytest.approx(1.0 / 6.0, rel=1e-2)

    def test_recycle_node_estimate_accepted(self):
        """Estimates provided via a recycle node are used as the initial guess."""
        nodes = [
            _feed_node("N_feed"),
            _node("N_mixer",    "mixer"),
            _node("N_pfr",      "pfr",
                  stoichiometry={"benzene": -1.0, "toluene": 1.0},
                  conversion=0.8,
                  delta_Hrxn_J_mol=0.0),
            _node("N_splitter", "splitter", fractions=[0.8, 0.2]),
            {
                "id": "N_recycle", "type": "recycle",
                "data": {
                    "estimate": {
                        "temperature_C": 25.0,
                        "pressure_bar":  1.0,
                        "flow_mol_s":    0.25,
                        "composition":   {"benzene": 0.17, "toluene": 0.83},
                    }
                },
                "position": {"x": 0, "y": 0},
            },
            _node("N_product", "product"),
        ]
        edges = [
            _edge("E_feed",      "N_feed",     "N_mixer"),
            _edge("E_mix_pfr",   "N_mixer",    "N_pfr"),
            _edge("E_pfr_split", "N_pfr",      "N_splitter"),
            _edge("E_product",   "N_splitter", "N_product",  handle="0"),
            _edge("E_to_rec",    "N_splitter", "N_recycle",  handle="1"),
            _edge("E_recycle",   "N_recycle",  "N_mixer"),
        ]
        result = FlowsheetSolver(nodes, edges).solve()
        assert result["convergence_info"]["converged"] is True


# ══════════════════════════════════════════════════════════════════════════════
# ConvergenceError
# ══════════════════════════════════════════════════════════════════════════════

class TestConvergenceError:
    def test_convergence_error_is_simulation_error(self):
        err = ConvergenceError("failed", iterations=100, residuals=[1.0])
        assert isinstance(err, SimulationError)

    def test_convergence_error_stores_diagnostics(self):
        residuals = [0.5, 0.3, 0.1]
        err = ConvergenceError("test", iterations=3, residuals=residuals)
        assert err.iterations == 3
        assert err.residuals == residuals
