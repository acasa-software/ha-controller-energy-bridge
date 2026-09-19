"""Config entry setup, seeding, energy-dashboard follow-up and unload."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import timedelta
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from homeassistant.components import persistent_notification
from homeassistant.components.energy.data import async_get_manager
from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import EVENT_HOMEASSISTANT_STARTED
from homeassistant.core import CoreState, HomeAssistant
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_time_changed,
)

from custom_components.controller_energy_bridge import nodes_signature
from custom_components.controller_energy_bridge.const import (
    CONF_NODES,
    CONF_PORT,
    CONF_PUBLISHED_SIGNATURE,
    CONF_UPDATE_INTERVAL,
    DOMAIN,
    NOTIFICATION_ID,
    NodeRole,
)
from custom_components.controller_energy_bridge.diagnostics import (
    async_get_config_entry_diagnostics,
)
from custom_components.controller_energy_bridge.energy_import import NodeConfig

from .conftest import REFERENCE_PREFS

NODES = [
    NodeConfig(
        NodeRole.GRID,
        0,
        "Netz",
        "sensor.grid_import_energy",
        "sensor.grid_export_energy",
        "sensor.grid_power",
    ),
    NodeConfig(
        NodeRole.SOLAR,
        1,
        "Solar",
        "sensor.pv_yield_total",
        None,
        "sensor.pv_power",
    ),
    NodeConfig(
        NodeRole.BATTERY,
        2,
        "Speicher",
        "sensor.battery_discharge_energy",
        "sensor.battery_charge_energy",
        "sensor.battery_power",
        "sensor.battery_soc",
        10.0,
    ),
    NodeConfig(NodeRole.DEVICE, 3, "Well pump", "sensor.well_pump_energy"),
]


@pytest.fixture
def hap_offline() -> Iterator[dict[str, Any]]:
    """Keep pyhap off the network and the disk."""
    driver_path = "custom_components.controller_energy_bridge.bridge.SharedZeroconfDriver"
    with (
        patch(f"{driver_path}.async_start", new_callable=AsyncMock) as start,
        patch(f"{driver_path}.async_stop", new_callable=AsyncMock) as stop,
        patch(f"{driver_path}.persist"),
        patch(f"{driver_path}.update_advertisement"),
        patch(
            "custom_components.controller_energy_bridge.bridge.network.async_get_announce_addresses",
            new_callable=AsyncMock,
            return_value=["127.0.0.1"],
        ),
    ):
        yield {"start": start, "stop": stop}


def _entry(nodes: list[NodeConfig] = NODES, **extra: Any) -> MockConfigEntry:
    return MockConfigEntry(
        domain=DOMAIN,
        data={
            CONF_PORT: 21066,
            CONF_UPDATE_INTERVAL: 5,
            CONF_NODES: [node.to_dict() for node in nodes],
            **extra,
        },
    )


def _seed_states(hass: HomeAssistant) -> None:
    hass.states.async_set("sensor.grid_power", "-1500", {"unit_of_measurement": "W"})
    hass.states.async_set("sensor.grid_import_energy", "500000", {"unit_of_measurement": "Wh"})
    hass.states.async_set("sensor.grid_export_energy", "1200000", {"unit_of_measurement": "Wh"})
    hass.states.async_set("sensor.pv_power", "2.5", {"unit_of_measurement": "kW"})
    hass.states.async_set("sensor.pv_yield_total", "15000", {"unit_of_measurement": "kWh"})
    hass.states.async_set("sensor.battery_power", "-400", {"unit_of_measurement": "W"})
    hass.states.async_set("sensor.battery_soc", "55", {"unit_of_measurement": "%"})


async def test_setup_seeds_values_and_creates_pairing_notification(
    energy_ready: None, hass: HomeAssistant, hap_offline: dict[str, Any]
) -> None:
    _seed_states(hass)
    entry = _entry()
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.LOADED
    # async_at_started fires immediately when HA is already running (test hass is).
    hap_offline["start"].assert_awaited_once()

    bridge = entry.runtime_data
    accessory = bridge.accessory
    assert accessory is not None
    assert accessory.node_indices() == [0, 1, 2, 3]
    grid = accessory.nodes[0]
    assert grid.power.get_value() == -1500.0
    assert grid.energy_from.get_value() == pytest.approx(500.0)
    assert grid.energy_to.get_value() == pytest.approx(1200.0)
    assert grid.status_fault.get_value() == 0
    solar = accessory.nodes[1]
    assert solar.power.get_value() == 2500.0
    battery = accessory.nodes[2]
    assert battery.power.get_value() == -400.0
    assert battery.soc.get_value() == pytest.approx(55.0)
    well_pump = accessory.nodes[3]
    assert well_pump.status_fault.get_value() == 1  # no power source

    # First start publishes the layout and stores its signature.
    assert entry.data[CONF_PUBLISHED_SIGNATURE] == nodes_signature(NODES)
    assert bridge.driver.state.config_version == 2  # fresh pyhap state starts at 1

    notifications = persistent_notification._async_get_or_create_notifications(hass)
    assert NOTIFICATION_ID in notifications
    assert bridge.pincode in notifications[NOTIFICATION_ID]["message"]

    diagnostics = await async_get_config_entry_diagnostics(hass, entry)
    assert diagnostics["bridge"]["paired"] is False
    assert diagnostics["entry"]["node_count"] == 4
    assert diagnostics["nodes"][0]["last_power"] == -1500.0
    # History rebuilt at start against the (empty) recorder: all NaN, full bucket count.
    assert diagnostics["bridge"]["history_available"] is True
    assert diagnostics["nodes"][0]["energy_history"]["buckets"] == 720
    assert diagnostics["nodes"][0]["energy_history"]["nan"] == 1440
    assert diagnostics["nodes"][0]["energy_history"]["size"] == 5772
    assert diagnostics["nodes"][0]["energy_history"]["rebuilt_at"] is not None
    assert diagnostics["nodes"][0]["power_history"]["size"] == 396
    assert diagnostics["nodes"][3]["power_history"] is None  # no power source
    assert grid.energy_history.get_value() != ""
    assert "pincode" not in str(diagnostics)

    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.NOT_LOADED
    hap_offline["stop"].assert_awaited_once()
    assert NOTIFICATION_ID not in persistent_notification._async_get_or_create_notifications(hass)


async def test_unchanged_signature_does_not_bump_config(
    energy_ready: None, hass: HomeAssistant, hap_offline: dict[str, Any]
) -> None:
    entry = _entry(**{CONF_PUBLISHED_SIGNATURE: nodes_signature(NODES)})
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.runtime_data.driver.state.config_version == 1


async def test_config_version_bumps_before_server_start(
    energy_ready: None, hass: HomeAssistant, hap_offline: dict[str, Any]
) -> None:
    """The new c# must be in the first advertisement; a post-start update task would
    re-add the mDNS name after a reload's unregister (ServiceNameAlreadyRegistered)."""
    entry = _entry()
    entry.add_to_hass(hass)
    seen: list[int] = []
    hap_offline["start"].side_effect = lambda: seen.append(
        entry.runtime_data.driver.state.config_version
    )
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert seen == [2]
    entry.runtime_data.driver.update_advertisement.assert_not_called()


