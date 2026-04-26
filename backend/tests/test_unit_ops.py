"""
Tests for:
  app.core.thermo         — thermodynamic property functions
  app.core.unit_ops       — Stream, SimulationError, and all 6 unit operations
  app.core.flowsheet_solver — FlowsheetSolver (topological sort, full solve)
"""

from __future__ import annotations

import math
import pytest

from app.core.unit_ops import (
    DistillationShortcut,
    Flash,
    HeatExchanger,
    Mixer,
    PFR,
    Pump,
    SimulationError,
    Splitter,
    Stream,
)
from app.core.flowsheet_solver import FlowsheetSolver
from app.core.thermo import (
    mixture_Cp_liquid,
    mixture_Cp_ig,
    mixture_MW,
    mixture_density_liquid,
    mixture_enthalpy,
)


# ── helpers ───────────────────────────────────────────────────────────────────

def benz_tol(benz: float = 0.5, T: float = 25.0, P: float = 1.0, F: float = 1.0) -> Stream:
    """50/50 benzene-toluene liquid stream."""
    tol = 1.0 - benz
    return Stream("feed", T, P, F, {"benzene": benz, "toluene": tol}, 0.0)


def water_stream(T: float = 25.0, P: float = 1.0, F: float = 1.0) -> Stream:
    return Stream("water_feed", T, P, F, {"water": 1.0}, 0.0)


# ══════════════════════════════════════════════════════════════════════════════
# Thermodynamic property functions
# ══════════════════════════════════════════════════════════════════════════════

class TestThermo:
    def test_mixture_MW_pure_benzene(self):
        MW = mixture_MW({"benzene": 1.0})
        assert abs(MW - 78.11) < 0.01

    def test_mixture_MW_50_50_benz_tol(self):
        MW = mixture_MW({"benzene": 0.5, "toluene": 0.5})
        assert abs(MW - (78.11 * 0.5 + 92.14 * 0.5)) < 0.01

    def test_mixture_Cp_liquid_water(self):
        Cp = mixture_Cp_liquid({"water": 1.0})
        # Pure water liquid Cp should be ~75 J/(mol·K)
        assert 70 < Cp < 80

    def test_mixture_Cp_ig_benzene(self):
        Cp = mixture_Cp_ig({"benzene": 1.0})
        assert 75 < Cp < 90

    def test_mixture_enthalpy_liquid_at_reference(self):
        # At T=0 °C, liquid enthalpy = 0 by definition
        H = mixture_enthalpy({"water": 1.0}, T_C=0.0, vapor_fraction=0.0)
        assert H == pytest.approx(0.0, abs=1.0)

    def test_mixture_enthalpy_increases_with_T(self):
        H1 = mixture_enthalpy({"benzene": 1.0}, T_C=25.0)
        H2 = mixture_enthalpy({"benzene": 1.0}, T_C=100.0)
        assert H2 > H1

    def test_mixture_enthalpy_vapor_higher_than_liquid(self):
        comp = {"benzene": 1.0}
        H_liq = mixture_enthalpy(comp, T_C=80.0, vapor_fraction=0.0)
        H_vap = mixture_enthalpy(comp, T_C=80.0, vapor_fraction=1.0)
        assert H_vap > H_liq   # must include ΔHvap

    def test_density_liquid_water(self):
        rho = mixture_density_liquid({"water": 1.0})
        assert 990 < rho < 1005   # ~997 kg/m³

    def test_density_liquid_benzene(self):
        rho = mixture_density_liquid({"benzene": 1.0})
        assert 860 < rho < 900

    def test_density_liquid_mixture(self):
        # Mixture density should be between the two pure components
        rho_benz = mixture_density_liquid({"benzene": 1.0})
        rho_tol  = mixture_density_liquid({"toluene": 1.0})
        rho_mix  = mixture_density_liquid({"benzene": 0.5, "toluene": 0.5})
        assert min(rho_benz, rho_tol) < rho_mix < max(rho_benz, rho_tol)


# ══════════════════════════════════════════════════════════════════════════════
# Stream validation
# ══════════════════════════════════════════════════════════════════════════════

class TestStream:
    def test_valid_stream(self):
        s = Stream("s1", 25.0, 1.0, 1.0, {"water": 1.0})
        assert s.flow == 1.0

    def test_negative_flow_rejected(self):
        with pytest.raises(SimulationError, match="flow"):
            Stream("bad", 25.0, 1.0, -0.1, {"water": 1.0})

    def test_empty_composition_rejected(self):
        with pytest.raises(SimulationError, match="composition"):
            Stream("bad", 25.0, 1.0, 1.0, {})

    def test_composition_not_summing_to_one(self):
        with pytest.raises(SimulationError, match="sums"):
            Stream("bad", 25.0, 1.0, 1.0, {"benzene": 0.3, "toluene": 0.3})

    def test_invalid_vapor_fraction(self):
        with pytest.raises(SimulationError, match="vapor_fraction"):
            Stream("bad", 25.0, 1.0, 1.0, {"water": 1.0}, vapor_fraction=1.5)

    def test_zero_flow_is_valid(self):
        s = Stream("zero", 25.0, 1.0, 0.0, {"water": 1.0})
        assert s.flow == 0.0

    def test_to_dict_roundtrip(self):
        s = Stream("s1", 80.0, 2.0, 5.0, {"benzene": 0.5, "toluene": 0.5}, 0.3)
        d = s.to_dict()
        assert d["temperature"] == 80.0
        assert d["pressure"] == 2.0
        assert d["flow"] == 5.0
        assert d["vapor_fraction"] == 0.3

    def test_enthalpy_flow_proportional_to_flow(self):
        s1 = Stream("s1", 50.0, 1.0, 1.0, {"water": 1.0})
        s2 = Stream("s2", 50.0, 1.0, 2.0, {"water": 1.0})
        assert s2.enthalpy_flow == pytest.approx(2 * s1.enthalpy_flow)


