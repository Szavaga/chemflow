"""
ChemFlow domain exceptions.
"""

from __future__ import annotations


class ThermodynamicRangeError(ValueError):
    """Raised when a property calculation is requested outside its valid range.

    Args:
        property_name: name of the property / method (e.g. "vapor_pressure")
        T:             requested temperature (K)
        T_min:         lower bound of valid range (K)
        T_max:         upper bound of valid range (K)
        component:     component name or identifier (optional)
    """

    def __init__(
        self,
        property_name: str,
        T: float,
        T_min: float,
        T_max: float,
        component: str = "",
    ) -> None:
        comp_str = f" for {component!r}" if component else ""
        super().__init__(
            f"{property_name}{comp_str}: T={T:.2f} K is outside Antoine valid range "
            f"[{T_min:.2f}, {T_max:.2f}] K — extrapolation disabled"
        )
        self.property_name = property_name
        self.T = T
        self.T_min = T_min
        self.T_max = T_max
        self.component = component