async def test_legacy_entry_without_newer_node_keys_does_not_reload(
    energy_ready: None, hass: HomeAssistant, hap_offline: dict[str, Any]
) -> None:
    """Entries written before the history fields existed must not trigger a reload
    from the signature-only write on start."""
    legacy_keys = {
        "energy_from_statistic",
        "energy_to_statistic",
        "name_is_default",
        "unreadable_statistics",
    }
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={
            CONF_PORT: 21066,
            CONF_UPDATE_INTERVAL: 5,
            CONF_NODES: [
                {k: v for k, v in node.to_dict().items() if k not in legacy_keys} for node in NODES
            ],
        },
    )
    entry.add_to_hass(hass)
    with patch.object(
        hass.config_entries, "async_reload", wraps=hass.config_entries.async_reload
    ) as reload:
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
    reload.assert_not_called()
    assert entry.state is ConfigEntryState.LOADED
    hap_offline["start"].assert_awaited_once()
    assert CONF_PUBLISHED_SIGNATURE in entry.data


async def test_state_change_updates_accessory(
    energy_ready: None, hass: HomeAssistant, hap_offline: dict[str, Any]
) -> None:
    _seed_states(hass)
    entry = _entry()
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    grid = entry.runtime_data.accessory.nodes[0]
    with patch(
        "custom_components.controller_energy_bridge.bridge.time.monotonic", return_value=99999.0
    ):
        hass.states.async_set("sensor.grid_power", "unavailable")
        await hass.async_block_till_done()
    assert grid.status_fault.get_value() == 1
    assert grid.power.get_value() == -1500.0  # last known value stays


