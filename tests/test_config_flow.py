"""Config and options flow."""

from __future__ import annotations

from typing import Any
from unittest.mock import patch

import pytest
from homeassistant import config_entries
from homeassistant.components.energy.data import async_get_manager
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.controller_energy_bridge.const import (
    CONF_CAPACITY,
    CONF_CONFIRM,
    CONF_HOME_ENERGY,
    CONF_HOME_POWER,
    CONF_INVERTED,
    CONF_NAME,
    CONF_NODES,
    CONF_PORT,
    CONF_POWER,
    CONF_SOC,
    CONF_UPDATE_INTERVAL,
    DOMAIN,
    NodeRole,
)

from .conftest import REFERENCE_PREFS


async def _set_prefs(hass: HomeAssistant, prefs: dict[str, Any]) -> None:
    manager = await async_get_manager(hass)
    await manager.async_update(prefs)


@pytest.fixture
def no_bridge_setup() -> Any:
    with patch("custom_components.controller_energy_bridge.async_setup_entry", return_value=True):
        yield


async def test_abort_without_energy_dashboard(energy_ready: None, hass: HomeAssistant) -> None:
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "no_energy_dashboard"


async def test_happy_path(energy_ready: None, hass: HomeAssistant, no_bridge_setup: None) -> None:
    await _set_prefs(hass, REFERENCE_PREFS)
    hass.states.async_set("sensor.grid_import_energy", "500000", {"unit_of_measurement": "Wh"})

    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "user"
    assert "Netz" in result["description_placeholders"]["nodes"]
    assert result["description_placeholders"]["count"] == "4"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_CONFIRM: True, CONF_PORT: 21066, CONF_UPDATE_INTERVAL: 5}
    )
    # Node 1/4: grid
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "node"
    assert result["description_placeholders"]["node_name"] == "Netz"
    assert result["description_placeholders"]["position"] == "1"
    assert result["description_placeholders"]["count"] == "4"
    suggested = {
        str(key): key.description.get("suggested_value")
        for key in result["data_schema"].schema
        if getattr(key, "description", None)
    }
    assert suggested[CONF_NAME] == "Netz"
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {CONF_NAME: "Hausanschluss", CONF_POWER: "sensor.grid_power", CONF_INVERTED: False},
    )
    # Node 2/4: solar (stat_rate already set, form still shown for confirmation)
    assert result["step_id"] == "node"
    assert result["description_placeholders"]["node_name"] == "Solar"
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {CONF_POWER: "sensor.pv_power", CONF_INVERTED: False},
    )
    # Node 3/4: battery
    assert result["step_id"] == "node"
    assert result["description_placeholders"]["node_name"] == "Speicher"
    schema_keys = {str(key) for key in result["data_schema"].schema}
    assert {CONF_POWER, CONF_INVERTED, CONF_SOC, CONF_CAPACITY} <= schema_keys
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {
            CONF_POWER: "sensor.battery_power",
            CONF_INVERTED: True,
            CONF_SOC: "sensor.battery_soc",
            CONF_CAPACITY: 10.0,
        },
    )
    # Node 4/4: device without power
    assert result["step_id"] == "node"
    assert result["description_placeholders"]["node_name"] == "Well pump"
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_INVERTED: False}
    )
    # Home step
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "home"
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_INVERTED: False}
    )

    assert result["type"] is FlowResultType.CREATE_ENTRY
    data = result["data"]
    assert data[CONF_PORT] == 21066
    assert data[CONF_UPDATE_INTERVAL] == 5
    nodes = data[CONF_NODES]
    assert [(n["role"], n["index"], n["name"]) for n in nodes] == [
        (1, 0, "Hausanschluss"),
        (2, 1, "Solar"),
        (3, 2, "Speicher"),
        (5, 3, "Well pump"),
    ]
    assert nodes[0]["name_is_default"] is False
    assert nodes[1]["name_is_default"] is False  # dashboard name
    assert nodes[0]["power_entity"] == "sensor.grid_power"
    assert nodes[2]["inverted"] is True
    assert nodes[2]["soc_entity"] == "sensor.battery_soc"
    assert nodes[2]["capacity"] == 10.0
    assert nodes[3]["power_entity"] is None


async def test_home_node_is_inserted_before_devices(
    energy_ready: None, hass: HomeAssistant, no_bridge_setup: None
) -> None:
    await _set_prefs(hass, REFERENCE_PREFS)
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_CONFIRM: True, CONF_PORT: 21066, CONF_UPDATE_INTERVAL: 5}
    )
    for _ in range(4):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_INVERTED: False}
        )
    assert result["step_id"] == "home"
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {CONF_HOME_POWER: "sensor.home_p", CONF_HOME_ENERGY: "sensor.home_e", CONF_INVERTED: False},
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    roles = [(n["role"], n["index"]) for n in result["data"][CONF_NODES]]
    assert roles == [(1, 0), (2, 1), (3, 2), (4, 3), (5, 4)]
    home = result["data"][CONF_NODES][3]
    assert home["power_entity"] == "sensor.home_p"
    assert home["energy_from_entity"] == "sensor.home_e"


