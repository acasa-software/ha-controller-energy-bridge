"""State decoding and per-node throttling in bridge.py."""

from __future__ import annotations

from datetime import timedelta
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from homeassistant.core import HomeAssistant, State
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import async_fire_time_changed

from custom_components.controller_energy_bridge.bridge import EnergyBridge, read_state
from custom_components.controller_energy_bridge.const import (
    ENERGY_HISTORY_BUCKET_COUNT,
    ENERGY_HISTORY_BUCKET_SECONDS,
    HISTORY_KIND_ENERGY,
    HISTORY_KIND_POWER,
    POWER_HISTORY_BUCKET_COUNT,
    POWER_HISTORY_BUCKET_SECONDS,
    NodeRole,
)
from custom_components.controller_energy_bridge.energy_import import NodeConfig
from custom_components.controller_energy_bridge.history import aligned_start, unpack_history


@pytest.mark.parametrize(
    ("state", "value", "faulted"),
    [
        (None, None, True),
        (State("sensor.x", "unavailable"), None, True),
        (State("sensor.x", "unknown"), None, True),
        (State("sensor.x", "on"), None, True),
        (State("sensor.x", "12.5"), 12.5, False),
        (State("sensor.x", "-3"), -3.0, False),
    ],
)
def test_read_state(state: State | None, value: float | None, faulted: bool) -> None:
    reading = read_state(state)
    assert reading.value == value
    assert reading.faulted is faulted


GRID = NodeConfig(NodeRole.GRID, 0, "Netz", "sensor.imp", "sensor.exp", "sensor.grid_p")
NO_POWER = NodeConfig(NodeRole.DEVICE, 1, "Plug", "sensor.plug_e")


def _bridge(hass: HomeAssistant, nodes: list[NodeConfig], interval: int = 5) -> EnergyBridge:
    bridge = EnergyBridge(
        hass, nodes, port=21066, update_interval=interval, serial_number="s", firmware_revision="1"
    )
    bridge.accessory = MagicMock()
    return bridge


def _calls(bridge: EnergyBridge) -> list[dict[str, Any]]:
    return [
        call.kwargs | {"index": call.args[0]}
        for call in bridge.accessory.update_node.call_args_list
    ]


async def test_collect_normalises_units_and_fault(hass: HomeAssistant) -> None:
    hass.states.async_set("sensor.grid_p", "1.5", {"unit_of_measurement": "kW"})
    hass.states.async_set("sensor.imp", "500000", {"unit_of_measurement": "Wh"})
    hass.states.async_set("sensor.exp", "1200.0", {"unit_of_measurement": "kWh"})
    bridge = _bridge(hass, [GRID])
    bridge._collect_node(0)
    bridge._flush(0)
    (call,) = _calls(bridge)
    assert call["power"] == pytest.approx(1500.0)
    assert call["energy_from"] == pytest.approx(500.0)
    assert call["energy_to"] == pytest.approx(1200.0)
    assert call["fault"] is False
    assert call["soc"] is None


async def test_unavailable_power_faults_but_keeps_energy(hass: HomeAssistant) -> None:
    hass.states.async_set("sensor.grid_p", "unavailable")
    hass.states.async_set("sensor.imp", "100", {"unit_of_measurement": "kWh"})
    bridge = _bridge(hass, [GRID])
    bridge._collect_node(0)
    bridge._flush(0)
    (call,) = _calls(bridge)
    assert call["fault"] is True
    assert call["power"] is None
    assert call["energy_from"] == pytest.approx(100.0)


async def test_node_without_power_source_is_always_faulted(hass: HomeAssistant) -> None:
    hass.states.async_set("sensor.plug_e", "5", {"unit_of_measurement": "kWh"})
    bridge = _bridge(hass, [NO_POWER])
    bridge._collect_node(1)
    bridge._flush(1)
    (call,) = _calls(bridge)
    assert call["fault"] is True
    assert call["energy_from"] == 5.0


async def test_throttle_flushes_at_most_once_per_interval(hass: HomeAssistant) -> None:
    hass.states.async_set("sensor.grid_p", "100", {"unit_of_measurement": "W"})
    bridge = _bridge(hass, [GRID], interval=5)

    with patch("custom_components.controller_energy_bridge.bridge.time.monotonic") as monotonic:
        monotonic.return_value = 1000.0
        bridge._collect_node(0)
        bridge._schedule_flush(0)  # first update goes straight through
        assert len(_calls(bridge)) == 1

        monotonic.return_value = 1001.0
        hass.states.async_set("sensor.grid_p", "200", {"unit_of_measurement": "W"})
        bridge._collect_node(0)
        bridge._schedule_flush(0)
        hass.states.async_set("sensor.grid_p", "300", {"unit_of_measurement": "W"})
        bridge._collect_node(0)
        bridge._schedule_flush(0)
        assert len(_calls(bridge)) == 1  # buffered, timer pending
        assert bridge._runtime[0].timer is not None

        monotonic.return_value = 1005.0
        async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=5))
        await hass.async_block_till_done()

    calls = _calls(bridge)
    assert len(calls) == 2
    assert calls[-1]["power"] == 300.0  # latest value wins
    assert bridge._runtime[0].timer is None


