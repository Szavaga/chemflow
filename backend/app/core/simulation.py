"""
ChemFlow simulation engine.

Implements steady-state unit operations for common pharma/chemical processes:
  - Flash Drum  : isothermal Rachford-Rice with Raoult's law K-values
  - CSTR        : Arrhenius nth-order kinetics design equation
  - Heat Exchanger: LMTD method + effectiveness cross-check
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import numpy as np
from scipy.optimize import brentq

from app.core.activity import wilson_gammas

R_GAS = 8.314  # J/(mol·K)


# ── Component library ─────────────────────────────────────────────────────────

@dataclass
class ChemComponent:
    """Chemical component with Antoine vapour-pressure constants.

    Antoine equation (base-10 log):
        log10(P_vap / mmHg) = A - B / (T_°C + C)
    """
    name: str
    molecular_weight: float   # g/mol
    Tc: float                 # critical temperature, K
    Pc: float                 # critical pressure, bar
    omega: float              # acentric factor
    antoine_A: float
    antoine_B: float
    antoine_C: float

    def vapor_pressure(self, T_C: float) -> float:
        """Return saturation vapour pressure in bar at T_C °C."""
        log_p = self.antoine_A - self.antoine_B / (T_C + self.antoine_C)
        p_mmhg = 10.0 ** log_p
        return p_mmhg * 1.33322e-3  # mmHg → bar


COMPONENT_LIBRARY: dict[str, ChemComponent] = {
    "benzene": ChemComponent(
        "Benzene", 78.11, 562.2, 48.9, 0.212,
        6.90565, 1211.033, 220.790,
    ),
    "toluene": ChemComponent(
        "Toluene", 92.14, 591.8, 41.1, 0.263,
        6.95464, 1344.800, 219.482,
    ),
    "ethanol": ChemComponent(
        "Ethanol", 46.07, 513.9, 61.4, 0.644,
        8.04494, 1554.300, 222.650,
    ),
    "water": ChemComponent(
        "Water", 18.02, 647.1, 220.6, 0.345,
        8.07131, 1730.630, 233.426,
    ),
    "methanol": ChemComponent(
        "Methanol", 32.04, 512.6, 80.97, 0.565,
        7.87863, 1473.110, 230.000,
    ),
    "acetone": ChemComponent(
        "Acetone", 58.08, 508.2, 47.0, 0.307,
        7.02447, 1161.000, 224.000,
    ),
    "n_hexane": ChemComponent(
        "n-Hexane", 86.18, 507.6, 30.25, 0.301,
        6.87601, 1171.170, 224.408,
    ),
    "n_heptane": ChemComponent(
        "n-Heptane", 100.2, 540.3, 27.40, 0.349,
        6.89585, 1264.370, 216.640,
    ),
    "methane": ChemComponent(
        "Methane", 16.04, 190.6, 46.1, 0.011,
        6.61184, 389.930, 266.696,
    ),
    "ethane": ChemComponent(
        "Ethane", 30.07, 305.3, 48.7, 0.099,
        6.80896, 663.720, 256.681,
    ),
    "propane": ChemComponent(
        "Propane", 44.10, 369.8, 42.5, 0.152,
        6.82973, 813.200, 248.097,
    ),
    "n_butane": ChemComponent(
        "n-Butane", 58.12, 425.1, 38.0, 0.200,
        6.82485, 943.453, 239.711,
    ),
    "isobutane": ChemComponent(
        "Isobutane", 58.12, 408.2, 36.5, 0.181,
        6.78866, 882.800, 240.000,
    ),
    "n_pentane": ChemComponent(
        "n-Pentane", 72.15, 469.7, 33.7, 0.251,
        6.85221, 1064.630, 232.000,
    ),
    "isopentane": ChemComponent(
        "Isopentane", 72.15, 460.4, 33.8, 0.227,
        6.78967, 1020.012, 233.097,
    ),
    "cyclohexane": ChemComponent(
        "Cyclohexane", 84.16, 553.6, 40.7, 0.212,
        6.84498, 1203.526, 222.863,
    ),
    "hydrogen": ChemComponent(
        "Hydrogen", 2.016, 33.2, 13.0, -0.216,
        5.82800, 181.000, 265.700,
    ),
    "nitrogen": ChemComponent(
        "Nitrogen", 28.01, 126.2, 34.0, 0.040,
        6.49457, 255.680, 266.550,
    ),
    "carbon_dioxide": ChemComponent(
        "Carbon Dioxide", 44.01, 304.1, 73.8, 0.225,
        6.81228, 1301.679, 272.200,
    ),
    "hydrogen_sulfide": ChemComponent(
        "Hydrogen Sulfide", 34.08, 373.2, 90.1, 0.090,
        7.05267, 1012.490, 247.100,
    ),
    "acetic_acid": ChemComponent(
        "Acetic Acid", 60.05, 592.7, 57.9, 0.467,
        7.38782, 1533.313, 222.309,
    ),
    "chloroform": ChemComponent(
        "Chloroform", 119.38, 536.4, 54.7, 0.218,
        6.90328, 1163.000, 227.400,
    ),
    "diethyl_ether": ChemComponent(
        "Diethyl Ether", 74.12, 466.7, 36.4, 0.281,
        6.92032, 1064.070, 228.799,
    ),
}


# ── Flash Drum ─────────────────────────────────────────────────────────────────

@dataclass
class FlashInput:
    components: list[str]          # keys into COMPONENT_LIBRARY
    feed_flow: float               # mol/s  (total)
    feed_composition: list[float]  # mole fractions (will be normalised)
    temperature: float             # °C
    pressure: float                # bar


@dataclass
class FlashResult:
    vapor_fraction: float
    liquid_flow: float             # mol/s
    vapor_flow: float              # mol/s
    liquid_composition: list[float]
    vapor_composition: list[float]
    K_values: list[float]
    converged: bool
    message: str


def _rr_objective(psi: float, z: np.ndarray, K: np.ndarray) -> float:
    """Rachford-Rice objective: Σ z_i(K_i-1)/(1+ψ(K_i-1)) = 0."""
    return float(np.sum(z * (K - 1.0) / (1.0 + psi * (K - 1.0))))


def simulate_flash(inp: FlashInput) -> FlashResult:
    """Isothermal flash via Rachford-Rice with Wilson activity coefficients.

    Uses modified Raoult's law  K_i = γ_i(x) · VP_i(T) / P  with successive
    substitution on the liquid composition.  Pairs without Wilson parameters
    default to γ_i = 1 (ideal, Raoult's law).
    """
    comp_ids = inp.components
    comps    = [COMPONENT_LIBRARY[c] for c in comp_ids]
    z = np.array(inp.feed_composition, dtype=float)
    z /= z.sum()
    VP = np.array([c.vapor_pressure(inp.temperature) for c in comps])

    # Initialise K from Wilson γ at feed composition
    x     = z.copy()
    gamma = np.array([wilson_gammas(dict(zip(comp_ids, x.tolist())))[c] for c in comp_ids])
    K     = gamma * VP / inp.pressure

    psi = 0.5
    converged, msg = False, "Maximum iterations reached"

    for _ in range(50):
        if np.sum(z * K) <= 1.0:
            return FlashResult(0.0, inp.feed_flow, 0.0, list(z), [0.0] * len(z),
                               list(K), True, "Sub-cooled liquid — below bubble point")
        if np.sum(z / K) <= 1.0:
            return FlashResult(1.0, 0.0, inp.feed_flow, [0.0] * len(z), list(z),
                               list(K), True, "Superheated vapour — above dew point")

        psi_lo = max(1.0 / (1.0 - float(K.max())) + 1e-9, 1e-9)
        psi_hi = min(1.0 / (1.0 - float(K.min())) - 1e-9, 1.0 - 1e-9)

        try:
            psi = brentq(_rr_objective, psi_lo, psi_hi, args=(z, K), xtol=1e-12, maxiter=300)
        except ValueError as exc:
            converged, msg = False, f"Solver failed: {exc}"
            break

        x_new   = z / (1.0 + psi * (K - 1.0));  x_new /= x_new.sum()
        gamma_new = np.array(
            [wilson_gammas(dict(zip(comp_ids, x_new.tolist())))[c] for c in comp_ids]
        )
        K_new = gamma_new * VP / inp.pressure

        if np.max(np.abs(K_new - K) / np.maximum(K, 1e-10)) < 1e-6:
            x, gamma, K = x_new, gamma_new, K_new
            converged, msg = True, "Converged successfully"
            break
        x, gamma, K = x_new, gamma_new, K_new

    y = K * x;  y /= y.sum()

    return FlashResult(
        vapor_fraction=float(psi),
        liquid_flow=float(inp.feed_flow * (1.0 - psi)),
        vapor_flow=float(inp.feed_flow * psi),
        liquid_composition=x.tolist(),
        vapor_composition=y.tolist(),
        K_values=K.tolist(),
        converged=converged,
        message=msg,
    )


# ── CSTR ──────────────────────────────────────────────────────────────────────

@dataclass
class CSTRInput:
    reactant_name: str = "A"
    feed_concentration: float = 2.0   # mol/L
    feed_flow: float = 1.0            # L/s
    volume: float = 10.0              # L
    temperature: float = 60.0        # °C
    pre_exponential: float = 1e6      # 1/s  (first-order units; generalise as needed)
    activation_energy: float = 50000  # J/mol
    reaction_order: float = 1.0


@dataclass
class CSTRResult:
    conversion: float
    outlet_concentration: float   # mol/L
    outlet_flow: float            # L/s
    reaction_rate: float          # mol/(L·s)
    residence_time: float         # s
    space_time_yield: float       # mol/s consumed
    converged: bool
    message: str


def simulate_cstr(inp: CSTRInput) -> CSTRResult:
    """Steady-state CSTR design equation with Arrhenius rate law (A→B)."""
    T_K = inp.temperature + 273.15
    k = inp.pre_exponential * np.exp(-inp.activation_energy / (R_GAS * T_K))
    tau = inp.volume / inp.feed_flow
    Ca0 = inp.feed_concentration
    n = inp.reaction_order

    if abs(n - 1.0) < 1e-6:
        Ca = Ca0 / (1.0 + k * tau)
        converged, msg = True, "Analytical solution (first-order)"
    else:
        def design_eq(Ca: float) -> float:
            return Ca0 - Ca - k * (Ca ** n) * tau

        try:
            Ca = brentq(design_eq, 1e-15, Ca0, xtol=1e-14, maxiter=300)
            converged, msg = True, f"Converged (order={n:.2f})"
        except ValueError as exc:
            Ca, converged, msg = Ca0 * 0.5, False, f"Solver failed: {exc}"

    X = 1.0 - Ca / Ca0
    rate = k * (Ca ** n)
    sty = inp.feed_flow * (Ca0 - Ca)

    return CSTRResult(
        conversion=float(X),
        outlet_concentration=float(Ca),
        outlet_flow=float(inp.feed_flow),
        reaction_rate=float(rate),
        residence_time=float(tau),
        space_time_yield=float(sty),
        converged=converged,
        message=msg,
    )


# ── Heat Exchanger ─────────────────────────────────────────────────────────────

@dataclass
class HeatExchangerInput:
    hot_inlet_temp: float    # °C
    hot_outlet_temp: float   # °C
    hot_flow: float          # kg/s
    hot_Cp: float            # J/(kg·K)
    cold_inlet_temp: float   # °C
    cold_flow: float         # kg/s
    cold_Cp: float           # J/(kg·K)
    flow_arrangement: str = "counterflow"   # "counterflow" | "parallel"


@dataclass
class HeatExchangerResult:
    cold_outlet_temp: float   # °C
    heat_duty: float          # W
    lmtd: float               # K
    UA: float                 # W/K
    effectiveness: float      # dimensionless
    converged: bool
    message: str


def simulate_heat_exchanger(inp: HeatExchangerInput) -> HeatExchangerResult:
    """Shell-and-tube HEX via LMTD + effectiveness-NTU cross-check."""
    Q = inp.hot_flow * inp.hot_Cp * (inp.hot_inlet_temp - inp.hot_outlet_temp)
    Tc_out = inp.cold_inlet_temp + Q / (inp.cold_flow * inp.cold_Cp)

    Th_in, Th_out = inp.hot_inlet_temp, inp.hot_outlet_temp
    Tc_in = inp.cold_inlet_temp

    if inp.flow_arrangement == "counterflow":
        dT1, dT2 = Th_in - Tc_out, Th_out - Tc_in
    else:
        dT1, dT2 = Th_in - Tc_in, Th_out - Tc_out

    if dT1 <= 0 or dT2 <= 0:
        return HeatExchangerResult(
            float(Tc_out), float(Q), 0.0, 0.0, 0.0, False,
            "Temperature cross detected — check inlet/outlet temperatures",
        )

    lmtd = dT1 if abs(dT1 - dT2) < 1e-6 else (dT1 - dT2) / np.log(dT1 / dT2)
    UA = Q / lmtd if lmtd > 1e-9 else float("inf")

    C_hot = inp.hot_flow * inp.hot_Cp
    C_cold = inp.cold_flow * inp.cold_Cp
    C_min = min(C_hot, C_cold)
    Q_max = C_min * (Th_in - Tc_in) if Th_in > Tc_in else 0.0
    effectiveness = Q / Q_max if Q_max > 0 else 0.0

    return HeatExchangerResult(
        cold_outlet_temp=float(Tc_out),
        heat_duty=float(Q),
        lmtd=float(lmtd),
        UA=float(UA),
        effectiveness=float(effectiveness),
        converged=True,
        message="Converged successfully",
    )
