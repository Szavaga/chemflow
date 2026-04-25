"""
Discrete Kalman Filter for 2-dimensional state estimation (deviation coordinates).

Model (deviation space):
  x_dev(k+1) = A_d · x_dev(k) + B_d · u_dev(k) + w(k),  w ~ N(0, Q_proc)
  y_dev(k)   = x_dev(k)                          + v(k),  v ~ N(0, R_meas)

where A_d = I + A_c·dt, B_d = B_c·dt  (first-order Euler discretisation),
H = I (full state observable).

Deviation coordinates: x_dev = x − x_ss,  u_dev = u − u_ss
"""

import numpy as np


class DiscreteKalmanFilter:
    def __init__(
        self,
        A_c: np.ndarray,
        B_c: np.ndarray,
        dt: float,
        x_ss: np.ndarray,
        u_ss: np.ndarray,
        Q_proc: np.ndarray = None,
    ):
        n = A_c.shape[0]
        self.n = n
        # Euler discretisation (deviation space)
        self.A_d = np.eye(n) + A_c * dt
        self.B_d = B_c * dt
        self.x_ss = x_ss.copy()
        self.u_ss = u_ss.copy()
        # Process noise covariance (model uncertainty)
        self.Q = Q_proc if Q_proc is not None else np.eye(n) * 0.1
        # KF state (deviation space)
        self.x_hat_dev = np.zeros(n)
        self.P = np.eye(n) * 10.0
        self.K = np.zeros((n, n))

    def reset(self, x0: np.ndarray):
        """Reset KF from a given absolute initial state."""
        self.x_hat_dev = (x0 - self.x_ss).copy()
        self.P = np.eye(self.n) * 10.0
        self.K = np.zeros((self.n, self.n))

    def step(
        self,
        y_meas:    np.ndarray,
        u_prev:    np.ndarray,
        sigma_vec,
    ) -> np.ndarray:
        """
        Prediction + update in one step.

        Args:
            y_meas:    noisy measurement in absolute coordinates  (n,)
            u_prev:    control input applied at the previous step (absolute)
            sigma_vec: scalar or (n,) array — measurement noise std dev per state

        Returns:
            x̂(k|k): updated state estimate in absolute coordinates
        """
        sv  = np.atleast_1d(sigma_vec)
        if sv.size == 1:
            sv = np.repeat(sv, self.n)
        var = np.maximum(sv ** 2, 1e-6)
        R   = np.diag(var)

        u_dev = u_prev - self.u_ss
        y_dev = y_meas - self.x_ss

        # Prediction
        x_pred = self.A_d @ self.x_hat_dev + self.B_d @ u_dev
        P_pred = self.A_d @ self.P @ self.A_d.T + self.Q

        # Update (H = I)
        S = P_pred + R
        self.K = P_pred @ np.linalg.inv(S)
        innov  = y_dev - x_pred
        self.x_hat_dev = x_pred + self.K @ innov
        self.P = (np.eye(self.n) - self.K) @ P_pred

        return self.x_hat_dev + self.x_ss

    @property
    def gain_diag(self):
        """Diagonal elements of K matrix [K[0,0], K[1,1]], range ≈ [0..1]."""
        return [float(np.clip(self.K[i, i], 0.0, 1.0)) for i in range(self.n)]