async def test_stop_cancels_timers_without_driver(hass: HomeAssistant) -> None:
    hass.states.async_set("sensor.grid_p", "100", {"unit_of_measurement": "W"})
    bridge = _bridge(hass, [GRID], interval=5)
    with patch(
        "custom_components.controller_energy_bridge.bridge.time.monotonic", return_value=10.0
    ):
        bridge._collect_node(0)
        bridge._schedule_flush(0)
        bridge._collect_node(0)
        bridge._schedule_flush(0)
    assert bridge._runtime[0].timer is not None
    await bridge.async_stop()
    assert bridge._runtime[0].timer is None


# --- history (§13) -----------------------------------------------------------

BRIDGE_MODULE = "custom_components.controller_energy_bridge.bridge"
NOW = 1_700_000_000.0


class _Recorder:
    """Stands in for the recorder instance: runs executor jobs inline."""

    async def async_add_executor_job(self, func: Any, *args: Any) -> Any:
        return func(*args)


def _patch_recorder(rows: dict[str, list[dict[str, Any]]], units: dict[str, str]) -> Any:
    metadata = {
        sid: (1, {"statistic_id": sid, "unit_of_measurement": unit}) for sid, unit in units.items()
    }
    return (
        patch(f"{BRIDGE_MODULE}.get_instance", return_value=_Recorder()),
        patch(f"{BRIDGE_MODULE}.statistics_during_period", return_value=rows),
        patch(f"{BRIDGE_MODULE}.get_metadata", return_value=metadata),
        patch(f"{BRIDGE_MODULE}.time.time", return_value=NOW),
    )


async def test_energy_history_rebuild_reads_hourly_change(hass: HomeAssistant) -> None:
    start = aligned_start(NOW, ENERGY_HISTORY_BUCKET_SECONDS, ENERGY_HISTORY_BUCKET_COUNT)
    last = start + (ENERGY_HISTORY_BUCKET_COUNT - 1) * ENERGY_HISTORY_BUCKET_SECONDS
    rows = {
        "sensor.imp": [{"start": float(last), "change": 1500.0}],  # Wh
        "sensor.exp": [{"start": float(start), "change": 0.25}],
    }
    bridge = _bridge(hass, [GRID])
    stats, instance, meta, clock = _patch_recorder(rows, {"sensor.imp": "Wh", "sensor.exp": "kWh"})
    with stats, instance as during_period, meta, clock:
        await bridge.async_rebuild_energy_history()

    args = during_period.call_args.args
    assert args[1] == dt_util.utc_from_timestamp(start)
    assert args[2] is None
    assert args[3] == {"sensor.imp", "sensor.exp"}
    assert args[4] == "hour"
    assert args[6] == {"change"}

    (call,) = bridge.accessory.set_history.call_args_list
    assert call.args == (0,)
    blob = unpack_history(call.kwargs["energy_blob"])
    assert (blob["kind"], blob["width"], blob["start"], blob["count"]) == (
        HISTORY_KIND_ENERGY,
        3600,
        start,
        720,
    )
    assert len(blob["series"]) == 2
    assert blob["series"][0][-1] == pytest.approx(1.5)
    assert blob["series"][1][0] == 0.25
    status = bridge.energy_history[0]
    assert (status.buckets, status.nan, status.size) == (720, 2 * 720 - 2, 5772)
    assert status.rebuilt_at is not None


async def test_power_history_rebuild_reads_five_minute_means(hass: HomeAssistant) -> None:
    start = aligned_start(NOW, POWER_HISTORY_BUCKET_SECONDS, POWER_HISTORY_BUCKET_COUNT)
    rows = {
        "sensor.grid_p": [
            {"start": float(start), "mean": 1.0},
            {"start": float(start + 300), "mean": 3.0},
        ]
    }
    inverted_grid = NodeConfig(
        NodeRole.GRID, 0, "Netz", "sensor.imp", None, "sensor.grid_p", inverted=True
    )
    bridge = _bridge(hass, [inverted_grid, NO_POWER])
    stats, instance, meta, clock = _patch_recorder(rows, {"sensor.grid_p": "kW"})
    with stats, instance as during_period, meta, clock:
        await bridge.async_rebuild_power_history()

    args = during_period.call_args.args
    assert args[3] == {"sensor.grid_p"}
    assert args[4] == "5minute"
    assert args[6] == {"mean"}

    (call,) = bridge.accessory.set_history.call_args_list  # NO_POWER has no power history
    assert call.args == (0,)
    blob = unpack_history(call.kwargs["power_blob"])
    assert (blob["kind"], blob["width"], blob["count"]) == (HISTORY_KIND_POWER, 900, 96)
    assert blob["series"][0][0] == -2000.0  # mean 2 kW, inverted
    assert blob["series"][0][1] is None
    assert bridge.power_history[0].nan == 95
    assert 1 not in bridge.power_history


