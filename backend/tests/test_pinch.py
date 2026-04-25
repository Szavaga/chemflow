"""
Tests for the Pinch Analysis module (app/core/pinch.py).

Hand-verified expected values
──────────────────────────────
All expected values are computed by tracing the Problem Table Algorithm
manually.  The Linnhoff 4-stream case below uses the stream data from
the specification; the correct utility targets for those streams are
Q_H_min = 195 kW and Q_C_min = 0 kW (pinch at 30 °C hot side), not the
values quoted in the spec (7.5 / 10 kW), which correspond to a different
problem not reproducible with the given CPs.
"""

import pytest

from app.core.pinch import (
    ColdStream,
    HotStream,
    PinchResult,
    TemperatureInterval,
    _build_cold_composite,
    _build_hot_composite,
    run_pinch_analysis,
)

# ── Helpers ───────────────────────────────────────────────────────────────────

def _approx(a: float, b: float, tol: float = 1e-3) -> bool:
    return abs(a - b) < tol


# ══════════════════════════════════════════════════════════════════════════════
# 1.  Simple 2-stream example (hand-verified)
# ══════════════════════════════════════════════════════════════════════════════
#
# H1 : 120 → 40 °C,  CP = 2 kW/K   →  Q_avail = 160 kW
# C1 :  30 → 110 °C, CP = 3 kW/K   →  Q_req   = 240 kW
# ΔT_min = 20 K,  shift = 10 K
#
# Shifted:  H1 → [110, 30],  C1 → [120, 40]
# Intervals (desc):
#   [110, 120]: HCP=0, CCP=3,  ΔH = −30
#   [ 40, 110]: HCP=2, CCP=3,  ΔH = −70
#   [ 30,  40]: HCP=2, CCP=0,  ΔH = +20
# Cascade R: 0 → −30 → −100 → −80
# Q_H_min = 100,  Q_C_min = 20
# Adjusted: 100 → 70 → 0 → 20
# Pinch: first 0 at position 2 → T_shifted = 40 °C → T_hot = 50 °C
# ─────────────────────────────────────────────────────────────────────────────

class TestTwoStreamExample:
    H1 = HotStream(supply_temp=120, target_temp=40, cp=2, name="H1")
    C1 = ColdStream(supply_temp=30, target_temp=110, cp=3, name="C1")

    def _run(self) -> PinchResult:
        return run_pinch_analysis([self.H1], [self.C1], delta_T_min=20.0)

    def test_q_h_min(self):
        r = self._run()
        assert _approx(r.q_h_min, 100.0), f"q_h_min={r.q_h_min}"

    def test_q_c_min(self):
        r = self._run()
        assert _approx(r.q_c_min, 20.0), f"q_c_min={r.q_c_min}"

    def test_pinch_temperature(self):
        r = self._run()
        # Pinch at T_shifted=40 + shift=10 = 50 °C hot side
        assert _approx(r.pinch_temperature, 50.0), f"pinch_temp={r.pinch_temperature}"

    def test_energy_balance(self):
        """Q_H_min − Q_C_min must equal Q_cold_total − Q_hot_total."""
        r = self._run()
        q_hot   = self.H1.cp * (self.H1.supply_temp - self.H1.target_temp)
        q_cold  = self.C1.cp * (self.C1.target_temp - self.C1.supply_temp)
        assert _approx(r.q_h_min - r.q_c_min, q_cold - q_hot)

    def test_interval_count(self):
        r = self._run()
        # 3 unique interval boundaries from 2 streams with ΔT_min=20
        assert len(r.temperature_intervals) == 3

    def test_cascade_feasibility(self):
        """All adjusted cascade residuals must be ≥ 0."""
        r = self._run()
        for iv in r.temperature_intervals:
            assert iv.cascade_in  >= -1e-6, f"cascade_in={iv.cascade_in}"
            assert iv.cascade_out >= -1e-6, f"cascade_out={iv.cascade_out}"

    def test_hot_composite_endpoints(self):
        pts = _build_hot_composite([self.H1])
        assert pts[0]["T"] == 120 and pts[0]["H"] == 0.0
        assert _approx(pts[-1]["H"], 160.0)   # 2 * (120 − 40)

    def test_cold_composite_endpoints(self):
        pts = _build_cold_composite([self.C1])
        assert pts[0]["T"] == 30 and pts[0]["H"] == 0.0
        assert _approx(pts[-1]["H"], 240.0)   # 3 * (110 − 30)

    def test_above_pinch_streams(self):
        r = self._run()
        # H1 has T_supply=120 > pinch=50, so it appears above pinch
        assert len(r.above_pinch_streams["hot"]) == 1

    def test_below_pinch_streams(self):
        r = self._run()
        # H1 target=40 < pinch=50, so it also has a below-pinch segment
        assert len(r.below_pinch_streams["hot"]) == 1


