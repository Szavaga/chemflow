"""
Phase 2 validation checklist — seven acceptance tests.
"""
from __future__ import annotations
import pytest
from app.core.exceptions import ThermodynamicRangeError
from app.core.simulation import COMPONENT_LIBRARY, CAS_LOOKUP
from app.core.unit_ops import DistillationShortcut, Flash, SimulationError, Stream
from app.core.flowsheet_solver import FlowsheetSolver
from app.core.context_builder import build_prompt_context


# ── 1. Feed CAS resolution ────────────────────────────────────────────────────

class TestFeedCasResolution:
    def test_cas_keys_resolve_to_library_components(self):
        for cas, comp_id in CAS_LOOKUP.items():
            assert comp_id in COMPONENT_LIBRARY

    def test_feed_node_with_cas_composition(self):
        nodes = [
            {
                "id": "F1", "type": "feed",
                "data": {
                    "label": "Feed",
                    "composition": {"71-43-2": 0.5, "108-88-3": 0.5},
                    "flow_mol_s": 2.0, "temperature_C": 25.0, "pressure_bar": 1.013,
                },
            },
            {"id": "P1", "type": "product", "data": {}},
        ]
        edges = [{"id": "e1", "source": "F1", "target": "P1", "source_handle": "0"}]
        result = FlowsheetSolver(nodes, edges).solve()
        assert result["converged"]
        stream = result["streams"]["e1"]
        assert abs(stream["flow"] - 2.0) < 1e-9
        comp = stream["composition"]
        assert "benzene" in comp and "toluene" in comp
        assert abs(comp["benzene"] - 0.5) < 1e-6

    def test_all_cas_entries_are_distinct(self):
        assert len(CAS_LOOKUP) == len(set(CAS_LOOKUP.keys()))


# ── 2. Flash PR — ethane/propane VLE ─────────────────────────────────────────

class TestFlashPR:
    def _feed(self):
        return Stream("feed", 26.85, 20.0, 1.0, {"ethane": 0.5, "propane": 0.5}, 0.0)

    def test_pr_flash_converges(self):
        feed = self._feed()
        outlets, summary = Flash().solve(
            [feed], temperature_C=feed.temperature, pressure_bar=feed.pressure,
            property_package="peng_robinson",
        )
        assert 0.0 <= summary["vapor_fraction"] <= 1.0

    def test_pr_k_values_ordered(self):
        feed = self._feed()
        _, summary = Flash().solve(
            [feed], temperature_C=feed.temperature, pressure_bar=feed.pressure,
            property_package="peng_robinson",
        )
        K = summary["K_values"]
        assert K["ethane"] > K["propane"]

    def test_pr_vs_ideal_within_10_pct(self):
        feed = self._feed()
        _, s_id = Flash().solve([feed], temperature_C=feed.temperature,
            pressure_bar=feed.pressure, property_package="ideal")
        _, s_pr = Flash().solve([feed], temperature_C=feed.temperature,
            pressure_bar=feed.pressure, property_package="peng_robinson")
        psi_id = s_id["vapor_fraction"]
        psi_pr = s_pr["vapor_fraction"]
        if 0.05 < psi_id < 0.95:
            assert abs(psi_pr - psi_id) < 0.10

    def test_pr_mass_balance_closes(self):
        feed = self._feed()
        outlets, _ = Flash().solve([feed], temperature_C=feed.temperature,
            pressure_bar=feed.pressure, property_package="peng_robinson")
        assert abs(sum(o.flow for o in outlets) - feed.flow) < 1e-9


# ── 3. Recycle CSTR ───────────────────────────────────────────────────────────