async def test_confirm_unchecked_shows_error(energy_ready: None, hass: HomeAssistant) -> None:
    await _set_prefs(hass, REFERENCE_PREFS)
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_CONFIRM: False, CONF_PORT: 21066, CONF_UPDATE_INTERVAL: 5}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "confirm_required"}


async def test_power_suggestion_from_same_device(
    energy_ready: None, hass: HomeAssistant, no_bridge_setup: None
) -> None:
    device_registry = dr.async_get(hass)
    entity_registry = er.async_get(hass)
    entry = MockConfigEntry(domain="test")
    entry.add_to_hass(hass)
    device = device_registry.async_get_or_create(
        config_entry_id=entry.entry_id, identifiers={("test", "meter")}
    )
    entity_registry.async_get_or_create(
        "sensor",
        "test",
        "energy",
        suggested_object_id="meter_energy",
        device_id=device.id,
        original_device_class="energy",
    )
    entity_registry.async_get_or_create(
        "sensor",
        "test",
        "power",
        suggested_object_id="meter_power",
        device_id=device.id,
        original_device_class="power",
    )
    await _set_prefs(
        hass,
        {
            "energy_sources": [{"type": "solar", "stat_energy_from": "sensor.meter_energy"}],
            "device_consumption": [],
        },
    )
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_CONFIRM: True, CONF_PORT: 21066, CONF_UPDATE_INTERVAL: 5}
    )
    assert result["step_id"] == "node"
    suggested = {
        str(key): key.description.get("suggested_value")
        for key in result["data_schema"].schema
        if getattr(key, "description", None)
    }
    assert suggested[CONF_POWER] == "sensor.meter_power"


async def test_single_instance(energy_ready: None, hass: HomeAssistant) -> None:
    MockConfigEntry(domain=DOMAIN, data={CONF_NODES: []}).add_to_hass(hass)
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "single_instance_allowed"


async def test_options_flow_reimports_and_keeps_user_power(
    energy_ready: None, hass: HomeAssistant, no_bridge_setup: None
) -> None:
    await _set_prefs(hass, REFERENCE_PREFS)
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={
            CONF_PORT: 21066,
            CONF_UPDATE_INTERVAL: 5,
            CONF_NODES: [
                {
                    "role": int(NodeRole.GRID),
                    "index": 0,
                    "energy_from_entity": "sensor.grid_import_energy",
                    "energy_to_entity": "sensor.grid_export_energy",
                    "power_entity": "sensor.grid_power",
                    "inverted": True,
                    "name": "Hausanschluss",
                    "name_is_default": False,
                }
            ],
        },
    )
    entry.add_to_hass(hass)
    with patch("custom_components.controller_energy_bridge.async_unload_entry", return_value=True):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

        result = await hass.config_entries.options.async_init(entry.entry_id)
        assert result["type"] is FlowResultType.FORM
        assert result["step_id"] == "init"
        result = await hass.config_entries.options.async_configure(
            result["flow_id"], {CONF_CONFIRM: True, CONF_PORT: 21067, CONF_UPDATE_INTERVAL: 10}
        )
        assert result["step_id"] == "node"
        suggested = {
            str(key): key.description.get("suggested_value")
            for key in result["data_schema"].schema
            if getattr(key, "description", None)
        }
        assert suggested[CONF_POWER] == "sensor.grid_power"
        # Dashboard name "Netz" wins over the stored user name.
        assert suggested[CONF_NAME] == "Netz"
        defaults = {
            str(key): key.default()
            for key in result["data_schema"].schema
            if key.default is not None and str(key) == CONF_INVERTED
        }
        assert defaults[CONF_INVERTED] is True
        for _ in range(4):
            result = await hass.config_entries.options.async_configure(
                result["flow_id"], {CONF_POWER: "sensor.any_power", CONF_INVERTED: False}
            )
        assert result["step_id"] == "home"
        result = await hass.config_entries.options.async_configure(
            result["flow_id"], {CONF_INVERTED: False}
        )
        assert result["type"] is FlowResultType.CREATE_ENTRY
        await hass.async_block_till_done()

    assert entry.data[CONF_PORT] == 21067
    assert entry.data[CONF_UPDATE_INTERVAL] == 10
    assert len(entry.data[CONF_NODES]) == 4
    assert all(n["power_entity"] == "sensor.any_power" for n in entry.data[CONF_NODES])
