"""Diagnostics: node configuration and pairing status, no secrets."""

from __future__ import annotations

from typing import Any

from homeassistant.core import HomeAssistant

from . import EnergyBridgeConfigEntry
from .bridge import HistoryStatus
from .const import CONF_NODES, CONF_PORT, CONF_PUBLISHED_SIGNATURE, CONF_UPDATE_INTERVAL


def _history(status: HistoryStatus | None) -> dict[str, Any] | None:
    return status.as_dict() if status else None


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: EnergyBridgeConfigEntry
) -> dict[str, Any]:
    bridge = entry.runtime_data
    accessory = bridge.accessory
    nodes: list[dict[str, Any]] = []
    for node in bridge.nodes:
        chars = accessory.nodes.get(node.index) if accessory else None
        nodes.append(
            {
                **node.to_dict(),
                "iid": accessory.iid_manager.get_iid(chars.service)
                if accessory and chars
                else None,
                "last_power": chars.last_power if chars else None,
                "last_energy_from": chars.last_energy_from if chars else None,
                "last_energy_to": chars.last_energy_to if chars else None,
                "last_soc": chars.last_soc if chars else None,
                "status_fault": chars.status_fault.get_value() if chars else None,
                "energy_history": _history(bridge.energy_history.get(node.index)),
                "power_history": _history(bridge.power_history.get(node.index)),
            }
        )
    return {
        "entry": {
            CONF_PORT: entry.data.get(CONF_PORT),
            CONF_UPDATE_INTERVAL: entry.data.get(CONF_UPDATE_INTERVAL),
            CONF_PUBLISHED_SIGNATURE: entry.data.get(CONF_PUBLISHED_SIGNATURE),
            "node_count": len(entry.data.get(CONF_NODES, [])),
        },
        "bridge": {
            "started": bridge.driver is not None,
            "history_available": bridge.history_available,
            "paired": bridge.paired,
            "config_version": bridge.driver.state.config_version if bridge.driver else None,
            "port": bridge.port,
        },
        "nodes": nodes,
    }