class TestRecycleCSTR:
    def _build(self):
        nodes = [
            {"id": "feed", "type": "feed", "data": {
                "composition": {"ethanol": 1.0}, "flow_mol_s": 1.0,
                "temperature_C": 25.0, "pressure_bar": 1.0,
            }},
            {"id": "mix", "type": "mixer", "data": {}},
            {"id": "cstr", "type": "cstr", "data": {
                "volume_L": 100.0, "temperature_C": 76.85,
                "coolant_temp_K": 300.0, "pre_exponential": 7.2e10 / 60.0,
                "activation_energy_J_mol": 72681.0,
            }},
            {"id": "split", "type": "splitter", "data": {"fractions": [0.7, 0.3]}},
            {"id": "prod", "type": "product", "data": {}},
            {"id": "recycle", "type": "recycle", "data": {
                "estimate": {"composition": {"ethanol": 1.0}, "flow_mol_s": 0.43,
                             "temperature_C": 76.85, "pressure_bar": 1.0}
            }},
        ]
        edges = [
            {"id": "e0", "source": "feed",    "target": "mix",     "source_handle": "0"},
            {"id": "e1", "source": "recycle", "target": "mix",     "source_handle": "0"},
            {"id": "e2", "source": "mix",     "target": "cstr",    "source_handle": "0"},
            {"id": "e3", "source": "cstr",    "target": "split",   "source_handle": "0"},
            {"id": "e4", "source": "split",   "target": "prod",    "source_handle": "0"},
            {"id": "e5", "source": "split",   "target": "recycle", "source_handle": "1"},
        ]
        return nodes, edges

    def test_recycle_cstr_converges(self):
        result = FlowsheetSolver(*self._build()).solve()
        assert result["converged"]

    def test_recycle_cstr_mass_balance(self):
        result = FlowsheetSolver(*self._build()).solve()
        s = result["streams"]
        if "e3" in s and "e4" in s and "e5" in s:
            assert abs(s["e4"]["flow"] + s["e5"]["flow"] - s["e3"]["flow"]) < 1e-6

    def test_recycle_cstr_convergence_info(self):
        result = FlowsheetSolver(*self._build()).solve()
        loops = result.get("convergence_info", {}).get("recycle_loops", [])
        assert len(loops) >= 1
        assert loops[0]["final_residual"] < 1e-4


# ── 4. Distillation FUG — BTX Seader & Henley ────────────────────────────────

class TestDistillationFUG:
    def _feed(self):
        return Stream("btx", 100.0, 1.013, 1.0,
                      {"benzene": 0.40, "toluene": 0.30, "xylene": 0.30}, 0.0)

    def test_btx_N_actual_within_2_of_textbook(self):
        _, s = DistillationShortcut().solve(
            [self._feed()], light_key="toluene", heavy_key="xylene",
            lk_recovery=0.99, hk_recovery=0.99, reflux_ratio=2.0,
            property_package="ideal", q=1.0,
            distillate_name="dist", bottoms_name="bot",
        )
        assert 13 <= s["N_actual"] <= 17

    def test_btx_lk_recovery(self):
        feed = self._feed()
        outlets, _ = DistillationShortcut().solve(
            [feed], light_key="toluene", heavy_key="xylene",
            lk_recovery=0.99, hk_recovery=0.99, reflux_ratio=2.0,
            property_package="ideal", distillate_name="dist", bottoms_name="bot",
        )
        dist = next(o for o in outlets if o.name == "dist")
        assert (dist.flow * dist.composition.get("toluene", 0)) / (feed.flow * feed.composition["toluene"]) >= 0.989

    def test_btx_hk_recovery(self):
        feed = self._feed()
        outlets, _ = DistillationShortcut().solve(
            [feed], light_key="toluene", heavy_key="xylene",
            lk_recovery=0.99, hk_recovery=0.99, reflux_ratio=2.0,
            property_package="ideal", distillate_name="dist", bottoms_name="bot",
        )
        bot = next(o for o in outlets if o.name == "bot")
        assert (bot.flow * bot.composition.get("xylene", 0)) / (feed.flow * feed.composition["xylene"]) >= 0.989


# ── 5. Full flowsheet end-to-end ──────────────────────────────────────────────

