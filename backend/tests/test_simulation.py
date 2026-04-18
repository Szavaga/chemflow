"""Unit tests for the core simulation engine — no DB, no network."""

import pytest

from app.core.simulation import (
    COMPONENT_LIBRARY,
    CSTRInput,
    FlashInput,
    HeatExchangerInput,
    simulate_cstr,
    simulate_flash,
    simulate_heat_exchanger,
)


# ── ChemComponent ──────────────────────────────────────────────────────────────

class TestChemComponent:
    def test_vapor_pressure_water_100c(self):
        """Water at 100 °C should give ~1.013 bar (1 atm)."""
        water = COMPONENT_LIBRARY["water"]
        p = water.vapor_pressure(100.0)
        assert abs(p - 1.013) < 0.05, f"Expected ~1.013 bar, got {p:.4f}"

    def test_vapor_pressure_increases_with_temperature(self):
        benzene = COMPONENT_LIBRARY["benzene"]
        p60 = benzene.vapor_pressure(60.0)
        p80 = benzene.vapor_pressure(80.0)
        assert p80 > p60

    def test_all_components_present(self):
        expected = {"benzene", "toluene", "ethanol", "water",
                    "methanol", "acetone", "n_hexane", "n_heptane"}
        assert expected == set(COMPONENT_LIBRARY.keys())


# ── Flash Drum ─────────────────────────────────────────────────────────────────

class TestFlashDrum:
    def _benzene_toluene(self, T=95.0, P=1.0, z=None):
        # 50/50 benzene/toluene bubble point ≈ 92 °C at 1 bar;
        # use 95 °C to sit firmly in the two-phase envelope.
        return FlashInput(
            components=["benzene", "toluene"],
            feed_flow=100.0,
            feed_composition=z or [0.5, 0.5],
            temperature=T,
            pressure=P,
        )

    def test_two_phase_region_gives_intermediate_vapor_fraction(self):
        result = simulate_flash(self._benzene_toluene(T=95, P=1.0))
        assert result.converged
        assert 0.0 < result.vapor_fraction < 1.0

    def test_material_balance(self):
        inp = self._benzene_toluene(T=95, P=1.0)
        r = simulate_flash(inp)
        total_out = r.liquid_flow + r.vapor_flow
        assert abs(total_out - inp.feed_flow) < 1e-6

    def test_composition_sums_to_one(self):
        r = simulate_flash(self._benzene_toluene(T=95, P=1.0))
        assert abs(sum(r.liquid_composition) - 1.0) < 1e-8
        assert abs(sum(r.vapor_composition) - 1.0) < 1e-8

    def test_benzene_enriched_in_vapor(self):
        """Benzene is more volatile than toluene → higher K → richer in vapour."""
        r = simulate_flash(self._benzene_toluene(T=95, P=1.0))
        assert r.vapor_composition[0] > r.liquid_composition[0]

    def test_all_liquid_at_low_temperature(self):
        r = simulate_flash(self._benzene_toluene(T=20, P=1.0))
        assert r.vapor_fraction == 0.0

    def test_all_vapor_at_high_temperature(self):
        r = simulate_flash(self._benzene_toluene(T=200, P=1.0))
        assert r.vapor_fraction == 1.0

    def test_feed_composition_normalised(self):
        """Unnormalised feed [1, 1] should behave like [0.5, 0.5]."""
        r_raw = simulate_flash(self._benzene_toluene(z=[1.0, 1.0]))
        r_norm = simulate_flash(self._benzene_toluene(z=[0.5, 0.5]))
        assert abs(r_raw.vapor_fraction - r_norm.vapor_fraction) < 1e-10

    def test_unknown_component_raises_key_error(self):
        with pytest.raises(KeyError):
            simulate_flash(FlashInput(
                components=["benzene", "unobtainium"],
                feed_flow=1.0,
                feed_composition=[0.5, 0.5],
                temperature=80,
                pressure=1.0,
            ))

    def test_multicomponent_three_species(self):
        r = simulate_flash(FlashInput(
            components=["benzene", "toluene", "n_hexane"],
            feed_flow=1.0,
            feed_composition=[1 / 3, 1 / 3, 1 / 3],
            temperature=70,
            pressure=1.0,
        ))
        assert r.converged
        assert len(r.liquid_composition) == 3
        assert abs(sum(r.liquid_composition) - 1.0) < 1e-8


# ── CSTR ──────────────────────────────────────────────────────────────────────