# ══════════════════════════════════════════════════════════════════════════════
# Mixer
# ══════════════════════════════════════════════════════════════════════════════

class TestMixer:
    def test_total_flow_conserved(self):
        s1 = Stream("a", 25.0, 1.0, 2.0, {"water": 1.0})
        s2 = Stream("b", 50.0, 1.0, 3.0, {"water": 1.0})
        outs, smry = Mixer().solve([s1, s2])
        assert outs[0].flow == pytest.approx(5.0)

    def test_component_balance(self):
        s1 = Stream("a", 25.0, 1.0, 1.0, {"benzene": 1.0})
        s2 = Stream("b", 25.0, 1.0, 1.0, {"toluene": 1.0})
        outs, _ = Mixer().solve([s1, s2])
        out = outs[0]
        assert out.composition["benzene"] == pytest.approx(0.5, abs=1e-6)
        assert out.composition["toluene"] == pytest.approx(0.5, abs=1e-6)

    def test_outlet_pressure_is_minimum(self):
        s1 = Stream("a", 25.0, 3.0, 1.0, {"water": 1.0})
        s2 = Stream("b", 25.0, 1.0, 1.0, {"water": 1.0})
        outs, _ = Mixer().solve([s1, s2])
        assert outs[0].pressure == pytest.approx(1.0)

    def test_energy_balance_same_component(self):
        # Two identical streams — outlet T should equal inlet T
        s1 = Stream("a", 40.0, 1.0, 1.0, {"water": 1.0})
        s2 = Stream("b", 40.0, 1.0, 1.0, {"water": 1.0})
        outs, _ = Mixer().solve([s1, s2])
        assert outs[0].temperature == pytest.approx(40.0, abs=1.0)

    def test_energy_balance_hot_cold_mix(self):
        # Mix 1 mol/s at 0°C and 1 mol/s at 100°C of pure water → ~50°C
        s1 = Stream("cold", 0.0,   1.0, 1.0, {"water": 1.0})
        s2 = Stream("hot",  100.0, 1.0, 1.0, {"water": 1.0})
        outs, _ = Mixer().solve([s1, s2])
        assert outs[0].temperature == pytest.approx(50.0, abs=2.0)

    def test_single_inlet_passthrough(self):
        s = benz_tol(T=60.0, F=3.0)
        outs, smry = Mixer().solve([s])
        assert outs[0].flow == pytest.approx(3.0)
        assert outs[0].temperature == pytest.approx(60.0, abs=1.0)

    def test_empty_inlets_raises(self):
        with pytest.raises(SimulationError, match="at least one"):
            Mixer().solve([])

    def test_zero_total_flow_raises(self):
        s1 = Stream("a", 25.0, 1.0, 0.0, {"water": 1.0})
        s2 = Stream("b", 25.0, 1.0, 0.0, {"water": 1.0})
        with pytest.raises(SimulationError, match="zero"):
            Mixer().solve([s1, s2])


# ══════════════════════════════════════════════════════════════════════════════
# Splitter
# ══════════════════════════════════════════════════════════════════════════════

class TestSplitter:
    def test_two_way_split_flows(self):
        feed = benz_tol(F=10.0)
        outs, smry = Splitter().solve([feed], fractions=[0.3, 0.7])
        assert outs[0].flow == pytest.approx(3.0)
        assert outs[1].flow == pytest.approx(7.0)

    def test_composition_unchanged(self):
        feed = benz_tol()
        outs, _ = Splitter().solve([feed], fractions=[0.5, 0.5])
        for out in outs:
            assert out.composition == pytest.approx(feed.composition, abs=1e-9)

    def test_temperature_unchanged(self):
        feed = benz_tol(T=80.0)
        outs, _ = Splitter().solve([feed], fractions=[0.4, 0.6])
        for out in outs:
            assert out.temperature == feed.temperature

    def test_three_way_split(self):
        feed = water_stream(F=9.0)
        outs, _ = Splitter().solve([feed], fractions=[1/3, 1/3, 1/3])
        assert len(outs) == 3
        for out in outs:
            assert out.flow == pytest.approx(3.0, abs=1e-9)

    def test_fractions_not_summing_to_one_raises(self):
        with pytest.raises(SimulationError, match="sum"):
            Splitter().solve([benz_tol()], fractions=[0.3, 0.3])

    def test_negative_fraction_raises(self):
        with pytest.raises(SimulationError, match="non-negative"):
            Splitter().solve([benz_tol()], fractions=[-0.1, 1.1])

    def test_wrong_inlet_count_raises(self):
        with pytest.raises(SimulationError, match="exactly 1"):
            Splitter().solve([benz_tol(), water_stream()], fractions=[0.5, 0.5])

    def test_custom_outlet_names(self):
        feed = water_stream()
        outs, _ = Splitter().solve(
            [feed], fractions=[0.6, 0.4], outlet_names=["top", "bottom"]
        )
        assert outs[0].name == "top"
        assert outs[1].name == "bottom"


