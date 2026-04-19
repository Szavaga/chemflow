"""
Thermodynamic property functions — ideal gas / ideal liquid assumptions.

All mixture rules are mole-fraction weighted (ideal mixing).

TODO: Replace activity coefficients with NRTL/UNIQUAC for non-ideal liquid
      mixtures, and use Peng-Robinson EOS for rigorous vapour-phase enthalpy
      and compressibility corrections.
"""

from __future__ import annotations

from app.core.simulation import COMPONENT_LIBRARY

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
