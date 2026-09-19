"""Unit normalisation."""

from __future__ import annotations

import pytest

from custom_components.controller_energy_bridge.units import (
    energy_to_kwh,
    parse_numeric,
    power_to_watts,
    soc_to_percent,
)


@pytest.mark.parametrize(
    ("value", "unit", "expected"),
    [
        (1500.0, "W", 1500.0),
        (1.5, "kW", 1500.0),
        (0.002, "MW", 2000.0),
        (12.0, None, 12.0),
        (12.0, "banana", 12.0),
        (3.0, " kw ", 3000.0),
    ],
)
def test_power_to_watts(value: float, unit: str | None, expected: float) -> None:
    assert power_to_watts(value, unit) == pytest.approx(expected)


@pytest.mark.parametrize(
    ("value", "unit", "expected"),
    [
        (500000.0, "Wh", 500.0),
        (500.0, "kWh", 500.0),
        (0.5, "MWh", 500.0),
        (10.0, "GWh", 10_000_000.0),
        (3_600_000.0, "J", 1.0),
        (3600.0, "kJ", 1.0),
        (5.0, None, 5.0),
        (5.0, "kwh", 5.0),
    ],
)
def test_energy_to_kwh(value: float, unit: str | None, expected: float) -> None:
    assert energy_to_kwh(value, unit) == pytest.approx(expected)


def test_soc_passthrough() -> None:
    assert soc_to_percent(28.0, "%") == 28.0
    assert soc_to_percent(0.5, None) == 0.5


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("12.5", 12.5),
        (" -3 ", -3.0),
        (7, 7.0),
        (0, 0.0),
        ("unavailable", None),
        ("", None),
        ("nan", None),
        ("inf", None),
        (True, None),
        (None, None),
        ([1], None),
    ],
)
def test_parse_numeric(raw: object, expected: float | None) -> None:
    assert parse_numeric(raw) == expected
