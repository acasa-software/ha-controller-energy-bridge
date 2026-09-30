"""Today's energy per meter (protocol §14)."""

from __future__ import annotations

import pytest

from custom_components.controller_energy_bridge.today import TodayMeter


def test_no_value_before_the_first_rebase() -> None:
    meter = TodayMeter()
    assert meter.update(100.0) is None
    assert meter.value is None


def test_rebase_pins_statistics_to_the_live_meter() -> None:
    meter = TodayMeter()
    # 4 kWh since midnight up to the last compiled period, which ended at a reading of 104;
    # the live meter already shows 104.2.
    assert meter.rebase(4.0, 104.0, 104.2) == pytest.approx(4.2)
    assert meter.update(105.0) == pytest.approx(5.0)
    assert meter.value == pytest.approx(5.0)


def test_reset_adds_the_new_count_to_what_today_had() -> None:
    meter = TodayMeter()
    meter.rebase(4.0, 104.0, 104.0)
    meter.update(106.0)  # today 6
    assert meter.update(0.5) == pytest.approx(6.5)
    assert meter.update(1.0) == pytest.approx(7.0)


def test_reset_between_statistics_and_now() -> None:
    meter = TodayMeter()
    assert meter.rebase(4.0, 104.0, 0.3) == pytest.approx(4.3)


def test_unavailable_live_meter_keeps_the_reference() -> None:
    meter = TodayMeter()
    assert meter.rebase(4.0, 104.0, None) == pytest.approx(4.0)
    assert meter.value == pytest.approx(4.0)
    assert meter.update(104.5) == pytest.approx(4.5)


def test_without_a_live_meter_the_statistics_value_stands() -> None:
    meter = TodayMeter()
    assert meter.rebase(12.5, None, None) == pytest.approx(12.5)
    assert meter.update(99.0) == pytest.approx(12.5)
    assert meter.value == pytest.approx(12.5)


def test_midnight_rebase_starts_again_from_zero() -> None:
    meter = TodayMeter()
    meter.rebase(20.0, 120.0, 120.0)
    assert meter.rebase(0.0, 121.0, 121.2) == pytest.approx(0.2)


def test_negative_change_is_clamped() -> None:
    meter = TodayMeter()
    assert meter.rebase(-1.0, None, None) == 0.0


def test_reset_after_the_compiled_period_keeps_the_running_value() -> None:
    meter = TodayMeter()
    meter.rebase(4.0, 104.0, 104.0)
    meter.update(106.0)
    assert meter.update(0.5) == pytest.approx(6.5)
    # The recorder still reports the pre-reset period: rebasing on it would drop 2 kWh.
    assert meter.rebase(4.0, 104.0, 0.5) == pytest.approx(6.5)
    assert meter.update(1.0) == pytest.approx(7.0)