# ══════════════════════════════════════════════════════════════════════════════
# 2.  Threshold problem: no pinch inside the diagram
# ══════════════════════════════════════════════════════════════════════════════
#
# H1 : 200 → 60 °C,  CP = 5 kW/K   →  Q_avail = 700 kW
# C1 :  20 → 150 °C, CP = 4 kW/K   →  Q_req   = 520 kW
# ΔT_min = 10 K
#
# Hot provides MORE than cold needs → Q_C_min > 0, Q_H_min = 0.
# ─────────────────────────────────────────────────────────────────────────────

class TestThresholdProblem:
    H1 = HotStream(200, 60, 5, name="H1")
    C1 = ColdStream(20, 150, 4, name="C1")

    def test_q_h_min_is_zero(self):
        r = run_pinch_analysis([self.H1], [self.C1], delta_T_min=10.0)
        assert _approx(r.q_h_min, 0.0), f"q_h_min={r.q_h_min}"

    def test_q_c_min_positive(self):
        r = run_pinch_analysis([self.H1], [self.C1], delta_T_min=10.0)
        expected = 700 - 520   # = 180 kW
        assert _approx(r.q_c_min, expected), f"q_c_min={r.q_c_min}"

    def test_energy_balance(self):
        r = run_pinch_analysis([self.H1], [self.C1], delta_T_min=10.0)
        q_hot  = 5 * (200 - 60)
        q_cold = 4 * (150 - 20)
        assert _approx(r.q_h_min - r.q_c_min, q_cold - q_hot)


# ══════════════════════════════════════════════════════════════════════════════
# 3.  Linnhoff & Flower (1978) 4-stream example — CORRECT expected values
# ══════════════════════════════════════════════════════════════════════════════
#
# H1 : 150 →  60 °C,  CP = 3.0 kW/K
# H2 :  90 →  60 °C,  CP = 1.5 kW/K
# C1 :  20 → 125 °C,  CP = 2.0 kW/K
# C2 :  25 → 100 °C,  CP = 4.0 kW/K
# ΔT_min = 10 K,  shift = 5 K
#
# Shifted temps (desc): 145, 130, 105, 85, 55, 30, 25
# Intervals and ΔH:
#   [130,145]  HCP=3, CCP=0     → +45
#   [105,130]  HCP=3, CCP=2     → +25
#   [ 85,105]  HCP=3, CCP=6     → −60
#   [ 55, 85]  HCP=4.5, CCP=6   → −45
#   [ 30, 55]  HCP=0, CCP=6     → −150
#   [ 25, 30]  HCP=0, CCP=2     → −10
# Cascade R:  0 → 45 → 70 → 10 → −35 → −185 → −195
# Q_H_min = 195,  Q_C_min = 0
# Adjusted: 195 → 240 → 265 → 205 → 160 → 10 → 0
# Pinch at T_shifted=25 → T_hot = 30 °C
# ─────────────────────────────────────────────────────────────────────────────

