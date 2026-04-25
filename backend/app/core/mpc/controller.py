"""
Model Predictive Control — two modes.

  controller_type = "NONLINEAR" (default):
    Arrhenius-based nonlinear ODEs, NMPC, stabilises the open-loop unstable SS.

  controller_type = "LINEAR":
    Jacobian linearisation around the operating point (fixed A, B matrices).
    Deviation space:
      dz/dt = A_c · z + B_c · v     (z = x − x_ss, v = u − u_ss)
    Matches NMPC exactly at the operating point; diverges for large excursions
    because Arrhenius exponential cannot be linearised far from steady state.
"""

import uuid
import numpy as np
from gekko import GEKKO
from app.core.mpc.system_model import CSTRModel


class MPCConfig:
    def __init__(self):
        self.prediction_horizon: int   = 40
        self.control_horizon:    int   = 10
        self.Q: np.ndarray             = np.diag([50.0, 0.2])
        self.R: np.ndarray             = np.diag([0.001, 0.01])
        self.dt: float                 = 1.0
        self.controller_type: str      = "NONLINEAR"   # "LINEAR" | "NONLINEAR"
        self.feedforward_enabled: bool = False

    def update(self, data: dict):
        if "prediction_horizon" in data:
            self.prediction_horizon = int(data["prediction_horizon"])
        if "control_horizon" in data:
            self.control_horizon = int(data["control_horizon"])
        if "Q00" in data:
            self.Q[0, 0] = float(data["Q00"])
        if "Q11" in data:
            self.Q[1, 1] = float(data["Q11"])
        if "R00" in data:
            self.R[0, 0] = float(data["R00"])
        if "R11" in data:
            self.R[1, 1] = float(data["R11"])
        if "dt" in data:
            self.dt = float(data["dt"])
        if "controller_type" in data:
            v = str(data["controller_type"]).upper()
            if v in ("LINEAR", "NONLINEAR"):
                self.controller_type = v
        if "feedforward_enabled" in data:
            self.feedforward_enabled = bool(data["feedforward_enabled"])


