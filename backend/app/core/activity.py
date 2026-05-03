"""
Wilson activity-coefficient model for non-ideal liquid phases.

Modified Raoult's law:  K_i = γ_i · VP_i(T) / P

Multicomponent Wilson equation
-------------------------------
    D_i   = Σ_j  Λ_ij · x_j          (Λ_ii = 1)
    ln γ_i = 1 − ln(D_i) − Σ_k [ Λ_ki · x_k / D_k ]

Vectorised (numpy):
    D      = Λ @ x
    ln_γ   = 1 − log(D) − Λᵀ @ (x / D)

Parameters
----------
``WILSON_PARAMS`` stores Λ_ij as ``(comp_i, comp_j) → float``.
Only the *off-diagonal* entries are stored; Λ_ii = 1 by convention.
Component pairs not listed default to Λ_ij = 1, which recovers
Raoult's law for that pair (ideal behaviour).

Sources
-------
T-independent Λ values taken from:
  · Gmehling et al., DECHEMA Chemistry Data Series
  · Smith, Van Ness, Abbott, "Introduction to Chemical Engineering
    Thermodynamics", 8th ed., App. B
"""

from __future__ import annotations

import numpy as np

# ── NRTL model ────────────────────────────────────────────────────────────────

# (CAS_i, CAS_j) → (alpha_ij, A_ij [J/mol], A_ji [J/mol])
# Both ordered pairs (i,j) and (j,i) must be listed; A_ij ≠ A_ji in general.
# Sources: Gmehling & Onken, DECHEMA VLE Data Collection; NIST ThermoData Engine
NRTL_PARAMS: dict[tuple[str, str], tuple[float, float, float]] = {
    # Ethanol (64-17-5) – Water (7732-18-5)  (azeotrope ~78.2 °C, 89.4 mol% EtOH)
    ("64-17-5",   "7732-18-5"): (0.2937, 2198.0,  639.7),
    ("7732-18-5", "64-17-5"):   (0.2937,  639.7, 2198.0),
    # Methanol (67-56-1) – Water (7732-18-5)
    ("67-56-1",   "7732-18-5"): (0.2937, 1210.1,  932.2),
    ("7732-18-5", "67-56-1"):   (0.2937,  932.2, 1210.1),
    # Acetone (67-64-1) – Water (7732-18-5)
    ("67-64-1",   "7732-18-5"): (0.5218, 2882.9,  459.0),
    ("7732-18-5", "67-64-1"):   (0.5218,  459.0, 2882.9),
    # IPA (67-63-0) – Water (7732-18-5)
    ("67-63-0",   "7732-18-5"): (0.2734, 3014.8,  472.1),
    ("7732-18-5", "67-63-0"):   (0.2734,  472.1, 3014.8),
    # Ethanol (64-17-5) – Benzene (71-43-2)
    ("64-17-5",   "71-43-2"):   (0.4725, 5188.0, 4659.0),
    ("71-43-2",   "64-17-5"):   (0.4725, 4659.0, 5188.0),
}

_R_NRTL = 8.314  # J/(mol·K)