async def test_history_rebuild_uses_external_statistic_ids(hass: HomeAssistant) -> None:
    external = NodeConfig(
        NodeRole.SOLAR, 0, "PV", None, None, None, energy_from_statistic="pvoutput:yield"
    )
    bridge = _bridge(hass, [external])
    stats, instance, meta, clock = _patch_recorder({}, {})
    with stats, instance as during_period, meta, clock:
        await bridge.async_rebuild_energy_history()
        await bridge.async_rebuild_power_history()  # no power source: no read at all
    assert during_period.call_count == 1
    assert during_period.call_args.args[3] == {"pvoutput:yield"}
    blob = unpack_history(bridge.accessory.set_history.call_args.kwargs["energy_blob"])
    assert len(blob["series"]) == 1
    assert bridge.energy_history[0].nan == 720


async def test_history_rebuild_failure_keeps_previous_blob(hass: HomeAssistant) -> None:
    bridge = _bridge(hass, [GRID])
    with (
        patch(f"{BRIDGE_MODULE}.get_instance", return_value=_Recorder()),
        patch(f"{BRIDGE_MODULE}.statistics_during_period", side_effect=RuntimeError("db")),
    ):
        await bridge.async_rebuild_energy_history()
    bridge.accessory.set_history.assert_not_called()
    assert bridge.energy_history == {}


# --- today (§14) -------------------------------------------------------------


def _today_recorder(
    changes: dict[str, float], references: dict[str, float], period_start: float
) -> tuple[Any, Any, Any, list[Any]]:
    ends: list[Any] = []

    def during_period(_hass, _start, end, statistic_id, types, units) -> dict[str, float]:  # noqa: ANN001
        assert types == {"change"}
        assert units == {"energy": "kWh"}
        ends.append(end)
        return {"change": changes[statistic_id]}

    def recent(_hass, _start, _end, ids, period, units, types) -> dict[str, list]:  # noqa: ANN001
        assert (period, types) == ("5minute", {"state"})
        (sid,) = ids
        return {sid: [{"start": period_start, "state": references[sid]}]}

    return (
        patch(f"{BRIDGE_MODULE}.get_instance", return_value=_Recorder()),
        patch(f"{BRIDGE_MODULE}.statistic_during_period", side_effect=during_period),
        patch(f"{BRIDGE_MODULE}.statistics_during_period", side_effect=recent),
        ends,
    )


async def test_today_rebuild_pins_statistics_and_follows_live_meters(hass: HomeAssistant) -> None:
    hass.config.components.add("recorder")
    hass.states.async_set("sensor.imp", "104.2", {"unit_of_measurement": "kWh"})
    hass.states.async_set("sensor.exp", "7.0", {"unit_of_measurement": "kWh"})
    bridge = _bridge(hass, [GRID])
    period_start = dt_util.utcnow().timestamp() - 600
    instance, during, recent, ends = _today_recorder(
        {"sensor.imp": 4.0, "sensor.exp": 1.5},
        {"sensor.imp": 104.0, "sensor.exp": 7.0},
        period_start,
    )
    with instance, during, recent:
        await bridge.async_rebuild_today()

    # The daily change is cut at the very end of the period whose reading is the reference.
    assert ends == [dt_util.utc_from_timestamp(period_start + 300)] * 2
    pushed = {k: v for call in _calls(bridge) for k, v in call.items() if k.endswith("_today")}
    assert pushed["energy_from_today"] == pytest.approx(4.2)
    assert pushed["energy_to_today"] == pytest.approx(1.5)

    hass.states.async_set("sensor.imp", "105.0", {"unit_of_measurement": "kWh"})
    bridge.accessory.update_node.reset_mock()
    bridge._collect_node(0)
    bridge._flush(0)
    (call,) = _calls(bridge)
    assert call["energy_from_today"] == pytest.approx(5.0)