class TestCSTR:
    def _default(self, **kw):
        defaults = dict(
            feed_concentration=2.0,
            feed_flow=1.0,
            volume=10.0,
            temperature=60.0,
            pre_exponential=1e6,
            activation_energy=50_000,
            reaction_order=1.0,
        )
        defaults.update(kw)
        return CSTRInput(**defaults)

    def test_conversion_between_zero_and_one(self):
        r = simulate_cstr(self._default())
        assert r.converged
        assert 0.0 < r.conversion < 1.0

    def test_material_balance(self):
        inp = self._default()
        r = simulate_cstr(inp)
        Ca_recovered = inp.feed_concentration * (1 - r.conversion)
        assert abs(Ca_recovered - r.outlet_concentration) < 1e-8

    def test_higher_temperature_gives_higher_conversion(self):
        r60 = simulate_cstr(self._default(temperature=60))
        r80 = simulate_cstr(self._default(temperature=80))
        assert r80.conversion > r60.conversion

    def test_larger_volume_gives_higher_conversion(self):
        r10 = simulate_cstr(self._default(volume=10))
        r50 = simulate_cstr(self._default(volume=50))
        assert r50.conversion > r10.conversion

    def test_first_order_analytical_vs_solver(self):
        """n=1 uses analytical formula; verify it matches the Brent result for n≈1."""
        inp_n1 = self._default(reaction_order=1.0)
        inp_n1_approx = self._default(reaction_order=1.0000001)
        r1 = simulate_cstr(inp_n1)
        r2 = simulate_cstr(inp_n1_approx)
        assert abs(r1.conversion - r2.conversion) < 1e-4

    def test_second_order_kinetics(self):
        r = simulate_cstr(self._default(reaction_order=2.0))
        assert r.converged
        assert 0.0 < r.conversion < 1.0

    def test_residence_time(self):
        inp = self._default(volume=20.0, feed_flow=2.0)
        r = simulate_cstr(inp)
        assert abs(r.residence_time - 10.0) < 1e-10

    def test_space_time_yield(self):
        inp = self._default()
        r = simulate_cstr(inp)
        expected_sty = inp.feed_flow * (inp.feed_concentration - r.outlet_concentration)
        assert abs(r.space_time_yield - expected_sty) < 1e-10


# ── Heat Exchanger ─────────────────────────────────────────────────────────────

class TestHeatExchanger:
    def _default(self, **kw):
        defaults = dict(
            hot_inlet_temp=150.0,
            hot_outlet_temp=90.0,
            hot_flow=2.0,
            hot_Cp=4200.0,
            cold_inlet_temp=25.0,
            cold_flow=3.0,
            cold_Cp=4200.0,
            flow_arrangement="counterflow",
        )
        defaults.update(kw)
        return HeatExchangerInput(**defaults)

    def test_energy_balance(self):
        inp = self._default()
        r = simulate_heat_exchanger(inp)
        Q_hot = inp.hot_flow * inp.hot_Cp * (inp.hot_inlet_temp - inp.hot_outlet_temp)
        Q_cold = inp.cold_flow * inp.cold_Cp * (r.cold_outlet_temp - inp.cold_inlet_temp)
        assert abs(Q_hot - Q_cold) < 1e-4

    def test_cold_outlet_above_cold_inlet(self):
        r = simulate_heat_exchanger(self._default())
        assert r.cold_outlet_temp > self._default().cold_inlet_temp

    def test_cold_outlet_below_hot_inlet(self):
        r = simulate_heat_exchanger(self._default())
        assert r.cold_outlet_temp < self._default().hot_inlet_temp

    def test_heat_duty_positive(self):
        r = simulate_heat_exchanger(self._default())
        assert r.heat_duty > 0

    def test_lmtd_positive(self):
        r = simulate_heat_exchanger(self._default())
        assert r.lmtd > 0

    def test_effectiveness_between_0_and_1(self):
        r = simulate_heat_exchanger(self._default())
        assert 0.0 < r.effectiveness <= 1.0

    def test_counterflow_vs_parallel(self):
        """Counterflow should give higher effectiveness than parallel for same UA."""
        r_cf = simulate_heat_exchanger(self._default(flow_arrangement="counterflow"))
        r_pf = simulate_heat_exchanger(self._default(flow_arrangement="parallel"))
        assert r_cf.effectiveness >= r_pf.effectiveness

    def test_temperature_cross_detected(self):
        """Hot outlet below cold inlet → temperature cross."""
        r = simulate_heat_exchanger(self._default(hot_outlet_temp=10.0))
        assert not r.converged
        assert "cross" in r.message.lower()

    def test_equal_delta_t_lmtd(self):
        """When dT1 == dT2 the LMTD should equal that ΔT (avoids log(1)/0)."""
        # Make a symmetric case: dT1 = dT2 = 50
        # counterflow: dT1 = Th_in - Tc_out, dT2 = Th_out - Tc_in
        # Choose Th_in=100, Th_out=50, Tc_in=0, Tc_out=50 → dT1=50, dT2=50
        r = simulate_heat_exchanger(HeatExchangerInput(
            hot_inlet_temp=100, hot_outlet_temp=50,
            hot_flow=1.0, hot_Cp=1000,
            cold_inlet_temp=0, cold_flow=1.0, cold_Cp=1000,
            flow_arrangement="counterflow",
        ))
        assert r.converged
        assert abs(r.lmtd - 50.0) < 0.1