class TestFullFlowsheet:
    def _build(self):
        nodes = [
            {"id": "feed", "type": "feed", "data": {
                "composition": {"benzene": 0.33, "toluene": 0.34, "xylene": 0.33},
                "flow_mol_s": 2.0, "temperature_C": 100.0, "pressure_bar": 1.5,
            }},
            {"id": "flash", "type": "flash_drum", "data": {
                "temperature_C": 100.0, "pressure_bar": 1.5, "property_package": "peng_robinson",
            }},
            {"id": "dist", "type": "distillation_shortcut", "data": {
                "light_key": "toluene", "heavy_key": "xylene",
                "lk_recovery": 0.95, "hk_recovery": 0.95, "reflux_ratio": 2.0,
                "property_package": "ideal",
            }},
            {"id": "prod_v", "type": "product", "data": {}},
            {"id": "prod_d", "type": "product", "data": {}},
            {"id": "prod_b", "type": "product", "data": {}},
        ]
        edges = [
            {"id": "e1", "source": "feed",  "target": "flash",  "source_handle": "0"},
            {"id": "e2", "source": "flash", "target": "dist",   "source_handle": "0"},
            {"id": "e3", "source": "flash", "target": "prod_v", "source_handle": "1"},
            {"id": "e4", "source": "dist",  "target": "prod_d", "source_handle": "0"},
            {"id": "e5", "source": "dist",  "target": "prod_b", "source_handle": "1"},
        ]
        return nodes, edges

    def test_full_flowsheet_converges(self):
        assert FlowsheetSolver(*self._build()).solve()["converged"]

    def test_full_flowsheet_mass_balance(self):
        result = FlowsheetSolver(*self._build()).solve()
        s = result["streams"]
        prod_flows = sum(s[eid]["flow"] for eid in ("e3", "e4", "e5") if eid in s)
        assert abs(prod_flows - s["e1"]["flow"]) < 1e-6

    def test_full_flowsheet_distillation_summary(self):
        result = FlowsheetSolver(*self._build()).solve()
        smry = result.get("node_summaries", {})
        assert "dist" in smry and "N_actual" in smry["dist"]

    def test_full_flowsheet_pr_flash_summary(self):
        result = FlowsheetSolver(*self._build()).solve()
        smry = result.get("node_summaries", {})
        assert smry.get("flash", {}).get("property_package") == "peng_robinson"


# ── 6. Antoine T-range guard ──────────────────────────────────────────────────

class TestAntoineExtrapolation:
    def test_water_at_500K_raises(self):
        water = COMPONENT_LIBRARY["water"]
        assert water.tmax_C is not None
        with pytest.raises(ThermodynamicRangeError) as exc_info:
            water.vapor_pressure(500.0 - 273.15)
        assert exc_info.value.T > exc_info.value.T_max

    def test_water_at_100C_ok(self):
        psat = COMPONENT_LIBRARY["water"].vapor_pressure(100.0)
        assert 0.9 < psat < 1.2

    def test_benzene_no_range_no_raise(self):
        b = COMPONENT_LIBRARY["benzene"]
        assert b.tmin_C is None and b.tmax_C is None
        assert b.vapor_pressure(200.0) > 0


# ── 7. context_builder: distillation + PR sections ───────────────────────────

class TestContextBuilder:
    def _result(self):
        return {
            "converged": True,
            "streams": {"s1": {"flow": 1.0, "temperature": 100.0, "pressure": 1.5,
                               "vapor_fraction": 0.0,
                               "composition": {"benzene": 0.4, "toluene": 0.3, "xylene": 0.3}}},
            "energy_balance": {"total_duty_kW": -12.5, "heating_kW": 0.0, "cooling_kW": 12.5},
            "node_summaries": {
                "dist1": {
                    "N_min": 10.82, "R_min": 0.55, "N_actual": 15,
                    "N_feed_tray": 8, "alpha_lk_hk": 2.34, "reflux_ratio": 2.0,
                    "condenser_duty_kW": -45.0, "reboiler_duty_kW": 48.0,
                    "property_package": "ideal",
                    "distillate_stream": {"flow": 0.59, "temperature": 110.0,
                                         "composition": {"benzene": 0.68, "toluene": 0.32}},
                    "bottoms_stream":    {"flow": 0.41, "temperature": 135.0,
                                         "composition": {"toluene": 0.08, "xylene": 0.92}},
                },
                "flash1": {
                    "vapor_fraction": 0.12, "property_package": "peng_robinson",
                    "K_values": {"benzene": 0.8, "toluene": 0.4, "xylene": 0.2},
                },
            },
            "solver_diagnostics": {"converged": True, "iterations": 0,
                                   "tear_streams": [], "residuals": [], "warnings": []},
        }

    def test_distillation_section_present(self):
        assert "Distillation Columns" in build_prompt_context(self._result())

    def test_distillation_node_id_present(self):
        assert "dist1" in build_prompt_context(self._result())

    def test_distillation_key_metrics_present(self):
        ctx = build_prompt_context(self._result())
        assert ("N_actual" in ctx or "Gilliland" in ctx) and ("N_min" in ctx or "Fenske" in ctx)

    def test_pr_section_present(self):
        assert "Peng-Robinson" in build_prompt_context(self._result())

    def test_pr_node_id_present(self):
        assert "flash1" in build_prompt_context(self._result())

    def test_context_wrapped_correctly(self):
        ctx = build_prompt_context(self._result())
        assert ctx.startswith("=== ChemFlow Process Context ===")
        assert ctx.strip().endswith("=== End of Process Context ===")
