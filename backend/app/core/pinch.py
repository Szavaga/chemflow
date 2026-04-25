"""
Pinch Analysis — Problem Table Algorithm (Linnhoff & Flower, 1978).

Calculates minimum energy targets (Q_H_min, Q_C_min) and the pinch
temperature for a set of hot and cold process streams, before any
heat exchanger network is designed.

Algorithm outline
-----------------
1. Shift stream temperatures by ΔT_min/2 to create a single
   temperature scale.
2. Build temperature intervals from all shifted boundaries.
3. Compute heat surplus/deficit in each interval.
4. Cascade heat from high-T to low-T intervals.
5. The pinch is the highest temperature where the adjusted cascade = 0.
6. Q_H_min  = amount of external heat needed at the top.
   Q_C_min  = residual heat leaving at the bottom.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


# ── Stream types ──────────────────────────────────────────────────────────────

@dataclass
class HotStream:
    """Process stream being cooled (supply_temp > target_temp)."""
    supply_temp: float          # °C  higher temperature
    target_temp: float          # °C  lower temperature
    cp: float                   # kW/K  (= ṁ · Cₚ)
    name: str = ""

    def __post_init__(self) -> None:
        if self.supply_temp <= self.target_temp:
            raise ValueError(
                f"HotStream '{self.name}': supply_temp ({self.supply_temp}) "
                f"must be > target_temp ({self.target_temp})"
            )
        if self.cp <= 0:
            raise ValueError(f"HotStream '{self.name}': cp must be > 0, got {self.cp}")


@dataclass
class ColdStream:
    """Process stream being heated (supply_temp < target_temp)."""
    supply_temp: float          # °C  lower temperature
    target_temp: float          # °C  higher temperature
    cp: float                   # kW/K
    name: str = ""

    def __post_init__(self) -> None:
        if self.supply_temp >= self.target_temp:
            raise ValueError(
                f"ColdStream '{self.name}': target_temp ({self.target_temp}) "
                f"must be > supply_temp ({self.supply_temp})"
            )
        if self.cp <= 0:
            raise ValueError(f"ColdStream '{self.name}': cp must be > 0, got {self.cp}")


# ── Interval & result types ───────────────────────────────────────────────────

@dataclass
class TemperatureInterval:
    t_high: float               # upper shifted boundary (°C)
    t_low: float                # lower shifted boundary (°C)
    hcp_sum: float              # sum of CPs of active hot streams (kW/K)
    ccp_sum: float              # sum of CPs of active cold streams (kW/K)
    delta_h: float              # net heat in this interval: + surplus, − deficit (kW)
    cascade_in: float = 0.0    # adjusted residual entering from above (kW)
    cascade_out: float = 0.0   # adjusted residual leaving below (kW)


@dataclass
class PinchResult:
    pinch_temperature: float                                # hot-side pinch (°C)
    q_h_min: float                                          # minimum hot utility (kW)
    q_c_min: float                                          # minimum cold utility (kW)
    delta_T_min: float = 10.0
    temperature_intervals: list[TemperatureInterval] = field(default_factory=list)
    hot_composite: list[dict[str, float]] = field(default_factory=list)   # [{T, H}]
    cold_composite: list[dict[str, float]] = field(default_factory=list)  # [{T, H}]
    above_pinch_streams: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    below_pinch_streams: dict[str, list[dict[str, Any]]] = field(default_factory=dict)


# ── Main entry point ──────────────────────────────────────────────────────────

def run_pinch_analysis(
    hot_streams: list[HotStream],
    cold_streams: list[ColdStream],
    delta_T_min: float = 10.0,
) -> PinchResult:
    """
    Problem Table Algorithm for minimum-energy targeting.

    Parameters
    ----------
    hot_streams   : streams to be cooled (supply_temp > target_temp)
    cold_streams  : streams to be heated (supply_temp < target_temp)
    delta_T_min   : minimum approach temperature difference (K or °C)

    Returns
    -------
    PinchResult containing pinch_temperature, q_h_min, q_c_min, interval
    data, composite curve points, and above/below-pinch stream lists.

    Raises
    ------
    ValueError if no streams are provided or fewer than 2 temperature levels.
    """
    if not hot_streams and not cold_streams:
        raise ValueError("At least one stream is required for pinch analysis")

    shift = delta_T_min / 2.0

    # Shifted temperature ranges [t_hi, t_lo] for each stream
    hot_shifted = [(s.supply_temp - shift, s.target_temp - shift, s) for s in hot_streams]
    cold_shifted = [(s.target_temp + shift, s.supply_temp + shift, s) for s in cold_streams]

    # All unique shifted temperatures, descending
    all_temps: list[float] = sorted(
        {t for hi, lo, _ in hot_shifted + cold_shifted for t in (hi, lo)},  # type: ignore[operator]
        reverse=True,
    )
    if len(all_temps) < 2:
        raise ValueError("Pinch analysis requires at least 2 distinct temperature levels")

    # ── Build intervals ───────────────────────────────────────────────────────
    intervals: list[TemperatureInterval] = []
    for i in range(len(all_temps) - 1):
        t_hi = all_temps[i]
        t_lo = all_temps[i + 1]
        dt   = t_hi - t_lo

        # A stream is active in [t_lo, t_hi] when the interval is fully within
        # the stream's shifted range: stream_lo ≤ t_lo  AND  stream_hi ≥ t_hi
        hcp = sum(s.cp for s_hi, s_lo, s in hot_shifted if s_lo <= t_lo and s_hi >= t_hi)
        ccp = sum(s.cp for s_hi, s_lo, s in cold_shifted if s_lo <= t_lo and s_hi >= t_hi)

        intervals.append(TemperatureInterval(
            t_high=t_hi,
            t_low=t_lo,
            hcp_sum=hcp,
            ccp_sum=ccp,
            delta_h=(hcp - ccp) * dt,
        ))

    # ── Infeasible cascade (seed = 0) ─────────────────────────────────────────
    residuals: list[float] = [0.0]
    for iv in intervals:
        residuals.append(residuals[-1] + iv.delta_h)

    min_residual = min(residuals)
    q_h_min      = max(0.0, -min_residual)
    q_c_min      = residuals[-1] + q_h_min   # = R_bottom + Q_H_min

    # ── Adjusted cascade ──────────────────────────────────────────────────────
    adjusted = [r + q_h_min for r in residuals]

    for k, iv in enumerate(intervals):
        iv.cascade_in  = round(adjusted[k],     6)
        iv.cascade_out = round(adjusted[k + 1], 6)

    # ── Pinch: highest T where adjusted cascade = 0 (skip position 0) ────────
    _EPS = 1e-6
    pinch_shifted: float = all_temps[-1]   # fallback: bottom of diagram
    for k in range(1, len(adjusted)):
        if abs(adjusted[k]) < _EPS:
            pinch_shifted = all_temps[k]
            break

    pinch_hot = pinch_shifted + shift       # back to real temperatures

    # ── Composite curves ──────────────────────────────────────────────────────
    hot_composite  = _build_hot_composite(hot_streams)
    cold_composite = _build_cold_composite(cold_streams)

    # ── Above / below pinch stream classification ─────────────────────────────
    cold_pinch = pinch_hot - delta_T_min

    above: dict[str, list[dict[str, Any]]] = {"hot": [], "cold": []}
    below: dict[str, list[dict[str, Any]]] = {"hot": [], "cold": []}

    for hs in hot_streams:
        if hs.supply_temp > pinch_hot:
            above["hot"].append({
                "name": hs.name,
                "supply_temp": hs.supply_temp,
                "target_temp": max(hs.target_temp, pinch_hot),
                "cp": hs.cp,
            })
        if hs.target_temp < pinch_hot:
            below["hot"].append({
                "name": hs.name,
                "supply_temp": min(hs.supply_temp, pinch_hot),
                "target_temp": hs.target_temp,
                "cp": hs.cp,
            })

    for cs in cold_streams:
        if cs.target_temp > cold_pinch:
            above["cold"].append({
                "name": cs.name,
                "supply_temp": max(cs.supply_temp, cold_pinch),
                "target_temp": cs.target_temp,
                "cp": cs.cp,
            })
        if cs.supply_temp < cold_pinch:
            below["cold"].append({
                "name": cs.name,
                "supply_temp": cs.supply_temp,
                "target_temp": min(cs.target_temp, cold_pinch),
                "cp": cs.cp,
            })

    return PinchResult(
        pinch_temperature=round(pinch_hot, 4),
        q_h_min=round(q_h_min, 4),
        q_c_min=round(q_c_min, 4),
        delta_T_min=delta_T_min,
        temperature_intervals=intervals,
        hot_composite=hot_composite,
        cold_composite=cold_composite,
        above_pinch_streams=above,
        below_pinch_streams=below,
    )


# ── Composite curve builders ──────────────────────────────────────────────────

def _build_hot_composite(hot_streams: list[HotStream]) -> list[dict[str, float]]:
    """
    Hot composite curve: {T, H} points where H is the cumulative heat released
    (kW) as T decreases from T_max to T_min.  H = 0 at T_max.
    """
    if not hot_streams:
        return []
    temps = sorted({t for hs in hot_streams for t in (hs.supply_temp, hs.target_temp)}, reverse=True)
    points: list[dict[str, float]] = [{"T": temps[0], "H": 0.0}]
    H = 0.0
    for i in range(len(temps) - 1):
        t_hi, t_lo = temps[i], temps[i + 1]
        hcp = sum(hs.cp for hs in hot_streams if hs.target_temp <= t_lo and hs.supply_temp >= t_hi)
        H += hcp * (t_hi - t_lo)
        points.append({"T": t_lo, "H": round(H, 4)})
    return points


def _build_cold_composite(cold_streams: list[ColdStream]) -> list[dict[str, float]]:
    """
    Cold composite curve: {T, H} points where H is the cumulative heat absorbed
    (kW) as T increases from T_min to T_max.  H = 0 at T_min.
    """
    if not cold_streams:
        return []
    temps = sorted({t for cs in cold_streams for t in (cs.supply_temp, cs.target_temp)})
    points: list[dict[str, float]] = [{"T": temps[0], "H": 0.0}]
    H = 0.0
    for i in range(len(temps) - 1):
        t_lo, t_hi = temps[i], temps[i + 1]
        ccp = sum(cs.cp for cs in cold_streams if cs.supply_temp <= t_lo and cs.target_temp >= t_hi)
        H += ccp * (t_hi - t_lo)
        points.append({"T": t_hi, "H": round(H, 4)})
    return points


# ── Flowsheet extraction helper ───────────────────────────────────────────────

def extract_streams_from_flowsheet(
    nodes: list[dict[str, Any]],
    edges: list[dict[str, Any]],
    streams: dict[str, Any],
) -> tuple[list[HotStream], list[ColdStream]]:
    """
    Auto-extract hot and cold process streams from a solved flowsheet.

    Strategy: for each heat_exchanger node, locate its single inlet edge
    and single outlet edge.  The stream is classified as hot (supply > target)
    or cold (supply < target) based on the temperature change across the unit.
    The CP is estimated from the inlet stream's flow and ideal-gas mixture Cp.

    Streams whose inlet and outlet temperatures are equal (within 0.1 K) are
    ignored — they carry no thermal load.
    """
    from app.core.thermo import mixture_Cp_ig, mixture_Cp_liquid

    node_map  = {n["id"]: n for n in nodes}
    edge_list = list(edges)

    hot:  list[HotStream]  = []
    cold: list[ColdStream] = []

    for node in nodes:
        if node.get("type") != "heat_exchanger":
            continue

        nid = node["id"]

        inlet_edges  = [e for e in edge_list if e.get("target") == nid]
        outlet_edges = [e for e in edge_list if e.get("source") == nid]

        if len(inlet_edges) != 1 or len(outlet_edges) != 1:
            continue   # skip malformed HEX nodes

        in_stream  = streams.get(inlet_edges[0]["id"])
        out_stream = streams.get(outlet_edges[0]["id"])

        if in_stream is None or out_stream is None:
            continue

        T_supply = float(in_stream.get("temperature", 0.0))
        T_target = float(out_stream.get("temperature", 0.0))

        if abs(T_target - T_supply) < 0.1:
            continue

        flow = float(in_stream.get("flow", 0.0))
        if flow <= 0:
            continue

        comp = in_stream.get("composition") or {}
        vf   = float(in_stream.get("vapor_fraction") or 0.0)

        # Molar Cp in J/(mol·K); convert to kW/K via flow (mol/s) / 1000
        try:
            cp_molar = (
                mixture_Cp_ig(comp) if vf >= 0.5 else mixture_Cp_liquid(comp)
            )
        except Exception:
            cp_molar = 30.0   # rough default: ~30 J/(mol·K)

        cp_kw = cp_molar * flow / 1000.0
        if cp_kw <= 0:
            continue

        label = (node.get("data") or {}).get("label") or node.get("label") or nid

        if T_supply > T_target:
            hot.append(HotStream(T_supply, T_target, cp_kw, name=str(label)))
        else:
            cold.append(ColdStream(T_supply, T_target, cp_kw, name=str(label)))

    return hot, cold
