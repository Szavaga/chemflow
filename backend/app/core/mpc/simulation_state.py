"""
Simulation state manager for single-CSTR MPC sessions.

Handles noisy measurements, Kalman Filter / MHE state estimation,
RK4 plant integration, and history buffering.
"""

from collections import deque
from typing import List, Optional
import numpy as np
from app.core.mpc.system_model import CSTRModel, DEFAULT_MODEL
from app.core.mpc.kalman_filter import DiscreteKalmanFilter


MAX_HISTORY = 500

# Measurement noise scaling: sigma=1 → CA: ±0.02 mol/L, T: ±2 K
_NOISE_SCALE = np.array([0.02, 2.0])


class SimulationState:
    def __init__(self, model: CSTRModel = None, dt: float = 1.0):
        self.model = model or DEFAULT_MODEL
        self.dt    = dt
        self._mhe: Optional[object] = None
        self.estimator_type: str = 'KF'
        self._mhe_residuals = np.zeros(2)
        self._mhe_success   = False
        self._init_kf()
        self._reset_state()

    def _init_kf(self):
        A_c, B_c = self.model.linearize()
        Q_proc = np.diag([1e-4, 10.0])
        self._kf = DiscreteKalmanFilter(
            A_c=A_c, B_c=B_c,
            dt=self.dt,
            x_ss=self.model.x_ss,
            u_ss=self.model.u_ss,
            Q_proc=Q_proc,
        )
        self.noise_sigma = 0.0
        self.sensor_bias = np.zeros(2)

    def _reset_state(self):
        self.x      = self.model.x_ss.copy()
        self.u      = self.model.u_ss.copy()
        self.sp     = self.model.x_ss.copy()
        self.time   = 0.0
        self.y_meas = self.x.copy()
        self.x_hat  = self.x.copy()
        self._kf.reset(self.x)
        self._kf.P = np.diag([0.01, 100.0])
        self._history: deque = deque(maxlen=MAX_HISTORY)
        self.iae_ca:   float = 0.0
        self.iae_temp: float = 0.0
        self._append_history(constraint_violations={}, mpc_success=True)

    def observe(self) -> np.ndarray:
        """Apply noise + sensor bias, run state estimator, return x̂."""
        eff   = self.noise_sigma * _NOISE_SCALE
        noise = np.random.randn(2) * eff if self.noise_sigma > 0 else np.zeros(2)
        self.y_meas = np.clip(
            self.x + noise + self.sensor_bias,
            self.model.x_min, self.model.x_max,
        )
        if self.estimator_type == 'MHE' and self._mhe is not None:
            x_hat, ok, res = self._mhe.update(self.y_meas, self.u)
            self.x_hat          = np.clip(x_hat, self.model.x_min, self.model.x_max)
            self._mhe_success   = ok
            self._mhe_residuals = res
            self._kf.x_hat_dev = (self.x_hat - self.model.x_ss).copy()
        else:
            self.x_hat = self._kf.step(self.y_meas, self.u, eff)
            self.x_hat = np.clip(self.x_hat, self.model.x_min, self.model.x_max)
        return self.x_hat.copy()

    def set_estimator(self, estimator_type: str, mhe=None):
        self.estimator_type = estimator_type
        if mhe is not None:
            self._mhe = mhe
        if estimator_type == 'KF':
            self._kf.reset(self.x_hat)

    def step(self, u_opt: np.ndarray, disturbances: np.ndarray, mpc_success: bool = True) -> dict:
        d = np.zeros(2)
        d[:min(len(disturbances), 2)] = disturbances[:min(len(disturbances), 2)]

        u_clipped  = np.clip(u_opt, self.model.u_min, self.model.u_max)
        violations = self.model.check_constraint_violations(self.x, u_clipped, self.u)
        x_next     = self.model.rk4_step(self.x, u_clipped, self.dt, d)

        self.u = u_clipped.copy()
        self.x = x_next.copy()
        self.time += self.dt

        self.iae_ca   += abs(self.x_hat[0] - self.sp[0]) * self.dt
        self.iae_temp += abs(self.x_hat[1] - self.sp[1]) * self.dt

        self._append_history(constraint_violations=violations, mpc_success=mpc_success)
        return self._current_snapshot(violations)

    def _append_history(self, constraint_violations: dict, mpc_success: bool):
        self._history.append({
            "time":    round(self.time, 3),
            "x":       self.x.tolist(),
            "y_meas":  self.y_meas.tolist(),
            "x_hat":   self.x_hat.tolist(),
            "u":       self.u.tolist(),
            "sp":      self.sp.tolist(),
            "violations": list(constraint_violations.keys()),
            "mpc_ok":  mpc_success,
        })

    def _current_snapshot(self, violations: dict) -> dict:
        return {
            "time":                  round(self.time, 3),
            "states":                self.x_hat.tolist(),
            "states_true":           self.x.tolist(),
            "states_raw":            self.y_meas.tolist(),
            "control":               self.u.tolist(),
            "setpoints":             self.sp.tolist(),
            "constraint_violations": violations,
            "kalman_gain":           self._kf.gain_diag,
            "approaching_runaway":   self.model.is_approaching_runaway(self.x),
            "is_runaway":            self.model.is_runaway(self.x),
            "estimator_type":        self.estimator_type,
            "mhe_success":           bool(self._mhe_success),
            "mhe_residuals":         [round(float(r), 5) for r in self._mhe_residuals],
            "iae_ca":                round(self.iae_ca,   4),
            "iae_temp":              round(self.iae_temp, 2),
        }

    def reset(self, x0: List[float] = None, u0: List[float] = None):
        self._reset_state()
        if x0 is not None:
            self.x = np.clip(np.array(x0, dtype=float), self.model.x_min, self.model.x_max)
        if u0 is not None:
            self.u = np.clip(np.array(u0, dtype=float), self.model.u_min, self.model.u_max)
        self._kf.reset(self.x)
        self.y_meas = self.x.copy()
        self.x_hat  = self.x.copy()
        self._mhe_residuals = np.zeros(2)
        self._mhe_success   = False
        if self._mhe is not None:
            self._mhe.reset(self.x)

    def get_history(self) -> List[dict]:
        return list(self._history)

    @property
    def is_at_steady_state(self) -> bool:
        return bool(np.linalg.norm(self.x - self.model.x_ss) < 0.01)
