"""History blob packing and series building against protocol §13."""

from __future__ import annotations

import math
import struct

import pytest

from custom_components.controller_energy_bridge.const import (
    ENERGY_HISTORY_BUCKET_COUNT,
    ENERGY_HISTORY_BUCKET_SECONDS,
    ENERGY_HISTORY_MAX_BYTES,
    HISTORY_KIND_ENERGY,
    HISTORY_KIND_POWER,
    POWER_HISTORY_BUCKET_COUNT,
    POWER_HISTORY_BUCKET_SECONDS,
    POWER_HISTORY_MAX_BYTES,
    NodeRole,
)
from custom_components.controller_energy_bridge.history import (
    aligned_start,
    energy_series_from_statistics,
    nan_count,
    pack_history,
    power_series_from_statistics,
    unpack_history,
)

# --- packing (§13.1) --------------------------------------------------------


def test_pack_header_and_values_exact_bytes() -> None:
    blob = pack_history(HISTORY_KIND_POWER, 900, 1_700_000_100, [[1.0, None, -2.5]])
    assert blob[0] == 1  # version
    assert blob[1] == 2  # kind power
    assert blob[2] == 1  # series count
    assert blob[3] == 0  # reserved
    assert blob[4:6] == (900).to_bytes(2, "little")
    assert blob[6:10] == (1_700_000_100).to_bytes(4, "little")
    assert blob[10:12] == (3).to_bytes(2, "little")
    assert blob[12:16] == struct.pack("<f", 1.0) == b"\x00\x00\x80\x3f"
    assert math.isnan(struct.unpack("<f", blob[16:20])[0])
    assert blob[20:24] == struct.pack("<f", -2.5)
    assert len(blob) == 12 + 3 * 4


def test_pack_two_series_is_series_major() -> None:
    blob = pack_history(HISTORY_KIND_ENERGY, 3600, 0, [[1.0, 2.0], [3.0, 4.0]])
    assert blob[2] == 2
    assert struct.unpack("<4f", blob[12:]) == (1.0, 2.0, 3.0, 4.0)
    unpacked = unpack_history(blob)
    assert unpacked["kind"] == HISTORY_KIND_ENERGY
    assert unpacked["series"] == [[1.0, 2.0], [3.0, 4.0]]


def test_pack_sizes_match_protocol_maxima() -> None:
    energy = pack_history(
        HISTORY_KIND_ENERGY,
        ENERGY_HISTORY_BUCKET_SECONDS,
        0,
        [[None] * ENERGY_HISTORY_BUCKET_COUNT] * 2,
    )
    power = pack_history(
        HISTORY_KIND_POWER, POWER_HISTORY_BUCKET_SECONDS, 0, [[None] * POWER_HISTORY_BUCKET_COUNT]
    )
    assert len(energy) == ENERGY_HISTORY_MAX_BYTES == 5772
    assert len(power) == POWER_HISTORY_MAX_BYTES == 396


@pytest.mark.parametrize(
    ("kind", "series"),
    [
        (3, [[1.0]]),
        (HISTORY_KIND_ENERGY, []),
        (HISTORY_KIND_ENERGY, [[1.0], [1.0], [1.0]]),
        (HISTORY_KIND_ENERGY, [[1.0, 2.0], [1.0]]),
    ],
)
def test_pack_rejects_invalid_input(kind: int, series: list[list[float | None]]) -> None:
    with pytest.raises(ValueError):
        pack_history(kind, 3600, 0, series)


# --- alignment --------------------------------------------------------------


def test_aligned_start_last_bucket_contains_now() -> None:
    now = 1_700_003_599.0
    start = aligned_start(now, 3600, 720)
    assert start % 3600 == 0
    last_start = start + 719 * 3600
    assert last_start <= now < last_start + 3600


