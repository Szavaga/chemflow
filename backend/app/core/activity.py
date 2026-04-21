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

# ── Wilson binary parameters  Λ_ij ────────────────────────────────────────────
# Key   : (comp_i, comp_j)  →  Λ_ij  (dimensionless)
# Note  : both ordered pairs must be present; Λ_ij ≠ Λ_ji in general.

WILSON_PARAMS: dict[tuple[str, str], float] = {
    # Ethanol – Water  (azeotrope at ~78.2 °C, 89.4 mol% EtOH)
    ("ethanol", "water"):  0.7248,
    ("water",  "ethanol"): 0.3154,

    # Methanol – Water
    ("methanol", "water"):  0.5680,
    ("water",  "methanol"): 0.4778,

    # Acetone – Water
    ("acetone", "water"):  0.3575,
    ("water",  "acetone"): 0.2595,
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
