"""Shared fixtures."""

from __future__ import annotations

import asyncio
import os
import tempfile
from collections.abc import Iterator
from typing import Any

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.setup import async_setup_component
from pyhap.accessory_driver import AccessoryDriver
from pyhap.loader import get_loader

pytest_plugins = "pytest_homeassistant_custom_component"


@pytest.fixture
async def energy_ready(
    recorder_mock: Any,
    hass: HomeAssistant,
    enable_custom_integrations: None,
    mock_async_zeroconf: Any,
) -> None:
    """HA with recorder + energy set up and custom_components/ discoverable.

    `recorder_mock` must be resolved before `hass`, hence the parameter order.
    """
    assert await async_setup_component(hass, "energy", {})
    await hass.async_block_till_done()


@pytest.fixture
def hap_driver() -> Iterator[AccessoryDriver]:
    """A bare pyhap driver that never touches the network."""
    loop = asyncio.new_event_loop()
    with tempfile.TemporaryDirectory() as tmp:
        driver = AccessoryDriver(
            loop=loop,
            port=21066,
            persist_file=os.path.join(tmp, "bridge.state"),
            loader=get_loader(),
            address="127.0.0.1",
        )
        yield driver
    loop.close()


REFERENCE_PREFS = {
    "energy_sources": [
        {
            "type": "grid",
            "stat_energy_from": "sensor.grid_import_energy",
            "stat_energy_to": "sensor.grid_export_energy",
            "stat_cost": None,
            "stat_compensation": None,
            "entity_energy_price": None,
            "number_energy_price": None,
            "entity_energy_price_export": None,
            "number_energy_price_export": None,
            "cost_adjustment_day": 0,
            "name": "Netz",
        },
        {
            "type": "solar",
            "stat_energy_from": "sensor.pv_yield_total",
            "stat_rate": "sensor.pv_power",
            "config_entry_solar_forecast": None,
            "name": "Solar",
        },
        {
            "type": "battery",
            "stat_energy_from": "sensor.battery_discharge_energy",
            "stat_energy_to": "sensor.battery_charge_energy",
            "stat_soc": "sensor.battery_soc",
            "capacity": 10.0,
            "name": "Speicher",
        },
    ],
    "device_consumption": [
        {"stat_consumption": "sensor.well_pump_energy", "name": "Well pump"},
    ],
}
