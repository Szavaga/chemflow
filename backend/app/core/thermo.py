"""
Thermodynamic property functions — ideal gas / ideal liquid assumptions.

All mixture rules are mole-fraction weighted (ideal mixing).

TODO: Replace activity coefficients with NRTL/UNIQUAC for non-ideal liquid
      mixtures, and use Peng-Robinson EOS for rigorous vapour-phase enthalpy
      and compressibility corrections.
"""

from __future__ import annotations

import numpy as np

from app.core.simulation import COMPONENT_LIBRARY


# ── Thermodynamic exception types ─────────────────────────────────────────────

class ThermodynamicError(ValueError):
    """Raised when a thermodynamic calculation cannot proceed (e.g. no valid Z root)."""


class MissingPropertyError(ValueError):
    """Raised when a required pure-component property is absent from the database."""

# ---------------------------------------------------------------------------
# Extended pure-component data not stored in ChemComponent (Antoine only).
#
#   Index 0 — Cp_liq   : liquid molar heat capacity,          J/(mol·K)
#   Index 1 — Cp_ig    : ideal-gas molar heat capacity,       J/(mol·K)
#   Index 2 — delta_Hvap: heat of vaporisation at normal b.p., J/mol
#   Index 3 — rho_liq  : liquid density at ~25 °C,            kg/m³
#
# Sources: NIST WebBook, Perry's Chemical Engineers' Handbook (8th ed.)
# ---------------------------------------------------------------------------
_EXTRA: dict[str, tuple[float, float, float, float]] = {
    #             Cp_liq   Cp_ig  delta_Hvap  rho_liq
    "benzene":   (136.0,   82.4,   30_720.0,   879.0),
    "toluene":   (157.0,  104.0,   33_180.0,   867.0),
    "ethanol":   (112.0,   65.3,   38_600.0,   789.0),
    "water":     ( 75.3,   33.6,   40_650.0,   997.0),
    "methanol":  ( 81.0,   43.9,   35_270.0,   791.0),
    "acetone":   (125.0,   74.9,   29_100.0,   791.0),
    "n_hexane":  (195.0,  143.1,   28_850.0,   659.0),
    "n_heptane": (224.0,  165.9,   31_770.0,   684.0),
    # Light hydrocarbons / gases (properties at/near normal boiling point)
    "methane":         ( 54.0,   35.7,    8_190.0,   422.0),
    "ethane":          ( 68.5,   52.5,   14_720.0,   546.0),
    "propane":         ( 96.2,   73.6,   15_700.0,   493.0),
    "n_butane":        (140.9,   97.5,   22_390.0,   579.0),
    "isobutane":       (130.5,   96.7,   21_300.0,   551.0),
    "n_pentane":       (167.2,  120.2,   25_770.0,   626.0),
    "isopentane":      (164.8,  118.9,   24_690.0,   620.0),
    "cyclohexane":     (156.0,  106.3,   29_970.0,   779.0),
    # Permanent gases
    "hydrogen":        ( 29.1,   28.8,      904.0,    71.0),
    "nitrogen":        ( 56.0,   29.1,    5_570.0,   809.0),
    "carbon_dioxide":  ( 37.1,   37.1,   15_326.0,   770.0),
    "hydrogen_sulfide":( 78.0,   34.2,   18_680.0,   993.0),
    # Other common solvents / process chemicals
    "acetic_acid":     (123.0,   66.5,   23_700.0,  1049.0),
    "chloroform":      (116.0,   65.7,   29_240.0,  1489.0),
    "diethyl_ether":   (172.0,  112.0,   26_520.0,   713.0),
}

# Fallback for any component not in _EXTRA (generic organic liquid-like values).
_DEFAULT_EXTRA: tuple[float, float, float, float] = (100.0, 80.0, 35_000.0, 800.0)


def _extra(component_id: str) -> tuple[float, float, float, float]:
    """Return (Cp_liq, Cp_ig, delta_Hvap, rho_liq) for a component."""
    return _EXTRA.get(component_id, _DEFAULT_EXTRA)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def mixture_Cp_liquid(composition: dict[str, float]) -> float:
    """
    Molar heat capacity of the liquid mixture, J/(mol·K).

    Ideal mixing: Cp_mix = Σ x_i · Cp_liq_i
    """
    return sum(x * _extra(cid)[0] for cid, x in composition.items())


