"""
Moving Horizon Estimator (MHE) for single CSTR using Gekko IMODE=5 + IPOPT.

Dual of NMPC:
  NMPC predicts forward  over a prediction horizon  (IMODE=6)
  MHE  estimates backward over an estimation horizon (IMODE=5)

Advantages over Kalman Filter:
  - Nonlinear model constraints (exact Arrhenius dynamics, not linearised)
  - Physical state bounds enforced hard  (CA ≥ 0, T ∈ [300, 500])
  - EV_TYPE=1 (L1 norm) → outlier/fault-robust estimation

MHE objective over N-step window  [k-N, …, k]:
  min Σ WMEAS · |y_i − ŷ_i|^ev_type   [measurement fit]
  s.t.  ẋ = f(x, u)                     [ODE — same Arrhenius model as NMPC]
        x_min ≤ x ≤ x_max              [hard physical bounds]
"""

from dataclasses import dataclass
from collections import deque
from gekko import GEKKO
import numpy as np


@dataclass
class MHEConfig:
    horizon:  int   = 10      # estimation window [steps]
    R_ca:     float = 0.001   # CA measurement noise variance  [mol²/L²]
    R_t:      float = 0.04    # T  measurement noise variance  [K²]
    wmodel:   float = 0.1     # process model weight (higher → smoother, model-following)
    ev_type:  int   = 2       # 1 = L1 robust  |  2 = L2 Gaussian-optimal

    def update(self, **kw):
        for k, v in kw.items():
            if hasattr(self, k):
                setattr(self, k, type(getattr(self, k))(v))