# ══════════════════════════════════════════════════════════════════════════════
# HeatExchanger
# ══════════════════════════════════════════════════════════════════════════════

class TestHeatExchanger:
    def test_duty_mode_increases_temperature(self):
        feed = water_stream(T=20.0)
        Q = mixture_Cp_liquid({"water": 1.0}) * feed.flow * 80.0   # heat to raise ~80°C
        outs, smry = HeatExchanger().solve([feed], mode="duty", duty_W=Q)
        assert outs[0].temperature > 20.0
        assert smry["duty_W"] == pytest.approx(Q)

    def test_duty_mode_negative_duty_cools(self):
        feed = water_stream(T=80.0)
        Q = -mixture_Cp_liquid({"water": 1.0}) * feed.flow * 60.0
        outs, smry = HeatExchanger().solve([feed], mode="duty", duty_W=Q)
        assert outs[0].temperature < 80.0

    def test_outlet_temp_mode(self):
        feed = water_stream(T=20.0)
        outs, smry = HeatExchanger().solve(
            [feed], mode="outlet_temp", outlet_temp_C=80.0
        )
        assert outs[0].temperature == pytest.approx(80.0)
        assert smry["duty_W"] > 0   # heating required

    def test_outlet_temp_cooling_gives_negative_duty(self):
        feed = water_stream(T=90.0)
        outs, smry = HeatExchanger().solve(
            [feed], mode="outlet_temp", outlet_temp_C=30.0
        )
        assert smry["duty_W"] < 0

    def test_flow_unchanged(self):
        feed = water_stream(F=5.0)
        outs, _ = HeatExchanger().solve([feed], mode="duty", duty_W=1000.0)
        assert outs[0].flow == pytest.approx(5.0)

    def test_composition_unchanged(self):
        feed = benz_tol()
        outs, _ = HeatExchanger().solve([feed], mode="outlet_temp", outlet_temp_C=60.0)
        assert outs[0].composition == pytest.approx(feed.composition, abs=1e-9)

    def test_duty_zero_no_change(self):
        feed = water_stream(T=50.0)
        outs, smry = HeatExchanger().solve([feed], mode="duty", duty_W=0.0)
        assert outs[0].temperature == pytest.approx(50.0, abs=0.1)

    def test_unknown_mode_raises(self):
        with pytest.raises(SimulationError, match="mode"):
            HeatExchanger().solve([water_stream()], mode="magic", duty_W=0.0)

    def test_duty_mode_missing_duty_raises(self):
        with pytest.raises(SimulationError, match="duty_W"):
            HeatExchanger().solve([water_stream()], mode="duty")

    def test_outlet_temp_mode_missing_temp_raises(self):
        with pytest.raises(SimulationError, match="outlet_temp_C"):
            HeatExchanger().solve([water_stream()], mode="outlet_temp")

    def test_wrong_inlet_count_raises(self):
        with pytest.raises(SimulationError, match="exactly 1"):
            HeatExchanger().solve(
                [water_stream(), water_stream()], mode="duty", duty_W=0.0
            )


# ══════════════════════════════════════════════════════════════════════════════
# PFR
# ══════════════════════════════════════════════════════════════════════════════

class TestPFR:
    # Stoichiometry: A → B  (single reactant, single product)
    _stoich_AB = {"benzene": -1.0, "toluene": 1.0}

    def test_full_conversion_depletes_reactant(self):
        # Feed: pure benzene; reaction benzene → toluene; X=1
        feed = Stream("f", 25.0, 1.0, 1.0, {"benzene": 1.0})
        outs, smry = PFR().solve(
            [feed], stoichiometry={"benzene": -1.0, "toluene": 1.0}, conversion=1.0
        )
        assert "toluene" in outs[0].composition
        assert outs[0].composition.get("benzene", 0.0) == pytest.approx(0.0, abs=1e-9)

    def test_zero_conversion_passthrough(self):
        feed = benz_tol()
        outs, smry = PFR().solve(
            [feed], stoichiometry=self._stoich_AB, conversion=0.0
        )
        assert outs[0].composition == pytest.approx(feed.composition, abs=1e-9)
        assert smry["moles_consumed_mol_s"] == pytest.approx(0.0)

    def test_partial_conversion(self):
        feed = Stream("f", 25.0, 1.0, 2.0, {"benzene": 1.0})
        outs, smry = PFR().solve(
            [feed], stoichiometry={"benzene": -1.0, "toluene": 1.0}, conversion=0.5
        )
        # 50% of 2 mol/s benzene consumed → 1 mol/s benzene + 1 mol/s toluene out
        # total = 2 mol/s, z_benz = 1/2 = 0.5
        assert outs[0].flow == pytest.approx(2.0, rel=1e-6)
        z_benz = outs[0].composition.get("benzene", 0.0)
        assert z_benz == pytest.approx(0.5, rel=1e-3)

    def test_exothermic_reaction_raises_temperature(self):
        feed = Stream("f", 25.0, 1.0, 1.0, {"benzene": 1.0})
        outs, smry = PFR().solve(
            [feed],
            stoichiometry={"benzene": -1.0, "toluene": 1.0},
            conversion=1.0,
            delta_Hrxn_J_mol=-50_000.0,   # exothermic
        )
        assert outs[0].temperature > 25.0
        assert smry["heat_released_W"] > 0

    def test_endothermic_reaction_lowers_temperature(self):
        feed = Stream("f", 100.0, 1.0, 1.0, {"benzene": 1.0})
        outs, smry = PFR().solve(
            [feed],
            stoichiometry={"benzene": -1.0, "toluene": 1.0},
            conversion=1.0,
            delta_Hrxn_J_mol=+50_000.0,   # endothermic
        )
        assert outs[0].temperature < 100.0

    def test_molar_expansion_A_to_2B(self):
        # A → 2B: outlet molar flow doubles at full conversion
        feed = Stream("f", 25.0, 1.0, 1.0, {"benzene": 1.0})
        outs, smry = PFR().solve(
            [feed],
            stoichiometry={"benzene": -1.0, "toluene": 2.0},
            conversion=1.0,
        )
        assert outs[0].flow == pytest.approx(2.0, rel=1e-6)

    def test_no_reactant_in_stoich_raises(self):
        feed = benz_tol()
        with pytest.raises(SimulationError, match="no reactant"):
            PFR().solve([feed], stoichiometry={"toluene": 1.0}, conversion=0.5)

    def test_conversion_out_of_range_raises(self):
        with pytest.raises(SimulationError, match="conversion"):
            PFR().solve(
                [benz_tol()], stoichiometry=self._stoich_AB, conversion=1.5
            )

    def test_empty_stoich_raises(self):
        with pytest.raises(SimulationError, match="stoichiometry"):
            PFR().solve([benz_tol()], stoichiometry={}, conversion=0.5)

    def test_wrong_inlet_count_raises(self):
        with pytest.raises(SimulationError, match="exactly 1"):
            PFR().solve(
                [benz_tol(), water_stream()],
                stoichiometry=self._stoich_AB,
                conversion=0.5,
            )