def mixture_Cp_ig(composition: dict[str, float]) -> float:
    """
    Molar heat capacity of the ideal-gas mixture, J/(mol·K).

    Ideal mixing: Cp_mix = Σ y_i · Cp_ig_i
    """
    return sum(x * _extra(cid)[1] for cid, x in composition.items())


def mixture_enthalpy(
    composition: dict[str, float],
    T_C: float,
    vapor_fraction: float = 0.0,
) -> float:
    """
    Molar enthalpy of a stream relative to *liquid at 0 °C*, J/mol.

    H = (1 − ψ)·Cp_liq·T  +  ψ·(Cp_ig·T + ΔHvap_mix)

    where ψ = vapor_fraction and T is in °C (numerically equal to ΔT from
    the 0 °C reference since Cp is taken as constant — ideal assumption).

    TODO: integrate Cp(T) polynomials and PR-EOS departure functions for
          rigorous enthalpy at high T/P.
    """
    Cp_liq = mixture_Cp_liquid(composition)
    Cp_ig  = mixture_Cp_ig(composition)
    dHvap  = sum(x * _extra(cid)[2] for cid, x in composition.items())

    H_liq = Cp_liq * T_C
    H_vap = Cp_ig  * T_C + dHvap
    return (1.0 - vapor_fraction) * H_liq + vapor_fraction * H_vap


def mixture_MW(composition: dict[str, float]) -> float:
    """
    Mean molecular weight of the mixture, g/mol.

    Falls back to 100 g/mol for components not in COMPONENT_LIBRARY.
    """
    return sum(
        x * (COMPONENT_LIBRARY[cid].molecular_weight if cid in COMPONENT_LIBRARY else 100.0)
        for cid, x in composition.items()
    )


def mixture_density_liquid(composition: dict[str, float]) -> float:
    """
    Liquid mixture density, kg/m³.

    Ideal volume mixing (additive molar volumes):
        V_mix = Σ x_i · (MW_i / ρ_i)   [m³/mol, MW in kg/mol]
        ρ_mix = MW_mix / V_mix

    TODO: add excess volume correlations for polar/associating mixtures.
    """
    MW_mix = mixture_MW(composition)       # g/mol

    V_mix = 0.0   # m³/mol
    for cid, x in composition.items():
        MW_i  = COMPONENT_LIBRARY[cid].molecular_weight if cid in COMPONENT_LIBRARY \
                else _DEFAULT_EXTRA[0]     # fallback MW ≈ 100 g/mol
        rho_i = _extra(cid)[3]            # kg/m³
        V_mix += x * (MW_i * 1e-3) / rho_i   # (kg/mol) / (kg/m³) = m³/mol

    if V_mix < 1e-15:
        return 800.0   # safety fallback

    return (MW_mix * 1e-3) / V_mix        # kg/m³


# ── Peng-Robinson EoS ─────────────────────────────────────────────────────────

