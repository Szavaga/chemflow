"""
Domain-level exceptions for ChemFlow.

These are raised by thermodynamic functions and unit-op solvers.
FastAPI exception handlers translate them into HTTP responses where needed.
"""


class ThermodynamicRangeError(ValueError):
    """Raised when a thermodynamic correlation is evaluated outside its valid range.

    Attributes
    ----------
    prop : str
        Name of the property being evaluated (e.g. "vapor_pressure").
    T : float
        Temperature at which evaluation was attempted (K).
    T_min : float
        Lower bound of the valid temperature range (K).
    T_max : float
        Upper bound of the valid temperature range (K).
    compound : str
        Compound identifier (name or CAS number).
    """

    def __init__(
        self,
        prop: str,
        T: float,
        T_min: float,
        T_max: float,
        compound: str = "",
    ) -> None:
        self.prop = prop
        self.T = T
        self.T_min = T_min
        self.T_max = T_max
        self.compound = compound
        where = f" for {compound!r}" if compound else ""
        super().__init__(
            f"{prop}{where}: T={T:.2f} K is outside valid Antoine range "
            f"[{T_min:.2f}, {T_max:.2f}] K"
        )