# ══════════════════════════════════════════════════════════════════════════════
# Flash
# ══════════════════════════════════════════════════════════════════════════════

class TestFlash:
    def test_two_phase_at_95C_1bar(self):
        # 50/50 benzene-toluene at 95°C, 1 bar → two-phase
        feed = benz_tol(T=95.0, P=1.0, F=2.0)
        outs, smry = Flash().solve([feed])
        assert 0.0 < smry["vapor_fraction"] < 1.0
        assert smry["converged"] is True

    def test_liquid_enriched_in_heavy_component(self):
        # At 95°C: benzene (lighter) concentrates in vapour
        feed = benz_tol(T=95.0, P=1.0, F=1.0)
        outs, smry = Flash().solve([feed])
        liq, vap = outs[0], outs[1]
        assert vap.composition["benzene"] > liq.composition["benzene"]

    def test_material_balance_closes(self):
        feed = benz_tol(T=95.0, P=1.0, F=4.0)
        outs, _ = Flash().solve([feed])
        liq, vap = outs[0], outs[1]
        assert liq.flow + vap.flow == pytest.approx(feed.flow, rel=1e-6)

    def test_component_balance_closes(self):
        feed = benz_tol(benz=0.3, T=95.0, P=1.0, F=1.0)
        outs, _ = Flash().solve([feed])
        liq, vap = outs[0], outs[1]
        for comp, z in feed.composition.items():
            reconstructed = (
                liq.flow * liq.composition.get(comp, 0.0)
                + vap.flow * vap.composition.get(comp, 0.0)
            )
            assert reconstructed == pytest.approx(z * feed.flow, rel=1e-4)

    def test_all_liquid_below_bubble_point(self):
        # At 25°C, 1 bar: benzene/toluene well below bubble point → all liquid
        feed = benz_tol(T=25.0, P=1.0, F=1.0)
        outs, smry = Flash().solve([feed])
        assert smry["vapor_fraction"] == pytest.approx(0.0)

    def test_all_vapor_above_dew_point(self):
        # At 150°C, 1 bar: far above dew point → all vapour
        feed = benz_tol(T=150.0, P=1.0, F=1.0)
        outs, smry = Flash().solve([feed])
        assert smry["vapor_fraction"] == pytest.approx(1.0)

    def test_temperature_override(self):
        feed = benz_tol(T=25.0, P=1.0, F=1.0)   # liquid at feed T
        outs, smry = Flash().solve([feed], temperature_C=95.0)
        assert 0.0 < smry["vapor_fraction"] < 1.0

    def test_pressure_override(self):
        feed = benz_tol(T=95.0, P=10.0, F=1.0)   # high P → liquid
        _, smry_high = Flash().solve([feed])
        _, smry_low  = Flash().solve([feed], pressure_bar=0.5)
        assert smry_low["vapor_fraction"] > smry_high["vapor_fraction"]

    def test_unknown_component_raises(self):
        feed = Stream("f", 25.0, 1.0, 1.0, {"unobtanium": 1.0})
        with pytest.raises(SimulationError, match="unknown"):
            Flash().solve([feed])

    def test_wrong_inlet_count_raises(self):
        with pytest.raises(SimulationError, match="exactly 1"):
            Flash().solve([benz_tol(), water_stream()])


# ══════════════════════════════════════════════════════════════════════════════
# Pump
# ══════════════════════════════════════════════════════════════════════════════