class PengRobinson:
    """Peng-Robinson equation of state for mixture VLE calculations.

    All thermodynamic methods use SI units internally:
        T  — Kelvin
        P  — Pascal
        a  — J²·s²/mol²  (Pa·m⁶/mol²)
        b  — m³/mol

    Parameters are loaded from COMPONENT_LIBRARY (Tc, Pc, omega).
    Raises MissingPropertyError for components not in the library.
    """

    _R: float = 8.314  # J/(mol·K)

    def __init__(self, components: list[str]) -> None:
        missing = [c for c in components if c not in COMPONENT_LIBRARY]
        if missing:
            raise MissingPropertyError(
                f"Components not in library: {missing}. "
                f"Available: {sorted(COMPONENT_LIBRARY)}"
            )
        n = len(components)
        self.Tc    = np.zeros(n)   # K
        self.Pc    = np.zeros(n)   # Pa
        self.omega = np.zeros(n)
        for i, cid in enumerate(components):
            c = COMPONENT_LIBRARY[cid]
            if c.Tc <= 0 or c.Pc <= 0:
                raise MissingPropertyError(
                    f"Component '{cid}' has Tc={c.Tc} K or Pc={c.Pc} bar — "
                    "both must be positive."
                )
            self.Tc[i]    = c.Tc
            self.Pc[i]    = c.Pc * 1e5   # bar → Pa
            self.omega[i] = c.omega

    # ── pure-component helpers ────────────────────────────────────────────────

    def _alpha(self, T: float, omega: float, Tc: float) -> float:
        """Soave alpha function for a single component."""
        kappa = 0.37464 + 1.54226 * omega - 0.26992 * omega**2
        Tr = T / Tc
        return (1.0 + kappa * (1.0 - np.sqrt(Tr))) ** 2

    def _a_pure(self, T: float, i: int) -> float:
        """Attractive parameter a(T) for component i, Pa·m⁶/mol²."""
        R = self._R
        a_c = 0.45724 * R**2 * self.Tc[i]**2 / self.Pc[i]
        return a_c * self._alpha(T, self.omega[i], self.Tc[i])

    def _b_pure(self, i: int) -> float:
        """Repulsive parameter b for component i, m³/mol."""
        return 0.07780 * self._R * self.Tc[i] / self.Pc[i]

    # ── mixing rules ──────────────────────────────────────────────────────────

    def _mix_params(
        self,
        T: float,
        y: np.ndarray,
        kij: np.ndarray | None = None,
    ) -> tuple[float, float, np.ndarray, np.ndarray]:
        """Van der Waals one-fluid mixing rules.

        Returns (a_mix, b_mix, a_i, a_ij) where a_ij is the n×n cross-parameter
        matrix used later in the fugacity coefficient formula.
        """
        n = len(y)
        a_i = np.array([self._a_pure(T, i) for i in range(n)])
        b_i = np.array([self._b_pure(i)    for i in range(n)])
        if kij is None:
            kij = np.zeros((n, n))
        # a_ij = sqrt(a_i * a_j) * (1 - kij)
        a_ij  = np.outer(np.sqrt(a_i), np.sqrt(a_i)) * (1.0 - kij)
        a_mix = float(np.sum(np.outer(y, y) * a_ij))
        b_mix = float(np.dot(y, b_i))
        return a_mix, b_mix, a_i, a_ij

    # ── cubic Z-factor solver ─────────────────────────────────────────────────

    def _solve_Z(
        self, T: float, P: float, y: np.ndarray, phase: str
    ) -> float:
        """Solve the PR cubic for Z.

        phase="vapor"  → largest real root above B
        phase="liquid" → smallest real root above B
        """
        R = self._R
        a_mix, b_mix, _, _ = self._mix_params(T, y)
        A = a_mix * P / (R * T) ** 2
        B = b_mix * P / (R * T)

        # Z³ - (1-B)Z² + (A-3B²-2B)Z - (AB-B²-B³) = 0
        coeffs = [
            1.0,
            -(1.0 - B),
            (A - 3.0 * B**2 - 2.0 * B),
            -(A * B - B**2 - B**3),
        ]
        roots = np.roots(coeffs)
        real_roots = roots[
            (np.abs(roots.imag) < 1e-6) & (roots.real > B)
        ].real

        if len(real_roots) == 0:
            raise ThermodynamicError(
                f"No valid Z roots found at T={T:.1f} K, P={P:.0f} Pa. "
                "Check that feed conditions are above the triple point."
            )

        if phase == "vapor":
            return float(np.max(real_roots))
        return float(np.min(real_roots))

    # ── fugacity coefficients ─────────────────────────────────────────────────

    def fugacity_coefficients(
        self, T: float, P: float, y: np.ndarray, phase: str
    ) -> np.ndarray:
        """Return ln(φ_i) for each component.

        T in K, P in Pa, y mole-fraction array (liquid x or vapor y).
        phase must be "liquid" or "vapor" to select the correct Z root.
        """
        R    = self._R
        Z    = self._solve_Z(T, P, y, phase)
        a_mix, b_mix, _, a_ij = self._mix_params(T, y)
        A    = a_mix * P / (R * T) ** 2
        B    = b_mix * P / (R * T)
        b_i  = np.array([self._b_pure(i) for i in range(len(y))])

        sqrt2   = np.sqrt(2.0)
        log_arg = (Z + (1.0 + sqrt2) * B) / (Z + (1.0 - sqrt2) * B)

        ln_phi = np.zeros(len(y))
        for i in range(len(y)):
            sum_ya_ij = 2.0 * float(np.sum(y * a_ij[i, :]))
            ln_phi[i] = (
                b_i[i] / b_mix * (Z - 1.0)
                - np.log(Z - B)
                - A / (2.0 * sqrt2 * B)
                * (sum_ya_ij / a_mix - b_i[i] / b_mix)
                * np.log(log_arg)
            )
        return ln_phi
