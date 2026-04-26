"""
Steady-state unit operation library for ChemFlow.

Each unit operation:
  - accepts a list of Stream objects plus configuration kwargs
  - returns (outlets: list[Stream], summary: dict)
  - raises SimulationError with a clear message on invalid input

Supported unit operations
--------------------------
  Mixer                — combines N inlets, conserves mass and enthalpy
  Splitter             — splits one inlet into N outlets by specified fractions
  HeatExchanger        — single-stream heater/cooler (duty or target-T mode)
  PFR                  — conversion-specified plug flow reactor (stoichiometry dict)
  Flash                — isothermal two-phase VLE via Rachford-Rice + Raoult's law
  Pump                 — raises liquid pressure; calculates shaft work
  DistillationShortcut — FUG shortcut method (Fenske-Underwood-Gilliland)
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np
from scipy.optimize import brentq

from app.core.activity import wilson_gammas
from app.core.simulation import COMPONENT_LIBRARY
from app.core.thermo import (
    MissingPropertyError,
    PengRobinson,
    ThermodynamicError,
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


class ConvergenceError(SimulationError):
    """Raised when a recycle loop fails to converge within the iteration limit."""

    def __init__(self, message: str, *, iterations: int, residuals: list[float]) -> None:
        super().__init__(message)
        self.iterations = iterations
        self.residuals = residuals


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
    Isothermal two-phase VLE split.

    property_package="ideal" (default)
        K_i = γ_i(x) · VP_i(T) / P  using Wilson activity coefficients.
        Converges when max relative K-change < 1e-6.

    property_package="peng_robinson"
        K-values initialised from the Wilson correlation
            K_i = (Pc_i/P) · exp(5.373·(1+ω_i)·(1−Tc_i/T))
        then iterated via PR fugacity coefficients:
            K_i = exp(ln φ_i^L − ln φ_i^V)
        Converges when max absolute K-change < 1e-8.

    All components must be present in COMPONENT_LIBRARY.
    Produces two outlet streams: ``liquid`` (index 0) and ``vapor`` (index 1).
    Zero-flow streams are returned for trivial (all-liquid or all-vapour) cases.
    """

    _MAX_SS_ITER = 50
    _SS_TOL      = 1e-6
    _PR_TOL      = 1e-8

    def solve(
        self,
        inlets: list[Stream],
        *,
        temperature_C: float | None = None,
        pressure_bar: float | None = None,
        property_package: str = "ideal",
        liquid_name: str = "flash_liquid",
        vapor_name: str  = "flash_vapor",
    ) -> tuple[list[Stream], dict[str, Any]]:
        if len(inlets) != 1:
            raise SimulationError(f"Flash expects exactly 1 inlet, got {len(inlets)}")

        feed = inlets[0]
        T_C = temperature_C if temperature_C is not None else feed.temperature
        P   = pressure_bar  if pressure_bar  is not None else feed.pressure

        unknown = [c for c in feed.composition if c not in COMPONENT_LIBRARY]
        if unknown:
            raise SimulationError(
                f"Flash: unknown components {unknown}. "
                f"Available: {list(COMPONENT_LIBRARY)}"
            )

        comps = list(feed.composition)
        z     = np.array([feed.composition[c] for c in comps])

        # ── Initialise K-values ───────────────────────────────────────────────
        gamma = np.ones(len(comps))   # activity coefficients (ideal default)

        if property_package == "peng_robinson":
            try:
                pr = PengRobinson(comps)
            except MissingPropertyError as exc:
                raise SimulationError(str(exc)) from exc
            T_K  = T_C + 273.15
            P_Pa = P * 1e5
            # Wilson correlation for initial K estimate
            K = (pr.Pc / 1e5) / P * np.exp(
                5.373 * (1.0 + pr.omega) * (1.0 - pr.Tc / T_K)
            )
        else:
            VP    = np.array([COMPONENT_LIBRARY[c].vapor_pressure(T_C) for c in comps])
            x     = z.copy()
            gamma = np.array(
                [wilson_gammas(dict(zip(comps, x.tolist())))[c] for c in comps]
            )
            K = gamma * VP / P

        x         = z.copy()
        psi       = 0.5
        converged = False
        msg       = "Maximum iterations reached — K-values may not be converged"
        ss_iters  = 0

        for ss_iter in range(self._MAX_SS_ITER):
            ss_iters = ss_iter + 1

            # ── trivial checks ────────────────────────────────────────────────
            if np.sum(z * K) <= 1.0:
                liq = Stream(liquid_name, T_C, P, feed.flow, dict(feed.composition), 0.0)
                vap = Stream(vapor_name,  T_C, P, 0.0,       dict(feed.composition), 1.0)
                return [liq, vap], {
                    "vapor_fraction": 0.0,
                    "property_package": property_package,
                    "message": "Sub-cooled liquid — below bubble point",
                    "K_values": dict(zip(comps, K.tolist())),
                    "activity_coefficients": dict(zip(comps, gamma.tolist())),
                    "ss_iterations": ss_iters,
                }

            if np.sum(z / K) <= 1.0:
                liq = Stream(liquid_name, T_C, P, 0.0,       dict(feed.composition), 0.0)
                vap = Stream(vapor_name,  T_C, P, feed.flow, dict(feed.composition), 1.0)
                return [liq, vap], {
                    "vapor_fraction": 1.0,
                    "property_package": property_package,
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

            # ── Update liquid composition and K-values ────────────────────────
            x_new = z / (1.0 + psi * (K - 1.0))
            x_new /= x_new.sum()

            if property_package == "peng_robinson":
                y_cur = K * x_new
                y_cur_sum = y_cur.sum()
                if y_cur_sum > 1e-15:
                    y_cur /= y_cur_sum
                try:
                    ln_phi_L = pr.fugacity_coefficients(T_K, P_Pa, x_new, "liquid")
                    ln_phi_V = pr.fugacity_coefficients(T_K, P_Pa, y_cur, "vapor")
                except (ThermodynamicError, ValueError) as exc:
                    raise SimulationError(f"PR EoS failed: {exc}") from exc
                K_new      = np.exp(ln_phi_L - ln_phi_V)
                rel_change = float(np.max(np.abs(K_new - K)))
                tol        = self._PR_TOL
            else:
                gamma_new = np.array(
                    [wilson_gammas(dict(zip(comps, x_new.tolist())))[c] for c in comps]
                )
                K_new      = gamma_new * VP / P
                rel_change = float(np.max(np.abs(K_new - K) / np.maximum(K, 1e-10)))
                tol        = self._SS_TOL
                gamma      = gamma_new

            x, K = x_new, K_new

            if rel_change < tol:
                converged = True
                msg       = f"Converged — two-phase equilibrium ({ss_iters} iterations)"
                break

        # ── Build output streams ──────────────────────────────────────────────
        y = K * x
        y /= y.sum()

        x_dict = dict(zip(comps, x.tolist()))
        y_dict = dict(zip(comps, y.tolist()))

        liq = Stream(liquid_name, T_C, P, float(feed.flow * (1.0 - psi)), x_dict, 0.0)
        vap = Stream(vapor_name,  T_C, P, float(feed.flow * psi),          y_dict, 1.0)
        summary: dict[str, Any] = {
            "vapor_fraction": float(psi),
            "converged": converged,
            "property_package": property_package,
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


# ── CSTR ──────────────────────────────────────────────────────────────────────

class CSTR:
    """
    Continuous Stirred Tank Reactor — steady-state solve via Arrhenius kinetics.

    Steady-state material and energy balances:
        0 = F/V * (CAf - CA) - k(T) * CA
        0 = F/V * (Tf - T)  + (-dH)/(rho*Cp) * k(T) * CA - UA/(rho*Cp*V) * (T - Tc)

    where k(T) = k0 * exp(-Ea/R / T).  Solved with SciPy fsolve.

    The summary includes CA_ss, T_ss_K, F_ss_L_min and Tc_ss_K so that the
    MPC Control Studio can seed its dynamic simulation from the steady-state
    operating point.

    Unit conversion
    ---------------
    ChemFlow streams carry flow in mol/s.  The CSTR model (from the MPC
    sandbox) uses volumetric flow F in L/min.  We derive F_L_min from the
    inlet stream as:
        F_L_min = feed.flow [mol/s] * MW_mix [g/mol] / rho_liq [kg/m³]
                  * 1e-3 [kg/g] * 1e3 [L/m³] * 60 [s/min]
    """

    # Physical defaults (GEKKO CSTR benchmark, matches MPC sandbox)
    _Caf:   float = 1.0          # feed concentration, mol/L
    _rho:   float = 1000.0       # density, g/L
    _Cp:    float = 0.239        # heat capacity, J/(g·K)
    _mdelH: float = 5.0e4        # heat of reaction, J/mol (exothermic → positive)
    _UA:    float = 5.0e4 / 60.0 # heat transfer coefficient, J/(s·K)

    def solve(
        self,
        inlets: list[Stream],
        *,
        volume_L: float = 100.0,
        temperature_C: float = 76.85,        # 350 K initial guess
        coolant_temp_K: float = 300.0,
        pre_exponential: float = 7.2e10 / 60.0,  # k0, 1/s
        activation_energy_J_mol: float = 72681.0,  # Ea = EoverR * R_GAS
        outlet_name: str = "cstr_out",
    ) -> tuple[list[Stream], dict[str, Any]]:
        from scipy.optimize import fsolve

        if len(inlets) != 1:
            raise SimulationError(f"CSTR expects exactly 1 inlet, got {len(inlets)}")
        if volume_L <= 0:
            raise SimulationError(f"CSTR: volume_L must be positive, got {volume_L}")

        feed = inlets[0]
        T_K_init = temperature_C + 273.15
        Tc_K     = coolant_temp_K
        Tf_K     = feed.temperature + 273.15
        k0       = pre_exponential
        EoverR   = activation_energy_J_mol / R_GAS

        # Derive volumetric flow in L/min from molar feed flow
        MW_mix  = mixture_MW(feed.composition)            # g/mol
        rho_liq = mixture_density_liquid(feed.composition)  # kg/m³
        # mol/s → L/min: (mol/s * g/mol) / (kg/m³ * 1e-3 kg/g * 1e-3 m³/L) / 60 s/min
        F_L_min = feed.flow * MW_mix / (rho_liq * 1e-3 * 1e-3) / 60.0

        # Derived constants
        mH_rho_Cp  = self._mdelH / (self._rho * self._Cp)   # K·L/mol
        UA_rho_Cp_V = self._UA / (self._rho * self._Cp * volume_L)  # 1/s
        q_over_V = F_L_min / 60.0 / volume_L               # volumetric throughput, 1/s

        def _cstr_ss(z: list[float]) -> list[float]:
            CA, T_r = z[0], z[1]
            T_r = max(T_r, 200.0)
            k   = k0 * np.exp(-EoverR / T_r)
            r0  = q_over_V * (self._Caf - CA) - k * CA
            r1  = q_over_V * (Tf_K - T_r) + mH_rho_Cp * k * CA \
                  - UA_rho_Cp_V * (T_r - Tc_K)
            return [r0, r1]

        x0 = [self._Caf * 0.5, T_K_init]
        try:
            sol, info, ier, msg = fsolve(_cstr_ss, x0, full_output=True)
        except Exception as exc:
            raise SimulationError(f"CSTR fsolve failed: {exc}") from exc

        CA_ss = float(np.clip(sol[0], 1e-6, self._Caf))
        T_ss  = float(np.clip(sol[1], 200.0, 600.0))

        # Check residual — if fsolve didn't converge, warn but don't abort
        residual_norm = float(np.linalg.norm(_cstr_ss([CA_ss, T_ss])))
        converged = residual_norm < 1e-6

        k_ss = k0 * np.exp(-EoverR / max(T_ss, 200.0))
        conversion = 1.0 - CA_ss / self._Caf if self._Caf > 0 else 0.0
        tau_s = volume_L / max(F_L_min / 60.0, 1e-12)  # residence time, s

        outlet = Stream(
            outlet_name,
            T_ss - 273.15,         # back to °C
            feed.pressure,
            feed.flow,
            dict(feed.composition),
            0.0,
        )
        summary: dict[str, Any] = {
            # MPC seed values
            "CA_ss":       CA_ss,
            "T_ss_K":      T_ss,
            "F_ss_L_min":  F_L_min,
            "Tc_ss_K":     Tc_K,
            # Diagnostics
            "conversion":        conversion,
            "residence_time_s":  tau_s,
            "k_rxn":             k_ss,
            "converged":         converged,
            "residual_norm":     residual_norm,
            "volume_L":          volume_L,
        }
        return [outlet], summary


# ── DistillationShortcut ───────────────────────────────────────────────────────

class DistillationShortcut:
    """
    Shortcut distillation column design — Fenske-Underwood-Gilliland (FUG) method.

    Fenske   — minimum theoretical stages at total reflux (N_min)
    Underwood — minimum reflux ratio (R_min) via Molokanov (1972) analytical fit
    Gilliland — actual theoretical stages (N_actual) at operating reflux R > R_min

    Component distribution assumption:
      LK  → lk_recovery fraction to distillate
      HK  → hk_recovery fraction to bottoms
      Lighter than LK (α > α_LK) → 99.9 % to distillate
      Heavier than HK (α < 1)    → 99.9 % to bottoms

    ``light_key`` and ``heavy_key`` accept either a component-library ID
    (e.g. "toluene") or a CAS number (e.g. "108-88-3").
    """

    def solve(
        self,
        inlets: list[Stream],
        *,
        light_key: str,
        heavy_key: str,
        lk_recovery: float = 0.99,
        hk_recovery: float = 0.99,
        reflux_ratio: float,
        condenser_type: str = "total",
        property_package: str = "ideal",
        q: float = 1.0,
        distillate_name: str = "distillate",
        bottoms_name: str = "bottoms",
    ) -> tuple[list[Stream], dict[str, Any]]:
        if len(inlets) != 1:
            raise SimulationError(
                f"DistillationShortcut expects exactly 1 inlet, got {len(inlets)}"
            )
        feed = inlets[0]

        from app.core.simulation import CAS_LOOKUP
        lk_id = CAS_LOOKUP.get(light_key, light_key)
        hk_id = CAS_LOOKUP.get(heavy_key, heavy_key)

        comps = list(feed.composition)
        z = dict(feed.composition)
        F = feed.flow
        P = feed.pressure

        if lk_id not in comps:
            raise SimulationError(
                f"Light key '{lk_id}' not found in feed composition {comps}"
            )
        if hk_id not in comps:
            raise SimulationError(
                f"Heavy key '{hk_id}' not found in feed composition {comps}"
            )
        if not (0.0 < lk_recovery < 1.0):
            raise SimulationError(
                f"lk_recovery must be in (0, 1), got {lk_recovery}"
            )
        if not (0.0 < hk_recovery < 1.0):
            raise SimulationError(
                f"hk_recovery must be in (0, 1), got {hk_recovery}"
            )
        if reflux_ratio <= 0:
            raise SimulationError(
                f"reflux_ratio must be positive, got {reflux_ratio}"
            )

        unknown = [c for c in comps if c not in COMPONENT_LIBRARY]
        if unknown:
            raise SimulationError(
                f"DistillationShortcut: components not in library: {unknown}"
            )

        # ── Average column temperature ────────────────────────────────────────────
        T_bubble_feed = self._bubble_T_ideal(z, P)
        T_avg = 0.5 * (feed.temperature + T_bubble_feed)

        # ── K-values and relative volatilities at T_avg ───────────────────────────
        K = self._K_values(T_avg, P, comps, z, property_package)
        K_HK = K[hk_id]
        if K_HK < 1e-15:
            raise SimulationError(
                f"K-value for heavy key '{hk_id}' ≈ 0 at T={T_avg:.1f} °C — "
                "verify component properties and feed conditions"
            )
        alpha = {c: K[c] / K_HK for c in comps}
        alpha_LK = alpha[lk_id]

        if alpha_LK <= 1.0:
            raise SimulationError(
                f"Light key '{lk_id}' has α={alpha_LK:.4f} ≤ 1 at T={T_avg:.1f} °C — "
                "LK must be more volatile than HK"
            )

        # ── Component distribution ────────────────────────────────────────────────
        n_D: dict[str, float] = {}
        n_B: dict[str, float] = {}
        for c in comps:
            n_feed = z[c] * F
            if c == lk_id:
                n_D[c] = lk_recovery * n_feed
                n_B[c] = (1.0 - lk_recovery) * n_feed
            elif c == hk_id:
                n_D[c] = (1.0 - hk_recovery) * n_feed
                n_B[c] = hk_recovery * n_feed
            elif alpha[c] > alpha_LK:
                n_D[c] = 0.999 * n_feed
                n_B[c] = 0.001 * n_feed
            else:
                n_D[c] = 0.001 * n_feed
                n_B[c] = 0.999 * n_feed

        D = sum(n_D.values())
        B = sum(n_B.values())
        if D < 1e-15 or B < 1e-15:
            raise SimulationError(
                "Component distribution produced zero distillate or bottoms flow"
            )
        x_D = _normalise(n_D)
        x_B = _normalise(n_B)

        # ── Fenske: N_min ─────────────────────────────────────────────────────────
        ratio_D = x_D[lk_id] / max(x_D[hk_id], 1e-15)
        ratio_B = x_B[lk_id] / max(x_B[hk_id], 1e-15)
        if ratio_D <= 0 or ratio_B <= 0:
            raise SimulationError("Cannot compute Fenske: zero key-component mole fraction")
        N_min = float(np.log(ratio_D / ratio_B) / np.log(alpha_LK))

        # ── Underwood: theta and R_min ────────────────────────────────────────────
        alpha_arr = np.array([alpha[c] for c in comps])
        z_arr     = np.array([z[c]     for c in comps])
        rhs       = 1.0 - q

        def _underwood_feed(theta: float) -> float:
            return float(np.sum(alpha_arr * z_arr / (alpha_arr - theta))) - rhs

        lo, hi = 1.0 + 1e-8, alpha_LK - 1e-8
        f_lo, f_hi = _underwood_feed(lo), _underwood_feed(hi)
        if f_lo * f_hi >= 0:
            raise SimulationError(
                f"Underwood bracket has no sign change: f({lo:.2e})={f_lo:.3f}, "
                f"f({hi:.4f})={f_hi:.3f}. α_LK={alpha_LK:.4f}. "
                "Verify that the light key is more volatile than the heavy key."
            )
        theta = float(brentq(_underwood_feed, lo, hi, xtol=1e-10))

        x_D_arr = np.array([x_D[c] for c in comps])
        R_min   = float(np.sum(alpha_arr * x_D_arr / (alpha_arr - theta))) - 1.0
        R_min   = max(R_min, 0.0)   # guard against tiny negative from numerics

        # ── Gilliland (Molokanov 1972): N_actual ──────────────────────────────────
        N_actual, N_feed = self._gilliland(N_min, R_min, reflux_ratio)

        # ── Product stream temperatures (bubble points) ───────────────────────────
        T_D = self._bubble_T_ideal(x_D, P)
        T_B = self._bubble_T_ideal(x_B, P)

        # ── Energy balance ────────────────────────────────────────────────────────
        # Overhead vapour flow V = (R + 1) · D
        V = (reflux_ratio + 1.0) * D
        dHvap_D = sum(x_D[c] * _extra(c)[2] for c in comps)
        Q_condenser = V * dHvap_D   # W  (latent heat to condense overhead)

        # Overall energy balance: Q_R = Q_C + D·H_D + B·H_B - F·H_F
        vf_D_stream = 0.0 if condenser_type == "total" else 1.0
        H_D = mixture_enthalpy(x_D, T_D, vf_D_stream)
        H_B = mixture_enthalpy(x_B, T_B, 0.0)
        H_F = feed.enthalpy
        Q_reboiler = Q_condenser + D * H_D + B * H_B - F * H_F   # W

        # ── Build output streams ──────────────────────────────────────────────────
        distillate = Stream(distillate_name, T_D, P, D, x_D, vf_D_stream)
        bottoms    = Stream(bottoms_name,    T_B, P, B, x_B, 0.0)

        summary: dict[str, Any] = {
            "N_min":              float(N_min),
            "R_min":              float(R_min),
            "N_actual":           int(N_actual),
            "N_feed_tray":        int(N_feed),
            "alpha_lk_hk":        float(alpha_LK),
            "theta_underwood":    float(theta),
            "T_avg_C":            float(T_avg),
            "T_bubble_feed_C":    float(T_bubble_feed),
            "distillate_flow_mol_s": float(D),
            "bottoms_flow_mol_s":    float(B),
            "condenser_duty_kW":  float(Q_condenser / 1_000.0),
            "reboiler_duty_kW":   float(Q_reboiler  / 1_000.0),
            "reflux_ratio":       float(reflux_ratio),
            "condenser_type":     condenser_type,
            "property_package":   property_package,
            "distillate_stream":  distillate.to_dict(),
            "bottoms_stream":     bottoms.to_dict(),
        }
        return [distillate, bottoms], summary

    # ── helpers ───────────────────────────────────────────────────────────────────

    @staticmethod
    def _bubble_T_ideal(composition: dict[str, float], P_bar: float) -> float:
        """Bubble-point temperature (°C) for an ideal liquid mixture at P_bar."""
        comp_ids = list(composition)
        z = np.array([composition[c] for c in comp_ids])

        def f(T_C: float) -> float:
            VP = np.array([COMPONENT_LIBRARY[c].vapor_pressure(T_C) for c in comp_ids])
            return float(np.dot(z, VP)) / P_bar - 1.0

        try:
            return float(brentq(f, -100.0, 500.0, xtol=1e-4))
        except ValueError as exc:
            raise SimulationError(
                "Cannot find bubble-point temperature in −100 … 500 °C. "
                "Check component properties and operating pressure."
            ) from exc

    @staticmethod
    def _K_values(
        T_C: float,
        P_bar: float,
        comps: list[str],
        composition: dict[str, float],
        property_package: str,
    ) -> dict[str, float]:
        """K-values K_i = y_i / x_i at (T_C, P_bar)."""
        if property_package == "peng_robinson":
            try:
                pr = PengRobinson(comps)
                T_K  = T_C + 273.15
                P_Pa = P_bar * 1e5
                z    = np.array([composition[c] for c in comps])
                ln_phi_L = pr.fugacity_coefficients(T_K, P_Pa, z, "liquid")
                ln_phi_V = pr.fugacity_coefficients(T_K, P_Pa, z, "vapor")
                K_arr = np.exp(ln_phi_L - ln_phi_V)
                return {comps[i]: float(K_arr[i]) for i in range(len(comps))}
            except (MissingPropertyError, ThermodynamicError) as exc:
                raise SimulationError(
                    f"PR K-value calculation failed: {exc}"
                ) from exc
        # Ideal (Raoult's law): K_i = VP_i(T) / P
        return {c: COMPONENT_LIBRARY[c].vapor_pressure(T_C) / P_bar for c in comps}

    @staticmethod
    def _gilliland(N_min: float, R_min: float, R: float) -> tuple[int, int]:
        """Molokanov (1972) analytical fit to the Gilliland correlation."""
        if R <= R_min:
            raise SimulationError(
                f"Actual reflux ratio R={R:.4f} must be greater than "
                f"R_min={R_min:.4f}. Typically R = 1.2 … 1.5 × R_min."
            )
        X = (R - R_min) / (R + 1.0)
        Y = 1.0 - np.exp(
            ((1.0 + 54.4 * X) / (11.0 + 117.2 * X)) * ((X - 1.0) / X ** 0.5)
        )
        N_actual = (Y + N_min) / (1.0 - Y)
        N_feed   = int(np.ceil(N_actual * 0.5))
        return int(np.ceil(N_actual)), N_feed