async def test_today_resumes_when_the_live_meter_returns(hass: HomeAssistant) -> None:
    hass.config.components.add("recorder")
    hass.states.async_set("sensor.imp", "unavailable")
    bridge = _bridge(hass, [GRID])
    instance, during, recent, _ = _today_recorder(
        {"sensor.imp": 4.0, "sensor.exp": 0.0},
        {"sensor.imp": 104.0, "sensor.exp": 0.0},
        dt_util.utcnow().timestamp() - 600,
    )
    with instance, during, recent:
        await bridge.async_rebuild_today()
    hass.states.async_set("sensor.imp", "104.5", {"unit_of_measurement": "kWh"})
    bridge.accessory.update_node.reset_mock()
    bridge._collect_node(0)
    bridge._flush(0)
    (call,) = _calls(bridge)
    assert call["energy_from_today"] == pytest.approx(4.5)


async def test_period_ending_before_midnight_retries_instead_of_guessing(
    hass: HomeAssistant,
) -> None:
    hass.config.components.add("recorder")
    hass.states.async_set("sensor.imp", "120.3", {"unit_of_measurement": "kWh"})
    bridge = _bridge(hass, [GRID])
    midnight = dt_util.start_of_local_day().timestamp()
    instance, during, recent, ends = _today_recorder(
        {"sensor.imp": 0.0, "sensor.exp": 0.0},
        {"sensor.imp": 119.0, "sensor.exp": 5.0},
        midnight - 900,
    )
    with instance, during, recent:
        await bridge.async_rebuild_today()
    assert ends == []
    assert not bridge.accessory.update_node.called
    assert bridge._today_retry is not None
    await bridge.async_stop()
    assert bridge._today_retry is None


async def test_period_ending_at_midnight_starts_today_at_its_reading(hass: HomeAssistant) -> None:
    hass.config.components.add("recorder")
    hass.states.async_set("sensor.imp", "120.3", {"unit_of_measurement": "kWh"})
    bridge = _bridge(hass, [GRID])
    midnight = dt_util.start_of_local_day().timestamp()
    instance, during, recent, ends = _today_recorder(
        {"sensor.imp": 99.0, "sensor.exp": 99.0},
        {"sensor.imp": 120.0, "sensor.exp": 5.0},
        midnight - 300,
    )
    with instance, during, recent:
        await bridge.async_rebuild_today()
    assert ends == []  # no change query: nothing of today is compiled yet
    pushed = {k: v for call in _calls(bridge) for k, v in call.items() if k.endswith("_today")}
    assert pushed["energy_from_today"] == pytest.approx(0.3)


async def test_rebase_replaces_a_pending_value_from_the_old_baseline(hass: HomeAssistant) -> None:
    hass.config.components.add("recorder")
    hass.states.async_set("sensor.imp", "104.0", {"unit_of_measurement": "kWh"})
    bridge = _bridge(hass, [GRID])
    bridge._runtime[0].pending["energy_from_today"] = 55.0  # yesterday's total, not yet flushed
    instance, during, recent, _ = _today_recorder(
        {"sensor.imp": 4.0, "sensor.exp": 0.0},
        {"sensor.imp": 104.0, "sensor.exp": 0.0},
        dt_util.utcnow().timestamp() - 600,
    )
    with instance, during, recent:
        await bridge.async_rebuild_today()
    assert bridge._runtime[0].pending["energy_from_today"] == pytest.approx(4.0)


async def test_live_sensor_without_recent_statistics_keeps_its_baseline(
    hass: HomeAssistant,
) -> None:
    hass.config.components.add("recorder")
    hass.states.async_set("sensor.imp", "104.0", {"unit_of_measurement": "kWh"})
    bridge = _bridge(hass, [GRID])
    bridge._runtime[0].today_from.rebase(4.0, 104.0, 104.0)

    def during_period(_hass, _start, _end, _sid, _types, _units) -> dict[str, float]:  # noqa: ANN001
        return {"change": 9.0}

    with (
        patch(f"{BRIDGE_MODULE}.get_instance", return_value=_Recorder()),
        patch(f"{BRIDGE_MODULE}.statistic_during_period", side_effect=during_period),
        patch(f"{BRIDGE_MODULE}.statistics_during_period", return_value={}),
    ):
        await bridge.async_rebuild_today()
    assert bridge._runtime[0].today_from.update(105.0) == pytest.approx(5.0)
    assert bridge._today_retry is not None
    await bridge.async_stop()


async def test_today_rebuild_failure_keeps_running_value(hass: HomeAssistant) -> None:
    hass.config.components.add("recorder")
    bridge = _bridge(hass, [GRID])
    with (
        patch(f"{BRIDGE_MODULE}.get_instance", return_value=_Recorder()),
        patch(f"{BRIDGE_MODULE}.statistics_during_period", side_effect=RuntimeError("db")),
    ):
        await bridge.async_rebuild_today()
    assert not bridge.accessory.update_node.called