class MPCController:
    def __init__(self, model: CSTRModel, config: MPCConfig = None):
        self.model  = model
        self.config = config or MPCConfig()
        self._A_c, self._B_c = model.linearize()

    def set_model(self, new_model: CSTRModel):
        self.model = new_model
        self._A_c, self._B_c = new_model.linearize()

    def compute(
        self,
        x0:           np.ndarray,
        setpoints:    np.ndarray,
        u_prev:       np.ndarray,
        disturbances: np.ndarray = None,
    ) -> tuple[np.ndarray, dict, bool]:
        if disturbances is None:
            disturbances = np.zeros(len(self.model.x_ss))
        d_pred = disturbances if self.config.feedforward_enabled else np.zeros_like(disturbances)
        if self.config.controller_type == "LINEAR":
            return self._compute_linear(x0, setpoints, u_prev, d_pred)
        return self._compute_nonlinear(x0, setpoints, u_prev, d_pred)

    # ── Nonlinear MPC ─────────────────────────────────────────────────────────
    def _compute_nonlinear(self, x0, setpoints, u_prev, disturbances):
        cfg = self.config
        mdl = self.model
        N   = cfg.prediction_horizon
        dt  = cfg.dt

        sid = str(uuid.uuid4()).replace("-", "")[:8]
        m   = GEKKO(remote=False, name=f"nmpc_{sid}")
        m.time = [dt * k for k in range(N + 1)]

        k0_s         = mdl.k0
        EoverR       = mdl.EoverR
        Caf          = mdl.Caf
        Tf           = mdl.Tf
        mdelH_rho_Cp = mdl.mdelH_rho_Cp
        UA_rho_Cp_V  = mdl.UA_rho_Cp_V
        V            = mdl.V
        d0, d1       = float(disturbances[0]), float(disturbances[1])

        CA = m.Var(value=float(x0[0]), lb=float(mdl.x_min[0]), ub=float(mdl.x_max[0]), name="CA")
        T  = m.Var(value=float(x0[1]), lb=float(mdl.x_min[1]), ub=float(mdl.x_max[1]), name="T")

        F  = m.MV(value=float(u_prev[0]), lb=float(mdl.u_min[0]), ub=float(mdl.u_max[0]), name="F")
        Tc = m.MV(value=float(u_prev[1]), lb=float(mdl.u_min[1]), ub=float(mdl.u_max[1]), name="Tc")
        F.STATUS  = 1;  Tc.STATUS  = 1
        F.DCOST   = float(cfg.R[0, 0]);  Tc.DCOST  = float(cfg.R[1, 1])
        F.DMAX    = float(mdl.du_max[0]); Tc.DMAX  = float(mdl.du_max[1])
        F.MV_STEP_HOR  = cfg.control_horizon
        Tc.MV_STEP_HOR = cfg.control_horizon

        cv_CA = m.CV(value=float(x0[0]), name="cv_CA")
        cv_T  = m.CV(value=float(x0[1]), name="cv_T")
        cv_CA.STATUS = 1;  cv_T.STATUS = 1
        cv_CA.SP  = float(setpoints[0]);  cv_T.SP  = float(setpoints[1])
        cv_CA.WSP = float(cfg.Q[0, 0]);   cv_T.WSP = float(cfg.Q[1, 1])
        cv_CA.TAU = 30.0;                 cv_T.TAU = 40.0

        k   = m.Intermediate(k0_s * m.exp(-EoverR / T))
        q_V = m.Intermediate(F / 60.0 / V)
        m.Equation(CA.dt() == q_V*(Caf - CA) - k*CA + d0)
        m.Equation(T.dt()  == q_V*(Tf  - T)  + mdelH_rho_Cp*k*CA - UA_rho_Cp_V*(T - Tc) + d1)
        m.Equation(cv_CA == CA)
        m.Equation(cv_T  == T)

        m.options.IMODE   = 6
        m.options.CV_TYPE = 2
        m.options.NODES   = 2
        m.options.SOLVER  = 3
        m.options.MAX_ITER = 200

        try:
            m.solve(disp=False)
            u_opt = np.array([float(F.NEWVAL), float(Tc.NEWVAL)])
            predicted = {
                "time": list(m.time),
                "CA":   [float(v) for v in CA.value],
                "T":    [float(v) for v in T.value],
                "u1":   [float(v) for v in F.value],
                "u2":   [float(v) for v in Tc.value],
            }
            success = True
        except Exception as exc:
            print(f"[NMPC] GEKKO failed: {exc}")
            u_opt = np.clip(u_prev.copy(), mdl.u_min, mdl.u_max)
            predicted = {}
            success = False
        finally:
            m.cleanup()

        return u_opt, predicted, success

    # ── Linear MPC ────────────────────────────────────────────────────────────
    def _compute_linear(self, x0, setpoints, u_prev, disturbances):
        """
        Linearised MPC — fixed Jacobian at the operating point.
        Deviation space: z = x − x_ss, v = u − u_ss
          dz/dt = A_c · z + B_c · v + d
        """
        cfg  = self.config
        mdl  = self.model
        N    = cfg.prediction_horizon
        dt   = cfg.dt
        A_c  = self._A_c
        B_c  = self._B_c

        x_ss = mdl.x_ss.copy()
        u_ss = mdl.u_ss.copy()

        d0, d1 = float(disturbances[0]), float(disturbances[1])

        sid = str(uuid.uuid4()).replace("-", "")[:8]
        m   = GEKKO(remote=False, name=f"lmpc_{sid}")
        m.time = [dt * k for k in range(N + 1)]

        z0_init = float(x0[0]) - float(x_ss[0])
        z1_init = float(x0[1]) - float(x_ss[1])

        z0_min = float(mdl.x_min[0]) - float(x_ss[0])
        z0_max = float(mdl.x_max[0]) - float(x_ss[0])
        z1_min = float(mdl.x_min[1]) - float(x_ss[1])
        z1_max = float(mdl.x_max[1]) - float(x_ss[1])

        z0 = m.Var(value=z0_init, lb=z0_min, ub=z0_max, name="z_CA")
        z1 = m.Var(value=z1_init, lb=z1_min, ub=z1_max, name="z_T")

        v0_init = float(u_prev[0]) - float(u_ss[0])
        v1_init = float(u_prev[1]) - float(u_ss[1])
        v0_min  = float(mdl.u_min[0]) - float(u_ss[0])
        v0_max  = float(mdl.u_max[0]) - float(u_ss[0])
        v1_min  = float(mdl.u_min[1]) - float(u_ss[1])
        v1_max  = float(mdl.u_max[1]) - float(u_ss[1])

        v0 = m.MV(value=v0_init, lb=v0_min, ub=v0_max, name="v_F")
        v1 = m.MV(value=v1_init, lb=v1_min, ub=v1_max, name="v_Tc")
        v0.STATUS = 1;  v1.STATUS = 1
        v0.DCOST  = float(cfg.R[0, 0]);  v1.DCOST  = float(cfg.R[1, 1])
        v0.DMAX   = float(mdl.du_max[0]); v1.DMAX  = float(mdl.du_max[1])
        v0.MV_STEP_HOR = cfg.control_horizon
        v1.MV_STEP_HOR = cfg.control_horizon

        ca_abs = m.CV(value=float(x0[0]), name="cv_CA")
        t_abs  = m.CV(value=float(x0[1]), name="cv_T")
        ca_abs.STATUS = 1;  t_abs.STATUS = 1
        ca_abs.SP  = float(setpoints[0]);  t_abs.SP  = float(setpoints[1])
        ca_abs.WSP = float(cfg.Q[0, 0]);   t_abs.WSP = float(cfg.Q[1, 1])
        ca_abs.TAU = 30.0;                 t_abs.TAU = 40.0

        m.Equation(z0.dt() == (float(A_c[0,0])*z0 + float(A_c[0,1])*z1
                               + float(B_c[0,0])*v0 + float(B_c[0,1])*v1 + d0))
        m.Equation(z1.dt() == (float(A_c[1,0])*z0 + float(A_c[1,1])*z1
                               + float(B_c[1,0])*v0 + float(B_c[1,1])*v1 + d1))
        m.Equation(ca_abs == z0 + float(x_ss[0]))
        m.Equation(t_abs  == z1 + float(x_ss[1]))

        m.options.IMODE   = 6
        m.options.CV_TYPE = 2
        m.options.NODES   = 2
        m.options.SOLVER  = 3
        m.options.MAX_ITER = 200

        try:
            m.solve(disp=False)
            F_opt  = float(v0.NEWVAL) + float(u_ss[0])
            Tc_opt = float(v1.NEWVAL) + float(u_ss[1])
            u_opt  = np.clip([F_opt, Tc_opt], mdl.u_min, mdl.u_max)
            ca_traj = [float(v) + float(x_ss[0]) for v in z0.value]
            t_traj  = [float(v) + float(x_ss[1]) for v in z1.value]
            f_traj  = [float(v) + float(u_ss[0]) for v in v0.value]
            tc_traj = [float(v) + float(u_ss[1]) for v in v1.value]
            predicted = {
                "time": list(m.time),
                "CA":   ca_traj,
                "T":    t_traj,
                "u1":   f_traj,
                "u2":   tc_traj,
            }
            success = True
        except Exception as exc:
            print(f"[LMPC] GEKKO failed: {exc}")
            u_opt = np.clip(u_prev.copy(), mdl.u_min, mdl.u_max)
            predicted = {}
            success = False
        finally:
            m.cleanup()

        return u_opt, predicted, success
