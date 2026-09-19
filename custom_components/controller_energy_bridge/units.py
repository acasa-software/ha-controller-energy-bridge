"""Unit normalisation for sensor values fed into the bridge.

The protocol fixes units per characteristic (Power in W, Energy in kWh, SoC in %),
so every source value is converted based on its Home Assistant unit_of_measurement.
"""

from __future__ import annotations

_POWER_FACTORS_TO_W: dict[str, float] = {
    "w": 1.0,
    "kw": 1_000.0,
    "mw": 1_000_000.0,
    "gw": 1_000_000_000.0,
    "btu/h": 0.29307107,
}

_ENERGY_FACTORS_TO_KWH: dict[str, float] = {
    "wh": 0.001,
    "kwh": 1.0,
    "mwh": 1_000.0,
    "gwh": 1_000_000.0,
    "twh": 1_000_000_000.0,
    "j": 1.0 / 3_600_000.0,
    "kj": 1.0 / 3_600.0,
    "mj": 1.0 / 3.6,
    "gj": 1_000.0 / 3.6,
    "cal": 4.184 / 3_600_000.0,
    "kcal": 4.184 / 3_600.0,
    "mcal": 4.184 / 3.6,
    "gcal": 4_184.0 / 3.6,
}


def _normalise_unit(unit: str | None) -> str:
    if unit is None:
        return ""
    return unit.strip().replace(" ", "").lower()


def parse_numeric(value: object) -> float | None:
    """Return the value as float, or None if it is not a finite number."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int | float):
        result = float(value)
    elif isinstance(value, str):
        try:
            result = float(value.strip())
        except ValueError:
            return None
    else:
        return None
    if result != result or result in (float("inf"), float("-inf")):
        return None
    return result


def power_to_watts(value: float, unit: str | None) -> float:
    """Convert a power reading to W. Unknown or missing units are treated as W."""
    return value * _POWER_FACTORS_TO_W.get(_normalise_unit(unit), 1.0)


def energy_to_kwh(value: float, unit: str | None) -> float:
    """Convert an energy reading to kWh. Unknown or missing units are treated as kWh."""
    return value * _ENERGY_FACTORS_TO_KWH.get(_normalise_unit(unit), 1.0)


def soc_to_percent(value: float, unit: str | None) -> float:
    """State of charge is always taken as %; the unit is accepted for symmetry only."""
    return value
