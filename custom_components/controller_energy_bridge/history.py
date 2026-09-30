"""History blobs (protocol §13): packing and bucket series from recorder statistics.

Pure logic, no Home Assistant imports, so it is testable standalone.
"""

from __future__ import annotations

import math
import struct
from collections.abc import Iterable, Mapping, Sequence
from typing import Any

from .accessory import normalise_power
from .const import (
    HISTORY_BLOB_VERSION,
    HISTORY_KIND_ENERGY,
    HISTORY_KIND_POWER,
    NodeRole,
)
from .units import energy_to_kwh, power_to_watts

_HEADER = struct.Struct("<BBBBHIH")

Series = list[float | None]


def aligned_start(now: float, width: int, count: int) -> int:
    """Start of the first bucket so that the last bucket contains `now` (§13.1)."""
    last_start = int(now // width) * width
    return last_start - (count - 1) * width


def pack_history(kind: int, bucket_seconds: int, start: int, series: Sequence[Series]) -> bytes:
    """Serialise the blob per §13.1: 12-byte header, then float32 values series-major."""
    if kind not in (HISTORY_KIND_ENERGY, HISTORY_KIND_POWER):
        raise ValueError(f"unknown history kind {kind}")
    if not 1 <= len(series) <= 2:
        raise ValueError("history needs one or two series")
    count = len(series[0])
    if any(len(values) != count for values in series):
        raise ValueError("all series must have the same length")
    header = _HEADER.pack(HISTORY_BLOB_VERSION, kind, len(series), 0, bucket_seconds, start, count)
    flat = [math.nan if value is None else float(value) for values in series for value in values]
    return header + struct.pack(f"<{len(flat)}f", *flat)


def unpack_history(blob: bytes) -> dict[str, Any]:
    """Inverse of `pack_history`; None where the blob holds NaN. Used by tests and diagnostics."""
    version, kind, series_count, _reserved, width, start, count = _HEADER.unpack_from(blob)
    values = struct.unpack_from(f"<{count * series_count}f", blob, _HEADER.size)
    series = [
        [None if math.isnan(value) else value for value in values[i * count : (i + 1) * count]]
        for i in range(series_count)
    ]
    return {
        "version": version,
        "kind": kind,
        "width": width,
        "start": start,
        "count": count,
        "series": series,
    }


def row_start_of(row: Mapping[str, Any]) -> float | None:
    start = row.get("start")
    if start is None:
        return None
    if isinstance(start, int | float):
        return float(start)
    timestamp = getattr(start, "timestamp", None)
    return float(timestamp()) if callable(timestamp) else None


def _bucket_index(row_start: float, start: int, width: int, count: int) -> int | None:
    index = int((row_start - start) // width)
    return index if 0 <= index < count else None


def energy_series_from_statistics(
    rows: Iterable[Mapping[str, Any]],
    start: int,
    width: int,
    count: int,
    unit: str | None = None,
) -> Series:
    """Energy delivered per bucket in kWh from hourly `change` rows; None where no row exists."""
    series: Series = [None] * count
    for row in rows:
        change = row.get("change")
        row_start = row_start_of(row)
        if change is None or row_start is None:
            continue
        index = _bucket_index(row_start, start, width, count)
        if index is None:
            continue
        value = energy_to_kwh(float(change), unit)
        series[index] = value if series[index] is None else series[index] + value
    return series


def power_series_from_statistics(
    rows: Iterable[Mapping[str, Any]],
    start: int,
    width: int,
    count: int,
    unit: str | None = None,
    *,
    role: NodeRole,
    inverted: bool = False,
) -> Series:
    """Mean power per bucket in W from 5-minute `mean` rows, with the node's sign rules (§7)."""
    sums = [0.0] * count
    weights = [0] * count
    for row in rows:
        mean = row.get("mean")
        row_start = row_start_of(row)
        if mean is None or row_start is None:
            continue
        index = _bucket_index(row_start, start, width, count)
        if index is None:
            continue
        sums[index] += power_to_watts(float(mean), unit)
        weights[index] += 1
    return [
        normalise_power(role, sums[i] / weights[i], inverted) if weights[i] else None
        for i in range(count)
    ]


def nan_count(series: Sequence[Series]) -> int:
    return sum(1 for values in series for value in values if value is None)