class TestPump:
    def test_pressure_increased(self):
        feed = water_stream(P=1.0)
        outs, smry = Pump().solve([feed], delta_P_bar=5.0)
        assert outs[0].pressure == pytest.approx(6.0)

    def test_shaft_work_positive(self):
        feed = water_stream()
        outs, smry = Pump().solve([feed], delta_P_bar=5.0, efficiency=0.75)
        assert smry["shaft_work_W"] > 0

    def test_higher_efficiency_less_work(self):
        feed = water_stream()
        _, s1 = Pump().solve([feed], delta_P_bar=5.0, efficiency=0.5)
        _, s2 = Pump().solve([feed], delta_P_bar=5.0, efficiency=0.9)
        assert s1["shaft_work_W"] > s2["shaft_work_W"]

    def test_zero_delta_P_no_work(self):
        feed = water_stream()
        outs, smry = Pump().solve([feed], delta_P_bar=0.0)
        assert smry["shaft_work_W"] == pytest.approx(0.0)

    def test_flow_unchanged(self):
        feed = water_stream(F=3.0)
        outs, _ = Pump().solve([feed], delta_P_bar=2.0)
        assert outs[0].flow == pytest.approx(3.0)

    def test_composition_unchanged(self):
        feed = benz_tol()
        outs, _ = Pump().solve([feed], delta_P_bar=2.0)
        assert outs[0].composition == pytest.approx(feed.composition, abs=1e-9)

    def test_vapor_inlet_issues_warning(self):
        feed = Stream("vap", 100.0, 1.0, 1.0, {"water": 1.0}, vapor_fraction=0.5)
        _, smry = Pump().solve([feed], delta_P_bar=2.0)
        assert any("vapor_fraction" in w for w in smry["warnings"])

    def test_negative_delta_P_raises(self):
        with pytest.raises(SimulationError, match="non-negative"):
            Pump().solve([water_stream()], delta_P_bar=-1.0)

    def test_zero_efficiency_raises(self):
        with pytest.raises(SimulationError, match="efficiency"):
            Pump().solve([water_stream()], delta_P_bar=1.0, efficiency=0.0)

    def test_shaft_work_scales_with_delta_P(self):
        feed = water_stream()
        _, s1 = Pump().solve([feed], delta_P_bar=1.0)
        _, s2 = Pump().solve([feed], delta_P_bar=2.0)
        assert s2["shaft_work_W"] == pytest.approx(2 * s1["shaft_work_W"], rel=1e-6)


# ══════════════════════════════════════════════════════════════════════════════
# FlowsheetSolver
# ══════════════════════════════════════════════════════════════════════════════

