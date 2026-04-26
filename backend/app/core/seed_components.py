"""
Seed script — populate chemical_components with 50 common industrial compounds.

Uses the 'chemicals' package for critical properties (Tc, Pc, omega, MW).
Antoine coefficients come from chemicals' internal data where available;
missing properties are stored as NULL per spec.

Run once after DB initialisation:
    python -m app.core.seed_components
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

logger = logging.getLogger(__name__)

# ── 50 compounds by CAS number ─────────────────────────────────────────────────

SEED_CAS: list[tuple[str, str]] = [
    ("Water",                 "7732-18-5"),
    ("Ethanol",               "64-17-5"),
    ("Methanol",              "67-56-1"),
    ("Acetone",               "67-64-1"),
    ("Benzene",               "71-43-2"),
    ("Toluene",               "108-88-3"),
    ("Ethylene",              "74-85-1"),
    ("Propylene",             "115-07-1"),
    ("n-Butane",              "106-97-8"),
    ("n-Hexane",              "110-54-3"),
    ("n-Heptane",             "142-82-5"),
    ("Cyclohexane",           "110-82-7"),
    ("Acetic acid",           "64-19-7"),
    ("Ethyl acetate",         "141-78-6"),
    ("Chloroform",            "67-66-3"),
    ("Ammonia",               "7664-41-7"),
    ("Carbon dioxide",        "124-38-9"),
    ("Nitrogen",              "7727-37-9"),
    ("Oxygen",                "7782-44-7"),
    ("Hydrogen",              "1333-74-0"),
    ("Methane",               "74-82-8"),
    ("Ethane",                "74-84-0"),
    ("Propane",               "74-98-6"),
    ("Isobutane",             "75-28-5"),
    ("n-Pentane",             "109-66-0"),
    ("Isopentane",            "78-78-4"),
    ("n-Octane",              "111-65-9"),
    ("Styrene",               "100-42-5"),
    ("Vinyl chloride",        "75-01-4"),
    ("Acetaldehyde",          "75-07-0"),
    ("Formaldehyde",          "50-00-0"),
    ("Formic acid",           "64-18-6"),
    ("Phenol",                "108-95-2"),
    ("Aniline",               "62-53-3"),
    ("Glycerol",              "56-81-5"),
    ("Ethylene glycol",       "107-21-1"),
    ("Dimethyl sulfoxide",    "67-68-5"),
    ("Tetrahydrofuran",       "109-99-9"),
    ("Diethyl ether",         "60-29-7"),
    ("Acetonitrile",          "75-05-8"),
    ("Hydrogen chloride",     "7647-01-0"),
    ("Hydrogen sulfide",      "7783-06-4"),
    ("Sulfur dioxide",        "7446-09-5"),
    ("Nitric oxide",          "10102-43-9"),
    ("Carbon monoxide",       "630-08-0"),
    ("Isoprene",              "78-79-5"),
    ("p-Xylene",              "106-42-3"),
    ("o-Xylene",              "95-47-6"),
    ("m-Xylene",              "108-38-3"),
    ("Cumene",                "98-82-8"),
]


def _fetch_properties(cas: str) -> dict[str, Any]:
    """Fetch all available properties from the chemicals package for one CAS."""
    props: dict[str, Any] = {}

    # Critical temperature (K)
    try:
        from chemicals import Tc
        val = Tc(cas)
        props["tc"] = float(val) if val is not None else None
    except Exception:
        props["tc"] = None

    # Critical pressure (Pa)
    try:
        from chemicals import Pc
        val = Pc(cas)
        props["pc"] = float(val) if val is not None else None
    except Exception:
        props["pc"] = None

    # Acentric factor
    try:
        from chemicals import omega
        val = omega(cas)
        props["omega"] = float(val) if val is not None else None
    except Exception:
        props["omega"] = None

    # Molecular weight (g/mol)
    try:
        from chemicals import MW
        val = MW(cas)
        props["mw"] = float(val) if val is not None else None
    except Exception:
        props["mw"] = None

    # Molecular formula — try several chemicals API variants
    formula = None
    for _get_formula in _formula_getters():
        try:
            result = _get_formula(cas)
            if result:
                formula = result
                break
        except Exception:
            pass
    props["formula"] = formula

    # Antoine coefficients — chemicals stores log10(P/Pa) = A - B/(C+T), T in K
    # We store the same convention and record units as "Pa" with T in K.
    props.update(_fetch_antoine(cas))

    return props


def _formula_getters():
    """Yield callables that try to get a molecular formula from a CAS."""
    # chemicals >= 1.0 exposes formula via identifiers or serialize
    def _via_serialize(cas):
        from chemicals.serialize import formula as fmt
        return fmt(cas)

    def _via_identifiers(cas):
        from chemicals.identifiers import CAS_to_formula
        return CAS_to_formula(cas)

    def _via_pubchem(cas):
        from chemicals.identifiers import search_chemical
        meta = search_chemical(cas)
        return getattr(meta, "formula", None)

    return [_via_serialize, _via_identifiers, _via_pubchem]


def _fetch_antoine(cas: str) -> dict[str, Any]:
    """Try to extract Antoine (A, B, C, Tmin, Tmax) from chemicals internals.

    The chemicals package stores Antoine data as log10(P/Pa) = A - B/(C+T)
    with T in K.  We persist the same convention and mark antoine_units="Pa".
    """
    null = {
        "antoine_a": None, "antoine_b": None, "antoine_c": None,
        "antoine_tmin": None, "antoine_tmax": None, "antoine_units": None,
    }

    # Strategy 1: chemicals.vapor_pressure.AntoineABC (some versions)
    try:
        from chemicals.vapor_pressure import AntoineABC  # type: ignore[attr-defined]
        result = AntoineABC(cas)
        if result is not None:
            A, B, C, Tmin, Tmax = result
            return {
                "antoine_a": float(A), "antoine_b": float(B), "antoine_c": float(C),
                "antoine_tmin": float(Tmin), "antoine_tmax": float(Tmax),
                "antoine_units": "Pa",
            }
    except Exception:
        pass

    # Strategy 2: internal data frames in chemicals.vapor_pressure
    try:
        import chemicals.vapor_pressure as _vp
        # Try _AntoineABC_data, _Antoine_extended_data, etc.
        for attr in ("_AntoineABC_data", "_Antoine_extended_data", "_Antoine_data"):
            df = getattr(_vp, attr, None)
            if df is None:
                continue
            import pandas as pd  # only if chemicals is installed, pandas usually is too
            if isinstance(df, pd.DataFrame) and cas in df.index:
                row = df.loc[cas]
                A = float(row.get("A", row.iloc[0]))
                B = float(row.get("B", row.iloc[1]))
                C = float(row.get("C", row.iloc[2]))
                Tmin = float(row.get("Tmin", row.iloc[3])) if len(row) > 3 else None
                Tmax = float(row.get("Tmax", row.iloc[4])) if len(row) > 4 else None
                return {
                    "antoine_a": A, "antoine_b": B, "antoine_c": C,
                    "antoine_tmin": Tmin, "antoine_tmax": Tmax,
                    "antoine_units": "Pa",
                }
    except Exception:
        pass

    # Strategy 3: VaporPressure correlation object
    try:
        from chemicals.vapor_pressure import VaporPressure  # type: ignore[attr-defined]
        vp_obj = VaporPressure(CASRN=cas)
        # Try to force loading data
        if hasattr(vp_obj, "load_all_methods"):
            vp_obj.load_all_methods()
        for method_attr in ("ANTOINE_EXTENDED_POLING",
                            "ANTOINE_POLING", "ANTOINE_WEBBOOK"):
            coeffs = getattr(vp_obj, method_attr, None)
            if coeffs is not None and len(coeffs) >= 5:
                A, B, C, Tmin, Tmax = coeffs[:5]
                return {
                    "antoine_a": float(A), "antoine_b": float(B), "antoine_c": float(C),
                    "antoine_tmin": float(Tmin), "antoine_tmax": float(Tmax),
                    "antoine_units": "Pa",
                }
    except Exception:
        pass

    return null


# ── Database seed logic ────────────────────────────────────────────────────────

async def seed_components(session=None) -> int:
    """Insert global components that are not yet present (idempotent).

    Returns the number of rows inserted.
    """
    from app.db import AsyncSessionLocal
    from app.models.orm import ChemicalComponent
    from sqlalchemy import select

    own_session = session is None
    if own_session:
        session = AsyncSessionLocal()

    inserted = 0
    updated = 0
    try:
        for name, cas in SEED_CAS:
            existing = (await session.execute(
                select(ChemicalComponent).where(ChemicalComponent.cas_number == cas)
            )).scalar_one_or_none()

            props = _fetch_properties(cas)

            if existing is None:
                comp = ChemicalComponent(
                    name=name,
                    cas_number=cas,
                    formula=props.get("formula"),
                    mw=props.get("mw"),
                    tc=props.get("tc"),
                    pc=props.get("pc"),
                    omega=props.get("omega"),
                    antoine_a=props.get("antoine_a"),
                    antoine_b=props.get("antoine_b"),
                    antoine_c=props.get("antoine_c"),
                    antoine_tmin=props.get("antoine_tmin"),
                    antoine_tmax=props.get("antoine_tmax"),
                    antoine_units=props.get("antoine_units"),
                    mu_coeffs=None,
                    is_global=True,
                    project_id=None,
                    created_by=None,
                )
                session.add(comp)
                inserted += 1
            elif not existing.is_global or existing.tc is None or existing.pc is None:
                # Upgrade incomplete or mis-classified existing rows.
                existing.is_global = True
                existing.project_id = None
                existing.name = name
                if props.get("tc") is not None:
                    existing.tc = props["tc"]
                if props.get("pc") is not None:
                    existing.pc = props["pc"]
                if props.get("omega") is not None and existing.omega is None:
                    existing.omega = props["omega"]
                if props.get("mw") is not None and existing.mw is None:
                    existing.mw = props["mw"]
                if props.get("formula") is not None and existing.formula is None:
                    existing.formula = props["formula"]
                if props.get("antoine_a") is not None and existing.antoine_a is None:
                    existing.antoine_a = props["antoine_a"]
                    existing.antoine_b = props["antoine_b"]
                    existing.antoine_c = props["antoine_c"]
                    existing.antoine_tmin = props["antoine_tmin"]
                    existing.antoine_tmax = props["antoine_tmax"]
                    existing.antoine_units = props["antoine_units"]
                updated += 1

        await session.commit()
        logger.info("Seeded %d, upgraded %d chemical components", inserted, updated)
    except Exception:
        await session.rollback()
        raise
    finally:
        if own_session:
            await session.close()

    return inserted


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    asyncio.run(seed_components())
