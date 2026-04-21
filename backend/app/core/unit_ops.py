"""
Steady-state unit operation library for ChemFlow.

Each unit operation:
  - accepts a list of Stream objects plus configuration kwargs
  - returns (outlets: list[Stream], summary: dict)
  - raises SimulationError with a clear message on invalid input

Supported unit operations
--------------------------
  Mixer         — combines N inlets, conserves mass and enthalpy
  Splitter      — splits one inlet into N outlets by specified fractions
  HeatExchanger — single-stream heater/cooler (duty or target-T mode)
  PFR           — conversion-specified plug flow reactor (stoichiometry dict)
  Flash         — isothermal two-phase VLE via Rachford-Rice + Raoult's law
  Pump          — raises liquid pressure; calculates shaft work
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np
from scipy.optimize import brentq

from app.core.activity import wilson_gammas
from app.core.simulation import COMPONENT_LIBRARY
from app.core.thermo import (
    _extra,
    mixture_Cp_ig,
    mixture_Cp_liquid,
    mixture_density_liquid,
    mixture_enthalpy,
    mixture_MW,
)

R_GAS = 8.314   # J/(mol·K)


# ── Exception ─────────────────────────────────────────────────────────────────

class SimulationError(Exception):
    """Raised when a unit operation cannot proceed due to invalid inputs or
    a convergence failure."""


# ── Stream ────────────────────────────────────────────────────────────────────

@dataclass
class Stream:
    """A process stream carrying molar flow, composition, T, and P.

    Attributes
    ----------
    name          : human-readable label for the stream
    temperature   : °C
    pressure      : bar
    flow          : mol/s  (total molar flow)
    composition   : {component_id: mole_fraction}  — must sum to 1 ± 1e-4
    vapor_fraction: 0 = liquid, 1 = vapour, (0, 1) = two-phase
    """

    name: str
    temperature: float
    pressure: float
    flow: float
    composition: dict[str, float]
    vapor_fraction: float = 0.0

    def __post_init__(self) -> None:
        self._validate()

    def _validate(self) -> None:
        if self.flow < 0:
            raise SimulationError(
                f"Stream '{self.name}': flow must be ≥ 0, got {self.flow}"
            )
        if not self.composition:
            raise SimulationError(
                f"Stream '{self.name}': composition must not be empty"
            )
        total = sum(self.composition.values())
        if abs(total - 1.0) > 1e-4:
            raise SimulationError(
                f"Stream '{self.name}': composition sums to {total:.6f}, expected 1.0"
            )
        if not (0.0 <= self.vapor_fraction <= 1.0):
            raise SimulationError(
                f"Stream '{self.name}': vapor_fraction {self.vapor_fraction} not in [0, 1]"
            )

    # -- derived properties ---------------------------------------------------

    @property
    def enthalpy(self) -> float:
        """Molar enthalpy relative to liquid at 0 °C, J/mol."""
        return mixture_enthalpy(self.composition, self.temperature, self.vapor_fraction)

    @property
    def enthalpy_flow(self) -> float:
        """Total enthalpy flow H·F, W (J/s)."""
        return self.enthalpy * self.flow

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "temperature": self.temperature,
            "pressure": self.pressure,
            "flow": self.flow,
            "composition": dict(self.composition),
            "vapor_fraction": self.vapor_fraction,
        }


# ── helpers ───────────────────────────────────────────────────────────────────

def _normalise(composition: dict[str, float]) -> dict[str, float]:
    total = sum(composition.values())
    if total < 1e-15:
        raise SimulationError("Cannot normalise a zero composition vector")
    return {k: v / total for k, v in composition.items()}


def _rr_objective(psi: float, z: np.ndarray, K: np.ndarray) -> float:
    """Rachford-Rice objective: Σ z_i(K_i − 1)/(1 + ψ(K_i − 1)) = 0."""
    return float(np.sum(z * (K - 1.0) / (1.0 + psi * (K - 1.0))))


# ── Mixer ─────────────────────────────────────────────────────────────────────

class Mixer:
    """
    Combines N inlet streams into one outlet.

    Mass balance  : F_out = Σ F_in
    Component bal.: z_out_i = Σ(F_in_j · z_in_j_i) / F_out
    Energy balance: T_out from H_out = Σ(H_in_j · F_in_j) / F_out
    Outlet P      : min(P_in)   (conservative — add a pump downstream if needed)

    TODO: include heat-of-mixing when activity coefficients are available.
    """

    def solve(
        self,
        inlets: list[Stream],
        outlet_name: str = "mix_out",
    ) -> tuple[list[Stream], dict[str, Any]]:
        if not inlets:
            raise SimulationError("Mixer requires at least one inlet stream")

        F_out = sum(s.flow for s in inlets)
        if F_out < 1e-15:
            raise SimulationError("Mixer: total inlet flow is zero")

        # --- component balance -----------------------------------------------
        all_comps: set[str] = set()
        for s in inlets:
            all_comps.update(s.composition)

        z_out: dict[str, float] = {
            comp: sum(s.flow * s.composition.get(comp, 0.0) for s in inlets) / F_out
            for comp in all_comps
        }
        z_out = _normalise(z_out)

        # --- energy balance --------------------------------------------------
        H_out_molar = sum(s.enthalpy_flow for s in inlets) / F_out
        vf_out = sum(s.vapor_fraction * s.flow for s in inlets) / F_out
        T_out = _invert_enthalpy(z_out, H_out_molar, vf_out)

        P_out = min(s.pressure for s in inlets)

        outlet = Stream(outlet_name, T_out, P_out, F_out, z_out, vf_out)
        summary: dict[str, Any] = {
            "n_inlets": len(inlets),
            "outlet_flow_mol_s": F_out,
            "outlet_temperature_C": T_out,
            "outlet_pressure_bar": P_out,
        }
        return [outlet], summary


def _invert_enthalpy(
    composition: dict[str, float],
    H_target: float,
    vapor_fraction: float,
) -> float:
    """
    Solve T from the linear ideal enthalpy model:
        H = Cp_eff · T + ψ · ΔHvap_mix
    where Cp_eff = (1−ψ)·Cp_liq + ψ·Cp_ig.
    """
    Cp_eff = (
        (1.0 - vapor_fraction) * mixture_Cp_liquid(composition)
        + vapor_fraction       * mixture_Cp_ig(composition)
    )
    dHvap = sum(x * _extra(cid)[2] for cid, x in composition.items())
    if Cp_eff < 1e-10:
        return 25.0   # degenerate fallback
    return (H_target - vapor_fraction * dHvap) / Cp_eff


# ── Splitter ──────────────────────────────────────────────────────────────────

class Splitter:
    """
    Splits one inlet into N outlets at specified molar flow fractions.

    Each outlet preserves the inlet T, P, composition, and vapor_fraction.
    ``fractions`` must be non-negative and sum to 1.0 (within 1e-4 tolerance).
    """

    def solve(
        self,
        inlets: list[Stream],
        fractions: list[float],
        outlet_names: list[str] | None = None,
    ) -> tuple[list[Stream], dict[str, Any]]:
        if len(inlets) != 1:
            raise SimulationError(
                f"Splitter expects exactly 1 inlet, got {len(inlets)}"
            )
        feed = inlets[0]

        if not fractions:
            raise SimulationError("Splitter: fractions list is empty")
        if any(f < 0 for f in fractions):
            raise SimulationError("Splitter: all fractions must be non-negative")
        if abs(sum(fractions) - 1.0) > 1e-4:
            raise SimulationError(
                f"Splitter: fractions sum to {sum(fractions):.6f}, must equal 1.0"
            )

        n = len(fractions)
        if outlet_names is None:
            outlet_names = [f"split_out_{i + 1}" for i in range(n)]
        if len(outlet_names) != n:
            raise SimulationError(
                "Splitter: outlet_names length must match fractions length"
            )

        outlets = [
            Stream(
                name=outlet_names[i],
                temperature=feed.temperature,
                pressure=feed.pressure,
                flow=feed.flow * fractions[i],
                composition=dict(feed.composition),
                vapor_fraction=feed.vapor_fraction,
            )
            for i in range(n)
        ]
        summary: dict[str, Any] = {
            "inlet_flow_mol_s": feed.flow,
            "split_fractions": fractions,
            "outlet_flows_mol_s": [s.flow for s in outlets],
        }
        return outlets, summary


# ── HeatExchanger ─────────────────────────────────────────────────────────────

class HeatExchanger:
    """
    Single-stream heater/cooler.

    Two modes
    ---------
    ``mode="duty"``
        Apply Q [W] to the inlet stream; back-calculate T_out and vapor_fraction.
        Positive Q = heat added.

    ``mode="outlet_temp"``
        Fix T_out_C; calculate the required Q.

    The outlet keeps the inlet pressure and composition.
    Phase change is approximated via the linear ideal-enthalpy model (no bubble
    or dew point solver); a proper VLE flash should follow in the flowsheet if
    two-phase conditions arise.

    TODO: wrap with bubble/dew point tracking using Flash for rigorous phase
          change handling.
    """

    def solve(
        self,
        inlets: list[Stream],
        *,
        mode: str = "duty",
        duty_W: float | None = None,
        outlet_temp_C: float | None = None,
        outlet_name: str = "hex_out",
    ) -> tuple[list[Stream], dict[str, Any]]:
        if len(inlets) != 1:
            raise SimulationError(
                f"HeatExchanger expects exactly 1 inlet, got {len(inlets)}"
            )
        feed = inlets[0]

        if mode == "duty":
            if duty_W is None:
                raise SimulationError(
                    "HeatExchanger in 'duty' mode requires duty_W"
                )
            Q = duty_W
            H_in = feed.enthalpy
            H_out = H_in + (Q / feed.flow if feed.flow > 1e-15 else 0.0)
            T_out, vf_out = _enthalpy_to_state(feed.composition, H_out)

        elif mode == "outlet_temp":
            if outlet_temp_C is None:
                raise SimulationError(
                    "HeatExchanger in 'outlet_temp' mode requires outlet_temp_C"
                )
            T_out  = outlet_temp_C
            vf_out = feed.vapor_fraction   # phase assumed unchanged (simplified)
            H_in   = feed.enthalpy
            H_out  = mixture_enthalpy(feed.composition, T_out, vf_out)
            Q      = (H_out - H_in) * feed.flow

        else:
            raise SimulationError(f"HeatExchanger: unknown mode '{mode}'")

        outlet = Stream(
            outlet_name, T_out, feed.pressure, feed.flow,
            dict(feed.composition), vf_out,
        )
        summary: dict[str, Any] = {
            "mode": mode,
            "inlet_temp_C": feed.temperature,
            "outlet_temp_C": T_out,
            "duty_W": Q,
            "duty_kW": Q / 1000.0,
        }
        return [outlet], summary


def _enthalpy_to_state(
    composition: dict[str, float],
    H_target: float,
) -> tuple[float, float]:
    """
    Approximate (T, vapor_fraction) from molar enthalpy using the linear ideal
    model.  Liquid assumed below ΔHvap, vapour above.

    TODO: replace with bubble/dew envelope tracking via Flash solver.
    """
    Cp_liq = mixture_Cp_liquid(composition)
    Cp_ig  = mixture_Cp_ig(composition)
    dHvap  = sum(x * _extra(cid)[2] for cid, x in composition.items())

    # Pure liquid: H = Cp_liq · T → T = H / Cp_liq
    if H_target <= 0.0 or Cp_liq < 1e-10:
        T_liq = H_target / max(Cp_liq, 1e-10)
        return T_liq, 0.0

    # Superheated vapour: H = Cp_ig · T + ΔHvap → T = (H - ΔHvap) / Cp_ig
    if H_target > dHvap:
        T_vap = (H_target - dHvap) / max(Cp_ig, 1e-10)
        return T_vap, 1.0

    # Two-phase region (simplified): T from liquid Cp, partial vaporisation
    T_liq = H_target / Cp_liq
    vf = H_target / dHvap   # rough estimate — proper flash needed here
    return T_liq, min(max(vf, 0.0), 1.0)


# ── PFR ───────────────────────────────────────────────────────────────────────

class PFR:
    """
    Plug flow reactor — conversion-specified model.

    Given a ``stoichiometry`` dict {component_id: coefficient} and a target
    ``conversion`` of the limiting reactant, updates the outlet composition.
    Negative coefficients denote reactants, positive denote products.

    Example — A → 2B:  stoichiometry={"A": -1, "B": 2}

    Energy balance is adiabatic by default (Q = 0).  The outlet temperature is
    calculated from the heat of reaction using the feed Cp (constant, liquid).

    Outlet pressure equals inlet pressure (no pressure drop model).

    TODO: integrate species balance ODEs along reactor length with Arrhenius
          kinetics for non-uniform conversion profiles.
    """

    def solve(
        self,
        inlets: list[Stream],
        *,
        stoichiometry: dict[str, float],
        conversion: float,
        delta_Hrxn_J_mol: float = 0.0,
        outlet_name: str = "pfr_out",
    ) -> tuple[list[Stream], dict[str, Any]]:
        if len(inlets) != 1:
            raise SimulationError(f"PFR expects exactly 1 inlet, got {len(inlets)}")
        if not (0.0 <= conversion <= 1.0):
            raise SimulationError(f"PFR: conversion {conversion} not in [0, 1]")
        if not stoichiometry:
            raise SimulationError("PFR: stoichiometry dict is empty")

        feed = inlets[0]

        reactants = {k: abs(v) for k, v in stoichiometry.items() if v < 0}
        if not reactants:
            raise SimulationError(
                "PFR: no reactant found in stoichiometry "
                "(at least one component needs a negative coefficient)"
            )

        # Identify limiting reactant: lowest availability relative to stoichiometry
        limiting = min(
            reactants,
            key=lambda k: feed.composition.get(k, 0.0) / reactants[k],
        )
        nu_lim = abs(stoichiometry[limiting])

        # Component molar flow rates [mol/s]
        n_in: dict[str, float] = {
            k: v * feed.flow for k, v in feed.composition.items()
        }
        n_lim_consumed = feed.composition.get(limiting, 0.0) * feed.flow * conversion

        # Apply stoichiometric changes
        n_out: dict[str, float] = dict(n_in)
        for comp, nu in stoichiometry.items():
            delta = (nu / nu_lim) * n_lim_consumed
            n_out[comp] = max(n_out.get(comp, 0.0) + delta, 0.0)

        F_out = sum(n_out.values())
        if F_out < 1e-15:
            raise SimulationError(
                "PFR: outlet total flow is zero — check stoichiometry"
            )

        z_out = _normalise({k: v for k, v in n_out.items() if v > 1e-15})

        # Adiabatic temperature rise: Q_rxn = -ΔHrxn · ξ consumed
        heat_released_W = -delta_Hrxn_J_mol * n_lim_consumed
        Cp_feed = mixture_Cp_liquid(feed.composition) * feed.flow   # J/(s·K)
        delta_T = heat_released_W / Cp_feed if Cp_feed > 1e-10 else 0.0
        T_out = feed.temperature + delta_T

        outlet = Stream(
            outlet_name, T_out, feed.pressure, F_out, z_out, feed.vapor_fraction
        )
        summary: dict[str, Any] = {
            "conversion": conversion,
            "limiting_reactant": limiting,
            "moles_consumed_mol_s": n_lim_consumed,
            "heat_released_W": heat_released_W,
            "adiabatic_temp_rise_C": delta_T,
            "outlet_flow_mol_s": F_out,
            "outlet_temperature_C": T_out,
        }
        return [outlet], summary


# ── Flash ─────────────────────────────────────────────────────────────────────

class Flash:
    """
    Isothermal two-phase VLE split using modified Raoult's law with Wilson
    activity coefficients for non-ideal liquid phases.

        K_i = γ_i(x) · VP_i(T) / P

    Wilson γ_i are iterated via successive substitution until K-values
    converge (max relative change < 1e-6, up to 50 outer iterations).
    Component pairs without Wilson parameters default to γ_i = 1 (ideal,
    Raoult's law).

    All components must be present in COMPONENT_LIBRARY (Antoine parameters
    required for vapour pressure).

    Produces two outlet streams: ``liquid`` (index 0) and ``vapor`` (index 1).
    Zero-flow streams are returned for trivial (all-liquid or all-vapour) cases.
    """

    _MAX_SS_ITER = 50
    _SS_TOL      = 1e-6

    def solve(
        self,
        inlets: list[Stream],
        *,
        temperature_C: float | None = None,
        pressure_bar: float | None = None,
        liquid_name: str = "flash_liquid",
        vapor_name: str  = "flash_vapor",
    ) -> tuple[list[Stream], dict[str, Any]]:
        if len(inlets) != 1:
            raise SimulationError(f"Flash expects exactly 1 inlet, got {len(inlets)}")

        feed = inlets[0]
        T = temperature_C if temperature_C is not None else feed.temperature
        P = pressure_bar  if pressure_bar  is not None else feed.pressure

        unknown = [c for c in feed.composition if c not in COMPONENT_LIBRARY]
        if unknown:
            raise SimulationError(
                f"Flash: unknown components {unknown}. "
                f"Available: {list(COMPONENT_LIBRARY)}"
            )

        comps = list(feed.composition)
        z     = np.array([feed.composition[c] for c in comps])
        VP    = np.array([COMPONENT_LIBRARY[c].vapor_pressure(T) for c in comps])

        # ── Successive-substitution outer loop ────────────────────────────────
        # Initialise K from Wilson γ evaluated at feed composition (x = z).
        x      = z.copy()
        gamma  = np.array([wilson_gammas(dict(zip(comps, x.tolist())))[c] for c in comps])
        K      = gamma * VP / P

        psi        = 0.5        # will be overwritten
        converged  = False
        msg        = "Maximum iterations reached — K-values may not be converged"
        ss_iters   = 0

        for ss_iter in range(self._MAX_SS_ITER):
            ss_iters = ss_iter + 1

            # ── trivial checks ────────────────────────────────────────────────
            if np.sum(z * K) <= 1.0:
                liq = Stream(liquid_name, T, P, feed.flow, dict(feed.composition), 0.0)
                vap = Stream(vapor_name,  T, P, 0.0,       dict(feed.composition), 1.0)
                return [liq, vap], {
                    "vapor_fraction": 0.0,
                    "message": "Sub-cooled liquid — below bubble point",
                    "K_values": dict(zip(comps, K.tolist())),
                    "activity_coefficients": dict(zip(comps, gamma.tolist())),
                    "ss_iterations": ss_iters,
                }

            if np.sum(z / K) <= 1.0:
                liq = Stream(liquid_name, T, P, 0.0,       dict(feed.composition), 0.0)
                vap = Stream(vapor_name,  T, P, feed.flow, dict(feed.composition), 1.0)
                return [liq, vap], {
                    "vapor_fraction": 1.0,
                    "message": "Superheated vapour — above dew point",
                    "K_values": dict(zip(comps, K.tolist())),
                    "activity_coefficients": dict(zip(comps, gamma.tolist())),
                    "ss_iterations": ss_iters,
                }

            # ── Rachford-Rice inner solve ─────────────────────────────────────
            psi_lo = max(1.0 / (1.0 - float(K.max())) + 1e-9, 1e-9)
            psi_hi = min(1.0 / (1.0 - float(K.min())) - 1e-9, 1.0 - 1e-9)

            try:
                psi = brentq(
                    _rr_objective, psi_lo, psi_hi, args=(z, K),
                    xtol=1e-12, maxiter=300,
                )
            except ValueError as exc:
                psi       = 0.5
                converged = False
                msg       = f"Rachford-Rice failed: {exc}"
                break

            # ── Update liquid composition and activity coefficients ───────────
            x_new   = z / (1.0 + psi * (K - 1.0));  x_new /= x_new.sum()
            gamma_new = np.array(
                [wilson_gammas(dict(zip(comps, x_new.tolist())))[c] for c in comps]
            )
            K_new = gamma_new * VP / P

            # ── Convergence check ─────────────────────────────────────────────
            rel_change = np.max(np.abs(K_new - K) / np.maximum(K, 1e-10))
            x, gamma, K = x_new, gamma_new, K_new

            if rel_change < self._SS_TOL:
                converged = True
                msg       = f"Converged — two-phase equilibrium ({ss_iters} iterations)"
                break

        # ── Build output streams ──────────────────────────────────────────────
        y = K * x;  y /= y.sum()

        x_dict = dict(zip(comps, x.tolist()))
        y_dict = dict(zip(comps, y.tolist()))

        liq = Stream(liquid_name, T, P, float(feed.flow * (1.0 - psi)), x_dict, 0.0)
        vap = Stream(vapor_name,  T, P, float(feed.flow * psi),          y_dict, 1.0)
        summary: dict[str, Any] = {
            "vapor_fraction": float(psi),
            "converged": converged,
            "message": msg,
            "K_values": dict(zip(comps, K.tolist())),
            "activity_coefficients": dict(zip(comps, gamma.tolist())),
            "ss_iterations": ss_iters,
            "liquid_flow_mol_s": liq.flow,
            "vapor_flow_mol_s": vap.flow,
        }
        return [liq, vap], summary


# ── Pump ──────────────────────────────────────────────────────────────────────

class Pump:
    """
    Liquid pump — raises pressure by ``delta_P_bar`` at a given mechanical
    efficiency.

    Shaft work
    ----------
        W_ideal = F · (MW_mix/ρ_liq) · ΔP          [J/s]
        W_shaft = W_ideal / η

    Temperature rise from fluid friction is neglected (isentropic incompressible
    liquid assumption).  A warning is issued if the inlet vapor_fraction > 0.05.

    TODO: add pump-work heating (temperature rise) via the thermodynamic
          efficiency split (adiabatic vs. isothermal efficiency).
    """

    def solve(
        self,
        inlets: list[Stream],
        *,
        delta_P_bar: float,
        efficiency: float = 0.75,
        outlet_name: str = "pump_out",
    ) -> tuple[list[Stream], dict[str, Any]]:
        if len(inlets) != 1:
            raise SimulationError(f"Pump expects exactly 1 inlet, got {len(inlets)}")
        if not (0.0 < efficiency <= 1.0):
            raise SimulationError(
                f"Pump: efficiency {efficiency} must be in (0, 1]"
            )
        if delta_P_bar < 0:
            raise SimulationError(
                f"Pump: delta_P_bar {delta_P_bar} must be non-negative"
            )

        feed = inlets[0]
        warnings: list[str] = []

        if feed.vapor_fraction > 0.05:
            warnings.append(
                f"Pump inlet vapor_fraction={feed.vapor_fraction:.2f} — "
                "work calculation assumes liquid; result may be inaccurate"
            )

        MW_avg  = mixture_MW(feed.composition)              # g/mol
        rho_liq = mixture_density_liquid(feed.composition)  # kg/m³

        # Specific volume of liquid: v = MW / ρ  [m³/mol]
        v_mol = (MW_avg * 1e-3) / rho_liq   # m³/mol
        W_ideal = feed.flow * v_mol * (delta_P_bar * 1e5)   # W
        W_shaft = W_ideal / efficiency

        outlet = Stream(
            outlet_name,
            feed.temperature,
            feed.pressure + delta_P_bar,
            feed.flow,
            dict(feed.composition),
            0.0,
        )
        summary: dict[str, Any] = {
            "delta_P_bar": delta_P_bar,
            "outlet_pressure_bar": feed.pressure + delta_P_bar,
            "efficiency": efficiency,
            "shaft_work_W": W_shaft,
            "shaft_work_kW": W_shaft / 1000.0,
            "liquid_density_kg_m3": rho_liq,
            "warnings": warnings,
        }
        return [outlet], summary