class TestFlowsheetSolver:
    # ── helpers ───────────────────────────────────────────────────────────────

    @staticmethod
    def _feed_node(node_id: str, **data_overrides) -> dict:
        data = {
            "composition": {"benzene": 0.5, "toluene": 0.5},
            "temperature_C": 25.0,
            "pressure_bar": 1.0,
            "flow_mol_s": 1.0,
        }
        data.update(data_overrides)
        return {"id": node_id, "type": "feed", "data": data, "position": {"x": 0, "y": 0}}

    @staticmethod
    def _node(node_id: str, node_type: str, **data) -> dict:
        return {"id": node_id, "type": node_type, "data": data, "position": {"x": 100, "y": 0}}

    @staticmethod
    def _edge(edge_id: str, src: str, tgt: str, handle: str | None = None) -> dict:
        e: dict = {"id": edge_id, "source": src, "target": tgt}
        if handle is not None:
            e["source_handle"] = handle
        return e

    # ── simple topologies ─────────────────────────────────────────────────────

    def test_single_feed_to_product(self):
        nodes = [
            self._feed_node("N1"),
            self._node("N2", "product"),
        ]
        edges = [self._edge("E1", "N1", "N2")]
        result = FlowsheetSolver(nodes, edges).solve()
        assert result["converged"] is True
        assert "E1" in result["streams"]
        stream = result["streams"]["E1"]
        assert stream["flow"] == pytest.approx(1.0)

    def test_feed_mixer_product(self):
        nodes = [
            self._feed_node("N1", flow_mol_s=1.0),
            self._feed_node("N2", flow_mol_s=2.0),
            self._node("N3", "mixer"),
            self._node("N4", "product"),
        ]
        edges = [
            self._edge("E1", "N1", "N3"),
            self._edge("E2", "N2", "N3"),
            self._edge("E3", "N3", "N4"),
        ]
        result = FlowsheetSolver(nodes, edges).solve()
        assert result["converged"] is True
        # Outlet of mixer = 3 mol/s
        assert result["streams"]["E3"]["flow"] == pytest.approx(3.0)

    def test_feed_splitter_two_products(self):
        nodes = [
            self._feed_node("N1", flow_mol_s=10.0),
            self._node("N2", "splitter", fractions=[0.4, 0.6]),
            self._node("N3", "product"),
            self._node("N4", "product"),
        ]
        edges = [
            self._edge("E1", "N1", "N2"),
            self._edge("E2", "N2", "N3", handle="0"),
            self._edge("E3", "N2", "N4", handle="1"),
        ]
        result = FlowsheetSolver(nodes, edges).solve()
        assert result["converged"] is True
        assert result["streams"]["E2"]["flow"] == pytest.approx(4.0, rel=1e-4)
        assert result["streams"]["E3"]["flow"] == pytest.approx(6.0, rel=1e-4)

    def test_flash_drum_liquid_vapor_products(self):
        nodes = [
            self._feed_node("N1", temperature_C=95.0, pressure_bar=1.0),
            self._node("N2", "flash_drum"),
            self._node("N3", "product"),
            self._node("N4", "product"),
        ]
        edges = [
            self._edge("E1", "N1", "N2"),
            self._edge("E2", "N2", "N3", handle="0"),   # liquid
            self._edge("E3", "N2", "N4", handle="1"),   # vapour
        ]
        result = FlowsheetSolver(nodes, edges).solve()
        assert result["converged"] is True
        liq = result["streams"]["E2"]
        vap = result["streams"]["E3"]
        assert liq["flow"] + vap["flow"] == pytest.approx(1.0, rel=1e-4)
        assert vap["vapor_fraction"] == pytest.approx(1.0)
        assert liq["vapor_fraction"] == pytest.approx(0.0)

    def test_heater_raises_temperature(self):
        Cp_benz_tol = mixture_Cp_liquid({"benzene": 0.5, "toluene": 0.5})
        duty = Cp_benz_tol * 1.0 * 50.0   # ~50°C rise for 1 mol/s
        nodes = [
            self._feed_node("N1", temperature_C=25.0),
            self._node("N2", "heat_exchanger", mode="duty", duty_W=duty),
            self._node("N3", "product"),
        ]
        edges = [self._edge("E1", "N1", "N2"), self._edge("E2", "N2", "N3")]
        result = FlowsheetSolver(nodes, edges).solve()
        assert result["converged"] is True
        assert result["streams"]["E2"]["temperature"] > 25.0

    def test_pump_increases_pressure(self):
        nodes = [
            self._feed_node("N1", pressure_bar=1.0),
            self._node("N2", "pump", delta_P_bar=4.0, efficiency=0.8),
            self._node("N3", "product"),
        ]
        edges = [self._edge("E1", "N1", "N2"), self._edge("E2", "N2", "N3")]
        result = FlowsheetSolver(nodes, edges).solve()
        assert result["converged"] is True
        assert result["streams"]["E2"]["pressure"] == pytest.approx(5.0)

    def test_pfr_reduces_reactant(self):
        nodes = [
            {
                "id": "N1", "type": "feed",
                "data": {
                    "composition": {"benzene": 1.0},
                    "temperature_C": 25.0, "pressure_bar": 1.0, "flow_mol_s": 1.0,
                },
                "position": {"x": 0, "y": 0},
            },
            {
                "id": "N2", "type": "pfr",
                "data": {
                    "stoichiometry": {"benzene": -1.0, "toluene": 1.0},
                    "conversion": 0.9,
                    "delta_Hrxn_J_mol": 0.0,
                },
                "position": {"x": 100, "y": 0},
            },
            self._node("N3", "product"),
        ]
        edges = [self._edge("E1", "N1", "N2"), self._edge("E2", "N2", "N3")]
        result = FlowsheetSolver(nodes, edges).solve()
        assert result["converged"] is True
        # benzene fraction should be low (10% remaining)
        stream = result["streams"]["E2"]
        assert stream["composition"]["benzene"] == pytest.approx(0.1 / 1.0, rel=0.05)

    # ── error handling ────────────────────────────────────────────────────────

    def test_cycle_handled_via_recycle_solver(self):
        # Cycles are no longer rejected; the recycle solver handles them.
        # A two-mixer loop with no feed trivially converges (passthrough).
        nodes = [
            self._node("N1", "mixer"),
            self._node("N2", "mixer"),
        ]
        edges = [
            self._edge("E1", "N1", "N2"),
            self._edge("E2", "N2", "N1"),   # cycle
        ]
        result = FlowsheetSolver(nodes, edges).solve()
        assert result["convergence_info"]["converged"] is True
        assert len(result["convergence_info"]["tear_streams"]) == 1

    def test_feed_missing_composition_captured_as_warning(self):
        nodes = [
            {"id": "N1", "type": "feed", "data": {}, "position": {"x": 0, "y": 0}},
            self._node("N2", "product"),
        ]
        edges = [self._edge("E1", "N1", "N2")]
        result = FlowsheetSolver(nodes, edges).solve()
        assert result["converged"] is False
        assert any("N1" in w for w in result["warnings"])

    def test_unknown_edge_node_issues_warning(self):
        nodes = [self._feed_node("N1"), self._node("N2", "product")]
        edges = [
            self._edge("E1", "N1", "N2"),
            self._edge("E_bad", "N_ghost", "N2"),   # ghost source
        ]
        result = FlowsheetSolver(nodes, edges).solve()
        assert any("unknown" in w.lower() for w in result["warnings"])

    def test_product_node_no_inlet_issues_warning(self):
        nodes = [self._node("N1", "product")]
        result = FlowsheetSolver(nodes, []).solve()
        assert any("no inlet" in w for w in result["warnings"])

    def test_unknown_node_type_issues_warning(self):
        nodes = [
            self._feed_node("N1"),
            self._node("N2", "flux_capacitor"),
            self._node("N3", "product"),
        ]
        edges = [self._edge("E1", "N1", "N2"), self._edge("E2", "N2", "N3")]
        result = FlowsheetSolver(nodes, edges).solve()
        assert any("flux_capacitor" in w for w in result["warnings"])

    def test_empty_flowsheet(self):
        result = FlowsheetSolver([], []).solve()
        assert result["converged"] is True
        assert result["streams"] == {}

    # ── energy balance ────────────────────────────────────────────────────────

    def test_energy_balance_keys_present(self):
        nodes = [self._feed_node("N1"), self._node("N2", "product")]
        result = FlowsheetSolver(nodes, [self._edge("E1", "N1", "N2")]).solve()
        eb = result["energy_balance"]
        assert "total_duty_kW" in eb
        assert "heating_kW"    in eb
        assert "cooling_kW"    in eb

    def test_heater_contributes_to_heating_kW(self):
        duty = 50_000.0   # 50 kW
        nodes = [
            self._feed_node("N1"),
            self._node("N2", "heat_exchanger", mode="duty", duty_W=duty),
            self._node("N3", "product"),
        ]
        edges = [self._edge("E1", "N1", "N2"), self._edge("E2", "N2", "N3")]
        result = FlowsheetSolver(nodes, edges).solve()
        assert result["energy_balance"]["heating_kW"] == pytest.approx(50.0, rel=0.01)