async def test_energy_dashboard_change_rebuilds_nodes(
    energy_ready: None, hass: HomeAssistant, hap_offline: dict[str, Any]
) -> None:
    manager = await async_get_manager(hass)
    await manager.async_update(REFERENCE_PREFS)
    entry = _entry()
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert len(entry.runtime_data.nodes) == 4

    prefs = {
        **REFERENCE_PREFS,
        "device_consumption": [
            *REFERENCE_PREFS["device_consumption"],
            {"stat_consumption": "sensor.waschmaschine_energie", "name": "Waschmaschine"},
        ],
    }
    await manager.async_update(prefs)
    await hass.async_block_till_done()

    nodes = entry.data[CONF_NODES]
    assert [n["name"] for n in nodes] == ["Netz", "Solar", "Speicher", "Well pump", "Waschmaschine"]
    # User-selected power sources survive the re-import.
    assert nodes[0]["power_entity"] == "sensor.grid_power"
    assert nodes[2]["soc_entity"] == "sensor.battery_soc"
    # The entry reloaded with the new layout.
    assert entry.state is ConfigEntryState.LOADED
    assert len(entry.runtime_data.nodes) == 5
    assert entry.runtime_data.accessory.node_indices() == [0, 1, 2, 3, 4]


async def test_energy_listener_is_noop_after_unload(
    energy_ready: None, hass: HomeAssistant, hap_offline: dict[str, Any]
) -> None:
    manager = await async_get_manager(hass)
    await manager.async_update(REFERENCE_PREFS)
    entry = _entry()
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()
    before = dict(entry.data)
    await manager.async_update({**REFERENCE_PREFS, "device_consumption": []})
    await hass.async_block_till_done()
    assert dict(entry.data) == before
    assert entry.state is ConfigEntryState.NOT_LOADED


async def test_start_is_deferred_until_hass_started(
    energy_ready: None, hass: HomeAssistant, hap_offline: dict[str, Any]
) -> None:
    hass.set_state(CoreState.starting)
    entry = _entry()
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    hap_offline["start"].assert_not_awaited()
    hass.set_state(CoreState.running)
    hass.bus.async_fire(EVENT_HOMEASSISTANT_STARTED)
    await hass.async_block_till_done()
    hap_offline["start"].assert_awaited_once()


async def test_history_rebuilds_on_schedule(
    energy_ready: None, hass: HomeAssistant, hap_offline: dict[str, Any]
) -> None:
    entry = _entry()
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    bridge = entry.runtime_data
    first_energy = bridge.energy_history[0].rebuilt_at
    first_power = bridge.power_history[0].rebuilt_at

    with patch(
        "custom_components.controller_energy_bridge.bridge.statistics_during_period",
        return_value={},
    ) as during_period:
        now = dt_util.utcnow()
        # Energy: next :15 after now (local time pattern, second 0).
        next_quarter = (now + timedelta(hours=1)).replace(minute=15, second=0, microsecond=0)
        async_fire_time_changed(hass, next_quarter)
        await hass.async_block_till_done()
        periods = [call.args[4] for call in during_period.call_args_list]
        assert "hour" in periods
        assert bridge.energy_history[0].rebuilt_at != first_energy

        # Power: 15-minute interval from setup.
        async_fire_time_changed(hass, now + timedelta(minutes=15, seconds=1))
        await hass.async_block_till_done()
        periods = [call.args[4] for call in during_period.call_args_list]
        assert "5minute" in periods
        assert bridge.power_history[0].rebuilt_at != first_power

    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()
    with patch(
        "custom_components.controller_energy_bridge.bridge.statistics_during_period",
        return_value={},
    ) as during_period:
        async_fire_time_changed(hass, dt_util.utcnow() + timedelta(hours=2))
        await hass.async_block_till_done()
        during_period.assert_not_called()