def nrtl_gammas(x: dict[str, float], T_K: float) -> dict[str, float]:
    """NRTL activity coefficients for a multicomponent liquid mixture.

    τ_ij = A_ij / (R·T),  G_ij = exp(−α_ij · τ_ij)
    Falls back to γ=1 (ideal) for pairs absent from NRTL_PARAMS.
    """
    comps = list(x.keys())
    n = len(comps)

    if n == 0:
        return {}
    if n == 1:
        return {comps[0]: 1.0}

    xv = np.array([max(x[c], 1e-15) for c in comps], dtype=float)
    xv /= xv.sum()

    tau   = np.zeros((n, n))
    alpha = np.zeros((n, n))
    for i, ci in enumerate(comps):
        for j, cj in enumerate(comps):
            if i == j:
                continue
            key = (ci, cj)
            if key in NRTL_PARAMS:
                a_ij, A_ij, _ = NRTL_PARAMS[key]
                alpha[i, j] = a_ij
                tau[i, j]   = A_ij / (_R_NRTL * T_K)
            # else: tau=0, alpha=0 → G=1 → γ→1 (ideal for unlisted pair)

    G = np.exp(-alpha * tau)   # G_ij = exp(-α_ij · τ_ij)

    gammas: dict[str, float] = {}
    for i, ci in enumerate(comps):
        # Term 1: Σ_j x_j τ_ji G_ji / Σ_k x_k G_ki
        denom1 = float(np.dot(xv, G[:, i]))
        term1  = float(np.dot(xv * tau[:, i], G[:, i])) / denom1 if denom1 > 1e-15 else 0.0

        # Term 2: Σ_j [ x_j G_ij / Σ_k x_k G_kj · (τ_ij − Σ_m x_m τ_mj G_mj / Σ_k x_k G_kj) ]
        term2 = 0.0
        for j in range(n):
            denom2 = float(np.dot(xv, G[:, j]))
            if denom2 < 1e-15:
                continue
            sum_num = float(np.dot(xv * tau[:, j], G[:, j]))
            term2 += xv[j] * G[i, j] / denom2 * (tau[i, j] - sum_num / denom2)

        gammas[ci] = float(np.exp(term1 + term2))

    return gammas


# ── Wilson binary parameters  Λ_ij ────────────────────────────────────────────
# Key   : (comp_i, comp_j)  →  Λ_ij  (dimensionless)
# Note  : both ordered pairs must be present; Λ_ij ≠ Λ_ji in general.

WILSON_PARAMS: dict[tuple[str, str], float] = {
    # Ethanol (64-17-5) – Water (7732-18-5)  (azeotrope at ~78.2 °C, 89.4 mol% EtOH)
    ("64-17-5",   "7732-18-5"): 0.7248,
    ("7732-18-5", "64-17-5"):   0.3154,

    # Methanol (67-56-1) – Water (7732-18-5)
    ("67-56-1",   "7732-18-5"): 0.5680,
    ("7732-18-5", "67-56-1"):   0.4778,

    # Acetone (67-64-1) – Water (7732-18-5)
    ("67-64-1",   "7732-18-5"): 0.3575,
    ("7732-18-5", "67-64-1"):   0.2595,
}


def wilson_gammas(x: dict[str, float]) -> dict[str, float]:
    """Return Wilson activity coefficients for a liquid mixture.

    Parameters
    ----------
    x : dict[str, float]
        Mole-fraction dict for each component (should sum to 1).

    Returns
    -------
    dict[str, float]
        γ_i for every component in *x*.
        Components whose pairs are not in ``WILSON_PARAMS`` are treated
        as ideal (Λ_ij = 1 → γ_i = 1 in the limit of no non-ideal pairs).
    """
    comps = list(x.keys())
    n = len(comps)

    if n == 0:
        return {}
    if n == 1:
        return {comps[0]: 1.0}

    xv = np.array([max(x[c], 1e-15) for c in comps], dtype=float)
    xv /= xv.sum()

    # Build Λ matrix  (n × n),  Λ_ij = row i, col j
    Lambda = np.ones((n, n), dtype=float)
    for i, ci in enumerate(comps):
        for j, cj in enumerate(comps):
            if i != j:
                Lambda[i, j] = WILSON_PARAMS.get((ci, cj), 1.0)

    # D_i = Σ_j Λ_ij · x_j
    D = Lambda @ xv
    D = np.maximum(D, 1e-300)

    # ln γ_i = 1 − ln(D_i) − Σ_k [ Λ_ki · x_k / D_k ]
    #        = 1 − ln(D) − Λᵀ @ (x / D)
    ln_gamma = 1.0 - np.log(D) - Lambda.T @ (xv / D)

    return dict(zip(comps, np.exp(ln_gamma).tolist()))