# ══════════════════════════════════════════════════════════════════════════════
# DistillationShortcut — FUG method
# ══════════════════════════════════════════════════════════════════════════════

def _btx_feed(F: float = 100.0 / 3600.0) -> Stream:
    """Classic Seader & Henley BTX feed: 45 % benzene, 35 % toluene, 20 % xylene.

    F defaults to 100 kmol/hr expressed in mol/s.
    """
    return Stream(
        "btx_feed",
        temperature=98.0,   # ≈ bubble point at 1 atm
        pressure=1.013,
        flow=F,
        composition={"benzene": 0.45, "toluene": 0.35, "xylene": 0.20},
        vapor_fraction=0.0,
    )


class TestDistillationShortcut:

    # ── basic solve ─────────────────────────────────────────────────────────────

    def test_solve_returns_two_streams(self):
        feed = _btx_feed()
        outlets, summary = DistillationShortcut().solve(
            [feed],
            light_key="toluene",
            heavy_key="xylene",
            lk_recovery=0.99,
            hk_recovery=0.99,
            reflux_ratio=2.0,
        )
        assert len(outlets) == 2

    def test_mass_balance(self):
        feed = _btx_feed()
        outlets, _ = DistillationShortcut().solve(
            [feed],
            light_key="toluene",
            heavy_key="xylene",
            lk_recovery=0.99,
            hk_recovery=0.99,
            reflux_ratio=2.0,
        )
        assert outlets[0].flow + outlets[1].flow == pytest.approx(feed.flow, rel=1e-6)

    def test_lk_recovery_in_distillate(self):
        feed = _btx_feed()
        outlets, _ = DistillationShortcut().solve(
            [feed],
            light_key="toluene",
            heavy_key="xylene",
            lk_recovery=0.99,
            hk_recovery=0.99,
            reflux_ratio=2.0,
        )
        distillate, bottoms = outlets
        tol_D = distillate.flow * distillate.composition["toluene"]
        tol_B = bottoms.flow   * bottoms.composition["toluene"]
        recovery = tol_D / (tol_D + tol_B)
        assert recovery == pytest.approx(0.99, rel=1e-4)

    def test_hk_recovery_in_bottoms(self):
        feed = _btx_feed()
        outlets, _ = DistillationShortcut().solve(
            [feed],
            light_key="toluene",
            heavy_key="xylene",
            lk_recovery=0.99,
            hk_recovery=0.99,
            reflux_ratio=2.0,
        )
        distillate, bottoms = outlets
        xyl_B = bottoms.flow   * bottoms.composition["xylene"]
        xyl_D = distillate.flow * distillate.composition["xylene"]
        recovery = xyl_B / (xyl_B + xyl_D)
        assert recovery == pytest.approx(0.99, rel=1e-4)

    def test_benzene_mostly_in_distillate(self):
        """Benzene is lighter than the LK (toluene) — expect ≥ 99.9 % in distillate."""
        feed = _btx_feed()
        outlets, _ = DistillationShortcut().solve(
            [feed],
            light_key="toluene",
            heavy_key="xylene",
            lk_recovery=0.99,
            hk_recovery=0.99,
            reflux_ratio=2.0,
        )
        distillate, bottoms = outlets
        benz_D = distillate.flow * distillate.composition["benzene"]
        benz_total = feed.flow * feed.composition["benzene"]
        assert benz_D / benz_total >= 0.999 - 1e-9

    # ── FUG parameter ranges ────────────────────────────────────────────────────

    def test_N_min_positive(self):
        _, summary = DistillationShortcut().solve(
            [_btx_feed()],
            light_key="toluene",
            heavy_key="xylene",
            lk_recovery=0.99,
            hk_recovery=0.99,
            reflux_ratio=2.0,
        )
        assert summary["N_min"] > 0

    def test_R_min_positive(self):
        _, summary = DistillationShortcut().solve(
            [_btx_feed()],
            light_key="toluene",
            heavy_key="xylene",
            lk_recovery=0.99,
            hk_recovery=0.99,
            reflux_ratio=2.0,
        )
        assert summary["R_min"] > 0

    def test_N_actual_gt_N_min(self):
        _, summary = DistillationShortcut().solve(
            [_btx_feed()],
            light_key="toluene",
            heavy_key="xylene",
            lk_recovery=0.99,
            hk_recovery=0.99,
            reflux_ratio=2.0,
        )
        assert summary["N_actual"] > summary["N_min"]

    def test_N_actual_in_reasonable_range(self):
        """Molokanov correlation for this system should give 8 – 30 actual stages."""
        _, summary = DistillationShortcut().solve(
            [_btx_feed()],
            light_key="toluene",
            heavy_key="xylene",
            lk_recovery=0.99,
            hk_recovery=0.99,
            reflux_ratio=2.0,
        )
        assert 8 <= summary["N_actual"] <= 30

    def test_R_gt_R_min_enforced(self):
        """reflux_ratio must exceed R_min — we first get R_min, then try R < R_min."""
        feed = _btx_feed()
        _, summary = DistillationShortcut().solve(
            [feed],
            light_key="toluene",
            heavy_key="xylene",
            lk_recovery=0.99,
            hk_recovery=0.99,
            reflux_ratio=5.0,   # definitely above R_min
        )
        R_min = summary["R_min"]
        with pytest.raises(SimulationError, match="R_min"):
            DistillationShortcut().solve(
                [feed],
                light_key="toluene",
                heavy_key="xylene",
                lk_recovery=0.99,
                hk_recovery=0.99,
                reflux_ratio=max(0.0, R_min - 0.1),
            )

    def test_alpha_lk_gt_1(self):
        _, summary = DistillationShortcut().solve(
            [_btx_feed()],
            light_key="toluene",
            heavy_key="xylene",
            lk_recovery=0.99,
            hk_recovery=0.99,
            reflux_ratio=2.0,
        )
        assert summary["alpha_lk_hk"] > 1.0

    # ── energy balance ──────────────────────────────────────────────────────────

    def test_condenser_duty_positive(self):
        _, summary = DistillationShortcut().solve(
            [_btx_feed()],
            light_key="toluene",
            heavy_key="xylene",
            lk_recovery=0.99,
            hk_recovery=0.99,
            reflux_ratio=2.0,
        )
        assert summary["condenser_duty_kW"] > 0

    # ── CAS number input ────────────────────────────────────────────────────────

    def test_cas_number_input(self):
        """light_key and heavy_key can be CAS numbers."""
        feed = _btx_feed()
        outlets_cas, summary_cas = DistillationShortcut().solve(
            [feed],
            light_key="108-88-3",   # toluene CAS
            heavy_key="106-42-3",   # p-xylene CAS
            lk_recovery=0.99,
            hk_recovery=0.99,
            reflux_ratio=2.0,
        )
        outlets_id, summary_id = DistillationShortcut().solve(
            [feed],
            light_key="toluene",
            heavy_key="xylene",
            lk_recovery=0.99,
            hk_recovery=0.99,
            reflux_ratio=2.0,
        )
        assert summary_cas["N_min"] == pytest.approx(summary_id["N_min"], rel=1e-9)

    # ── Peng-Robinson property package ─────────────────────────────────────────

    def test_pr_package_gives_positive_N_min(self):
        _, summary = DistillationShortcut().solve(
            [_btx_feed()],
            light_key="toluene",
            heavy_key="xylene",
            lk_recovery=0.99,
            hk_recovery=0.99,
            reflux_ratio=2.0,
            property_package="peng_robinson",
        )
        assert summary["N_min"] > 0

    # ── validation errors ───────────────────────────────────────────────────────

    def test_missing_light_key_raises(self):
        feed = Stream("f", 25.0, 1.0, 1.0, {"benzene": 0.5, "toluene": 0.5})
        with pytest.raises(SimulationError):
            DistillationShortcut().solve(
                [feed],
                light_key="xylene",    # not in feed
                heavy_key="toluene",
                lk_recovery=0.99,
                hk_recovery=0.99,
                reflux_ratio=2.0,
            )

    def test_wrong_inlet_count_raises(self):
        feed = _btx_feed()
        with pytest.raises(SimulationError):
            DistillationShortcut().solve(
                [feed, feed],
                light_key="toluene",
                heavy_key="xylene",
                lk_recovery=0.99,
                hk_recovery=0.99,
                reflux_ratio=2.0,
            )

    def test_lk_recovery_out_of_range_raises(self):
        with pytest.raises(SimulationError):
            DistillationShortcut().solve(
                [_btx_feed()],
                light_key="toluene",
                heavy_key="xylene",
                lk_recovery=1.0,    # must be strictly < 1
                hk_recovery=0.99,
                reflux_ratio=2.0,
            )

    # ── binary benzene-toluene (no lighter-than-LK components) ─────────────────

    def test_binary_benzene_toluene(self):
        feed = Stream(
            "bt_feed", 80.0, 1.013, 1.0,
            {"benzene": 0.5, "toluene": 0.5}, 0.0,
        )
        outlets, summary = DistillationShortcut().solve(
            [feed],
            light_key="benzene",
            heavy_key="toluene",
            lk_recovery=0.95,
            hk_recovery=0.95,
            reflux_ratio=3.0,
        )
        assert summary["N_min"] > 0
        assert summary["R_min"] > 0
        assert summary["N_actual"] > summary["N_min"]
        assert outlets[0].flow + outlets[1].flow == pytest.approx(feed.flow, rel=1e-6)