def test_aligned_start_exact_boundary_and_power_width() -> None:
    assert aligned_start(3600 * 10, 3600, 3) == 3600 * 8
    assert aligned_start(3600 * 10 + 1, 3600, 3) == 3600 * 8
    assert aligned_start(3600 * 10 - 1, 3600, 3) == 3600 * 7
    start = aligned_start(1_700_000_000, 900, 96)
    assert start % 900 == 0
    assert start + 95 * 900 <= 1_700_000_000 < start + 96 * 900


# --- energy series (§13.2) --------------------------------------------------


def test_energy_series_places_change_per_hour_with_gaps() -> None:
    start = 3600 * 100
    rows = [
        {"start": float(start), "change": 0.5},
        {"start": float(start + 3600), "change": None},  # compiled but no change
        {"start": float(start + 3 * 3600), "change": 1.25},
        {"start": float(start - 3600), "change": 9.0},  # before window
        {"start": float(start + 4 * 3600), "change": 9.0},  # after window
        {"change": 9.0},  # malformed
    ]
    series = energy_series_from_statistics(rows, start, 3600, 4)
    assert series == [0.5, None, None, 1.25]
    assert nan_count([series]) == 2


def test_energy_series_converts_units_and_sums_same_bucket() -> None:
    start = 0
    rows = [{"start": 0.0, "change": 500.0}, {"start": 10.0, "change": 250.0}]
    assert energy_series_from_statistics(rows, start, 3600, 1, "Wh") == [pytest.approx(0.75)]
    assert energy_series_from_statistics(rows, start, 3600, 1, "kWh") == [750.0]


def test_energy_series_accepts_datetime_start() -> None:
    from datetime import UTC, datetime

    start = 3600 * 5
    rows = [{"start": datetime.fromtimestamp(start + 3600, tz=UTC), "change": 2.0}]
    assert energy_series_from_statistics(rows, start, 3600, 2) == [None, 2.0]


# --- power series (§13.3) ---------------------------------------------------


def test_power_series_averages_available_five_minute_means() -> None:
    start = 900 * 10
    rows = [
        {"start": float(start), "mean": 100.0},
        {"start": float(start + 300), "mean": 200.0},
        {"start": float(start + 600), "mean": 300.0},
        {"start": float(start + 900), "mean": 50.0},  # only one of three present
        {"start": float(start + 900 + 600), "mean": None},  # ignored
    ]
    series = power_series_from_statistics(rows, start, 900, 3, "W", role=NodeRole.GRID)
    assert series == [200.0, 50.0, None]


def test_power_series_units_inversion_and_clamp() -> None:
    start = 0
    rows = [{"start": 0.0, "mean": -1.5}]  # kW
    grid = power_series_from_statistics(rows, start, 900, 1, "kW", role=NodeRole.GRID)
    assert grid == [-1500.0]
    inverted = power_series_from_statistics(
        rows, start, 900, 1, "kW", role=NodeRole.GRID, inverted=True
    )
    assert inverted == [1500.0]
    solar = power_series_from_statistics(rows, start, 900, 1, "kW", role=NodeRole.SOLAR)
    assert solar == [0.0]  # negative solar clamps to 0 (§7)
    solar_inverted = power_series_from_statistics(
        rows, start, 900, 1, "kW", role=NodeRole.SOLAR, inverted=True
    )
    assert solar_inverted == [1500.0]
    huge = power_series_from_statistics(
        [{"start": 0.0, "mean": 5e6}], start, 900, 1, "W", role=NodeRole.GRID
    )
    assert huge == [1_000_000.0]


def test_power_series_round_trips_through_blob() -> None:
    start = aligned_start(1_700_000_000, 900, 96)
    rows = [{"start": float(start + i * 300), "mean": float(i)} for i in range(6)]
    series = power_series_from_statistics(rows, start, 900, 96, "W", role=NodeRole.HOME)
    blob = pack_history(HISTORY_KIND_POWER, 900, start, [series])
    unpacked = unpack_history(blob)
    assert unpacked["start"] == start
    assert unpacked["width"] == 900
    assert unpacked["count"] == 96
    assert unpacked["series"][0][:2] == [1.0, 4.0]
    assert unpacked["series"][0][2] is None