class MHEEstimator:
    """
    Nonlinear Moving Horizon Estimator for the single-CSTR model.

    Usage
    -----
    est = MHEEstimator(model=DEFAULT_MODEL, config=MHEConfig(), dt=1.0)
    est.warmup()                         # pre-compile IPOPT (once)

    each step:
        x_hat, ok, residuals = est.update(y_meas, u_applied)
    """

    def __init__(self, model, config: MHEConfig, dt: float):
        self.model   = model
        self.config  = config
        self.dt      = dt

        N = config.horizon
        self._buf_u = deque([model.u_ss.copy()] * (N + 1), maxlen=N + 1)

        self._x_hat     = model.x_ss.copy()
        self._residuals = np.zeros(2)
        self._success   = False

        self._build_gekko()

    def _build_gekko(self):
        N  = self.config.horizon
        dt = self.dt
        md = self.model

        m = GEKKO(remote=False)
        m.time = np.linspace(0, N * dt, N + 1)

        F_init  = float(md.u_ss[0]) * np.ones(N + 1)
        Tc_init = float(md.u_ss[1]) * np.ones(N + 1)
        F_p  = m.MV(value=F_init,  lb=float(md.u_min[0]), ub=float(md.u_max[0]))
        Tc_p = m.MV(value=Tc_init, lb=float(md.u_min[1]), ub=float(md.u_max[1]))
        F_p.STATUS  = 0;  F_p.FSTATUS  = 0
        Tc_p.STATUS = 0;  Tc_p.FSTATUS = 0

        CA = m.Var(value=float(md.x_ss[0]), lb=0.001, ub=1.0)
        T  = m.Var(value=float(md.x_ss[1]), lb=300.0, ub=500.0)

        k   = m.Intermediate(md.k0 * m.exp(-md.EoverR / T))
        q_V = m.Intermediate(F_p / (60.0 * md.V))

        m.Equation(CA.dt() == q_V * (md.Caf - CA) - k * CA)
        m.Equation(T.dt()  == (q_V * (md.Tf - T)
                               + md.mdelH_rho_Cp * k * CA
                               - md.UA_rho_Cp_V  * (T - Tc_p)))

        y_CA = m.CV(value=float(md.x_ss[0]), lb=0.001, ub=1.0)
        y_T  = m.CV(value=float(md.x_ss[1]), lb=300.0, ub=500.0)

        y_CA.STATUS  = 1;  y_CA.FSTATUS = 1
        y_T.STATUS   = 1;  y_T.FSTATUS  = 1
        y_CA.WMEAS   = 1.0 / max(self.config.R_ca, 1e-9)
        y_T.WMEAS    = 1.0 / max(self.config.R_t,  1e-9)
        y_CA.WMODEL  = self.config.wmodel
        y_T.WMODEL   = self.config.wmodel
        y_CA.MEAS_GAP = 0.0
        y_T.MEAS_GAP  = 0.0

        m.Equation(y_CA == CA)
        m.Equation(y_T  == T)

        m.options.IMODE    = 5
        m.options.EV_TYPE  = self.config.ev_type
        m.options.NODES    = 2
        m.options.SOLVER   = 3
        m.options.MAX_ITER = 50

        self._m    = m
        self._CA   = CA;   self._T    = T
        self._F_p  = F_p;  self._Tc_p = Tc_p
        self._y_CA = y_CA; self._y_T  = y_T

    def warmup(self):
        """Trigger first IPOPT solve to compile model and allocate memory."""
        try:
            self._y_CA.MEAS = float(self.model.x_ss[0])
            self._y_T.MEAS  = float(self.model.x_ss[1])
            self._m.solve(disp=False)
        except Exception:
            pass

    def update(self, y_meas: np.ndarray, u_input: np.ndarray) -> tuple[np.ndarray, bool, np.ndarray]:
        """
        Advance MHE window by one step and solve.

        Returns
        -------
        x_hat      : (2,)  estimated state  [CA_hat, T_hat]
        success    : bool
        residuals  : (2,)  |y_meas − x_hat|
        """
        self._buf_u.append(u_input.copy())
        buf_u = list(self._buf_u)

        self._F_p.value  = [float(u[0]) for u in buf_u]
        self._Tc_p.value = [float(u[1]) for u in buf_u]

        self._y_CA.MEAS = float(np.clip(y_meas[0], 0.001, 1.0))
        self._y_T.MEAS  = float(np.clip(y_meas[1], 300.0, 500.0))

        try:
            self._m.solve(disp=False)
            CA_hat = float(self._CA.value[-1])
            T_hat  = float(self._T.value[-1])
            x_hat  = np.clip(
                np.array([CA_hat, T_hat]),
                self.model.x_min, self.model.x_max,
            )
            residuals       = np.abs(y_meas - x_hat)
            self._x_hat     = x_hat
            self._residuals = residuals
            self._success   = True
            return x_hat, True, residuals
        except Exception:
            self._success = False
            return self._x_hat, False, self._residuals

    def reset(self, x0: np.ndarray | None = None):
        x0 = x0 if x0 is not None else self.model.x_ss.copy()
        self._x_hat     = np.array(x0[:2]).copy()
        self._residuals = np.zeros(2)
        self._success   = False
        N = self.config.horizon
        self._buf_u = deque([self.model.u_ss.copy()] * (N + 1), maxlen=N + 1)
        try:
            self._m.cleanup()
        except Exception:
            pass
        self._build_gekko()

    def reconfigure(self, config: MHEConfig):
        """Apply new config. Rebuilds if horizon changed; otherwise hot-swaps weights."""
        if config.horizon != self.config.horizon:
            self.config = config
            try:
                self._m.cleanup()
            except Exception:
                pass
            N = config.horizon
            self._buf_u = deque([self.model.u_ss.copy()] * (N + 1), maxlen=N + 1)
            self._build_gekko()
        else:
            self.config = config
            self._m.options.EV_TYPE = config.ev_type
            self._y_CA.WMEAS  = 1.0 / max(config.R_ca, 1e-9)
            self._y_T.WMEAS   = 1.0 / max(config.R_t,  1e-9)
            self._y_CA.WMODEL = config.wmodel
            self._y_T.WMODEL  = config.wmodel

    @property
    def last_success(self) -> bool:
        return self._success

    @property
    def last_residuals(self) -> np.ndarray:
        return self._residuals.copy()
