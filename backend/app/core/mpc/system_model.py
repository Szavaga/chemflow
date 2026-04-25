"""
Nonlinear CSTR (Continuous Stirred Tank Reactor) — physics-based model.

States:
  x[0] = CA  [mol/L]   — reactant concentration
  x[1] = T   [K]       — reactor temperature

Inputs:
  u[0] = F   [L/min]   — feed flow rate
  u[1] = Tc  [K]       — coolant temperature

Physics:
  Arrhenius:     k(T) = k0 · exp(−Ea/R / T)
  Mass balance:  V · dCA/dt = F·(CAf − CA)/60 − V·k(T)·CA
  Energy balance: ρ·Cp·V·dT/dt = ρ·Cp·F·(Tf − T)/60
                               + (−ΔH)·V·k(T)·CA
                               − UA·(T − Tc)

Time in [s], F in [L/min] (hence F/60 in equations).

Note: The default steady state (CA_ss=0.5, T_ss=350 K, Tc_ss=300 K)
is open-loop UNSTABLE — NMPC actively stabilises it.
"""

import numpy as np
from dataclasses import dataclass, field
from typing import List


@dataclass
class CSTRModel:
    # Physical parameters (GEKKO CSTR benchmark)
    V:      float = 100.0        # reactor volume [L]
    Caf:    float = 1.0          # feed concentration [mol/L]
    Tf:     float = 350.0        # feed temperature [K]
    rho:    float = 1000.0       # density [g/L]
    Cp:     float = 0.239        # heat capacity [J/(g·K)]
    mdelH:  float = 5.0e4        # heat of reaction [J/mol] (exothermic → positive)
    k0:     float = 7.2e10/60.0  # Arrhenius pre-exponential [1/s]
    EoverR: float = 8750.0       # Ea/R [K]
    UA:     float = 5.0e4/60.0   # heat transfer coefficient [J/(s·K)]

    # Steady-state operating point
    x_ss:   np.ndarray = field(default_factory=lambda: np.array([0.5,   350.0]))
    u_ss:   np.ndarray = field(default_factory=lambda: np.array([100.0, 300.0]))

    # Physical bounds
    x_min:  np.ndarray = field(default_factory=lambda: np.array([0.02, 300.0]))
    x_max:  np.ndarray = field(default_factory=lambda: np.array([0.98, 430.0]))
    u_min:  np.ndarray = field(default_factory=lambda: np.array([50.0,  250.0]))
    u_max:  np.ndarray = field(default_factory=lambda: np.array([200.0, 350.0]))
    du_max: np.ndarray = field(default_factory=lambda: np.array([20.0,  5.0]))

    state_names: List[str] = field(default_factory=lambda: [
        "Concentration CA [mol/L]", "Temperature T [K]"
    ])
    input_names: List[str] = field(default_factory=lambda: [
        "Feed Flow F [L/min]", "Coolant Temp Tc [K]"
    ])

    T_danger:  float = 400.0   # K — yellow warning
    T_runaway: float = 420.0   # K — red: runaway

    @property
    def mdelH_rho_Cp(self) -> float:
        """(−ΔH) / (ρ·Cp) [K·L/mol]"""
        return self.mdelH / (self.rho * self.Cp)

    @property
    def UA_rho_Cp_V(self) -> float:
        """UA / (ρ·Cp·V) [1/s] — heat removal time constant"""
        return self.UA / (self.rho * self.Cp * self.V)

    def k_arrhenius(self, T: float) -> float:
        """k(T) = k0 · exp(−EoverR / T)  [1/s]"""
        return self.k0 * np.exp(-self.EoverR / max(float(T), 200.0))

    def f(self, x: np.ndarray, u: np.ndarray, d: np.ndarray = None) -> np.ndarray:
        """
        Continuous-time ODE.
          dCA/dt = F/(60·V)·(CAf − CA) − k(T)·CA + d[0]
          dT/dt  = F/(60·V)·(Tf  − T)  + (ΔH/ρCp)·k(T)·CA − (UA/ρCpV)·(T − Tc) + d[1]
        """
        if d is None:
            d = np.zeros(2)
        CA, T_r = float(x[0]), float(x[1])
        F,  Tc  = float(u[0]), float(u[1])
        q_V = F / 60.0 / self.V
        k   = self.k_arrhenius(T_r)
        dCA = q_V * (self.Caf - CA) - k * CA + d[0]
        dT  = q_V * (self.Tf - T_r) + self.mdelH_rho_Cp * k * CA \
              - self.UA_rho_Cp_V * (T_r - Tc) + d[1]
        return np.array([dCA, dT])

    def rk4_step(self, x: np.ndarray, u: np.ndarray, dt: float, d: np.ndarray = None) -> np.ndarray:
        if d is None:
            d = np.zeros(2)
        k1 = self.f(x,               u, d)
        k2 = self.f(x + 0.5*dt*k1,  u, d)
        k3 = self.f(x + 0.5*dt*k2,  u, d)
        k4 = self.f(x + dt*k3,       u, d)
        return np.clip(x + (dt / 6.0) * (k1 + 2*k2 + 2*k3 + k4), self.x_min, self.x_max)

    def check_constraint_violations(self, x: np.ndarray, u: np.ndarray, u_prev: np.ndarray) -> dict:
        violations = {}
        for i, name in enumerate(self.state_names):
            if x[i] < self.x_min[i]:
                violations[f"x{i}_low"]  = {
                    "variable": name, "type": "lower_bound",
                    "value": float(x[i]), "limit": float(self.x_min[i]),
                }
            if x[i] > self.x_max[i]:
                violations[f"x{i}_high"] = {
                    "variable": name, "type": "upper_bound",
                    "value": float(x[i]), "limit": float(self.x_max[i]),
                }
        for i, name in enumerate(self.input_names):
            du = abs(u[i] - u_prev[i])
            if du > self.du_max[i]:
                violations[f"du{i}"] = {
                    "variable": name, "type": "rate_limit",
                    "value": float(du), "limit": float(self.du_max[i]),
                }
        return violations

    def linearize(self, x0: np.ndarray = None, u0: np.ndarray = None, eps: float = 1e-5):
        """Numerical Jacobian ∂f/∂x (A_c) and ∂f/∂u (B_c) at the given operating point."""
        x0 = (x0 if x0 is not None else self.x_ss).copy()
        u0 = (u0 if u0 is not None else self.u_ss).copy()
        d0 = np.zeros(2)
        f0 = self.f(x0, u0, d0)
        n, m = len(x0), len(u0)
        A_c = np.zeros((n, n))
        B_c = np.zeros((n, m))
        for i in range(n):
            xp = x0.copy(); xp[i] += eps
            A_c[:, i] = (self.f(xp, u0, d0) - f0) / eps
        for j in range(m):
            up = u0.copy(); up[j] += eps
            B_c[:, j] = (self.f(x0, up, d0) - f0) / eps
        return A_c, B_c

    def is_approaching_runaway(self, x: np.ndarray) -> bool:
        return float(x[1]) > self.T_danger

    def is_runaway(self, x: np.ndarray) -> bool:
        return float(x[1]) > self.T_runaway


DEFAULT_MODEL = CSTRModel()