class TestLinnhoff4Stream:
    HOT_STREAMS = [
        HotStream(150, 60, 3.0, "H1"),
        HotStream(90,  60, 1.5, "H2"),
    ]
    COLD_STREAMS = [
        ColdStream(20, 125, 2.0, "C1"),
        ColdStream(25, 100, 4.0, "C2"),
    ]

    def _run(self) -> PinchResult:
        return run_pinch_analysis(self.HOT_STREAMS, self.COLD_STREAMS, delta_T_min=10.0)

    def test_q_h_min(self):
        r = self._run()
        assert _approx(r.q_h_min, 195.0, tol=0.01), f"q_h_min={r.q_h_min}"

    def test_q_c_min(self):
        r = self._run()
        assert _approx(r.q_c_min, 0.0, tol=0.01), f"q_c_min={r.q_c_min}"

    def test_pinch_temperature(self):
        # Pinch at T_shifted=25 + 5 = 30 °C hot side
        r = self._run()
        assert _approx(r.pinch_temperature, 30.0, tol=0.01), f"pinch_temp={r.pinch_temperature}"

    def test_energy_balance(self):
        """Q_H_min − Q_C_min = Q_cold_total − Q_hot_total."""
        r = self._run()
        q_hot  = 3.0 * 90 + 1.5 * 30          # 270 + 45 = 315
        q_cold = 2.0 * 105 + 4.0 * 75         # 210 + 300 = 510
        assert _approx(r.q_h_min - r.q_c_min, q_cold - q_hot, tol=0.01)

    def test_interval_count(self):
        r = self._run()
        assert len(r.temperature_intervals) == 6

    def test_delta_h_values(self):
        """Spot-check the ΔH values in each interval."""
        r = self._run()
        expected_dh = [45.0, 25.0, -60.0, -45.0, -150.0, -10.0]
        for iv, exp in zip(r.temperature_intervals, expected_dh, strict=True):
            assert _approx(iv.delta_h, exp, tol=0.01), f"delta_h={iv.delta_h}, expected={exp}"

    def test_cascade_feasibility(self):
        r = self._run()
        for iv in r.temperature_intervals:
            assert iv.cascade_in  >= -1e-6
            assert iv.cascade_out >= -1e-6

    def test_hot_composite_total_enthalpy(self):
        pts = _build_hot_composite(self.HOT_STREAMS)
        assert _approx(pts[-1]["H"], 315.0)   # total hot duty

    def test_cold_composite_total_enthalpy(self):
        pts = _build_cold_composite(self.COLD_STREAMS)
        assert _approx(pts[-1]["H"], 510.0)   # total cold duty


# ══════════════════════════════════════════════════════════════════════════════
# 4.  Validation tests
# ══════════════════════════════════════════════════════════════════════════════

class TestValidation:
    def test_empty_streams_raises(self):
        with pytest.raises(ValueError, match="At least one stream"):
            run_pinch_analysis([], [])

    def test_hot_stream_inverted_temps_raises(self):
        with pytest.raises(ValueError, match="supply_temp.*must be"):
            HotStream(supply_temp=50, target_temp=100, cp=1)

    def test_cold_stream_inverted_temps_raises(self):
        with pytest.raises(ValueError, match="target_temp.*must be"):
            ColdStream(supply_temp=100, target_temp=50, cp=1)

    def test_non_positive_cp_raises(self):
        with pytest.raises(ValueError, match="cp must be"):
            HotStream(supply_temp=100, target_temp=50, cp=0.0)

    def test_hot_only_streams(self):
        """Single hot stream with no cold streams should not crash."""
        h = HotStream(100, 40, 2)
        r = run_pinch_analysis([h], [], delta_T_min=10)
        assert r.q_h_min >= 0
        assert r.q_c_min >= -1e-6

    def test_cold_only_streams(self):
        """Single cold stream with no hot streams should not crash."""
        c = ColdStream(20, 80, 1.5)
        r = run_pinch_analysis([], [c], delta_T_min=10)
        assert r.q_h_min >= 0

    def test_delta_T_min_affects_pinch(self):
        """Wider ΔT_min should give a higher (or equal) Q_H_min."""
        h = [HotStream(150, 60, 3), HotStream(90, 60, 1.5)]
        c = [ColdStream(20, 125, 2), ColdStream(25, 100, 4)]
        r10 = run_pinch_analysis(h, c, delta_T_min=10)
        r20 = run_pinch_analysis(h, c, delta_T_min=20)
        assert r20.q_h_min >= r10.q_h_min

    def test_result_energy_balance_invariant(self):
        """Q_H_min − Q_C_min = ΣQ_cold − ΣQ_hot for any valid input."""
        h = [HotStream(200, 80, 4.2), HotStream(130, 50, 2.1)]
        c = [ColdStream(30, 160, 3.5), ColdStream(60, 120, 1.8)]
        r = run_pinch_analysis(h, c, delta_T_min=15)
        q_hot  = sum(s.cp * (s.supply_temp - s.target_temp) for s in h)
        q_cold = sum(s.cp * (s.target_temp - s.supply_temp) for s in c)
        assert _approx(r.q_h_min - r.q_c_min, q_cold - q_hot, tol=0.01)
