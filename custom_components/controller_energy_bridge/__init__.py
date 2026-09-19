"""Controller Energy Bridge: expose the Energy dashboard as a HomeKit accessory."""

from __future__ import annotations

import hashlib
import json
import logging
from datetime import datetime, timedelta
from typing import Any

from homeassistant.components import persistent_notification
from homeassistant.components.energy.data import async_get_manager
from homeassistant.config_entries import ConfigEntry, ConfigEntryState
from homeassistant.core import CALLBACK_TYPE, HomeAssistant, callback
from homeassistant.helpers import instance_id
from homeassistant.helpers.event import async_track_time_interval
from homeassistant.helpers.start import async_at_started
from homeassistant.loader import async_get_integration

from .bridge import EnergyBridge
from .config_flow import friendly_names
from .const import (
    CONF_NODES,
    CONF_PORT,
    CONF_PUBLISHED_SIGNATURE,
    CONF_UPDATE_INTERVAL,
    DEFAULT_PORT,
    DEFAULT_UPDATE_INTERVAL,
    DOMAIN,
    NOTIFICATION_ID,
)
from .energy_import import NodeConfig, build_nodes_from_prefs, merge_user_settings

_LOGGER = logging.getLogger(__name__)

type EnergyBridgeConfigEntry = ConfigEntry[EnergyBridge]

_DATA_MANAGER_LISTENER = "manager_listener_registered"
_PAIRING_POLL = timedelta(seconds=30)


# Bumped whenever the bridge changes which characteristics a given node layout produces,
# so upgraded installs advertise a new HAP configuration number (§4). 2: history (§13).
_LAYOUT_REVISION = 2


def nodes_signature(nodes: list[NodeConfig]) -> str:
    """Stable fingerprint of the static node layout (§9: rename/reorder/remove)."""
    payload = [
        (
            int(node.role),
            node.index,
            node.name,
            node.energy_from_entity,
            node.energy_to_entity,
            node.power_entity,
            node.soc_entity,
            node.capacity,
            node.energy_from_statistic,
            node.energy_to_statistic,
        )
        for node in sorted(nodes, key=lambda node: node.index)
    ]
    return hashlib.sha1(json.dumps([_LAYOUT_REVISION, payload]).encode()).hexdigest()


def _nodes_from_entry(entry: ConfigEntry) -> list[NodeConfig]:
    return [NodeConfig.from_dict(item) for item in entry.data.get(CONF_NODES, [])]


async def async_setup_entry(hass: HomeAssistant, entry: EnergyBridgeConfigEntry) -> bool:
    nodes = _nodes_from_entry(entry)
    integration = await async_get_integration(hass, DOMAIN)
    serial = (await instance_id.async_get(hass)).replace("-", "")[:12]

    bridge = EnergyBridge(
        hass,
        nodes,
        port=int(entry.data.get(CONF_PORT, DEFAULT_PORT)),
        update_interval=int(entry.data.get(CONF_UPDATE_INTERVAL, DEFAULT_UPDATE_INTERVAL)),
        serial_number=serial,
        firmware_revision=str(integration.version or "0.0.0"),
    )
    await bridge.async_setup()
    entry.runtime_data = bridge

    async def _start(_hass: HomeAssistant) -> None:
        signature = nodes_signature(nodes)
        if entry.data.get(CONF_PUBLISHED_SIGNATURE) != signature:
            bridge.bump_config_version()
            hass.config_entries.async_update_entry(
                entry, data={**entry.data, CONF_PUBLISHED_SIGNATURE: signature}
            )
        await bridge.async_start()
        _update_pairing_notification(hass, bridge)
        if bridge.paired:
            return
        cancel_poll: CALLBACK_TYPE | None = None

        @callback
        def _poll_pairing(_now: datetime) -> None:
            _update_pairing_notification(hass, bridge)
            if bridge.paired and cancel_poll is not None:
                cancel_poll()

        cancel_poll = async_track_time_interval(hass, _poll_pairing, _PAIRING_POLL)
        entry.async_on_unload(cancel_poll)

    entry.async_on_unload(async_at_started(hass, _start))
    entry.async_on_unload(entry.add_update_listener(_async_entry_updated))
    await _async_register_energy_listener(hass)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: EnergyBridgeConfigEntry) -> bool:
    await entry.runtime_data.async_stop()
    persistent_notification.async_dismiss(hass, NOTIFICATION_ID)
    return True


async def _async_entry_updated(hass: HomeAssistant, entry: EnergyBridgeConfigEntry) -> None:
    """Reload when nodes/port/interval change; the signature-only write must not reload."""
    bridge: EnergyBridge | None = getattr(entry, "runtime_data", None)
    if bridge is None:
        return
    # Compare parsed nodes, not raw dicts: entries written by older versions lack newer keys.
    unchanged = (
        _nodes_from_entry(entry) == bridge.nodes
        and bridge.port == int(entry.data.get(CONF_PORT, DEFAULT_PORT))
        and bridge.update_interval
        == int(entry.data.get(CONF_UPDATE_INTERVAL, DEFAULT_UPDATE_INTERVAL))
    )
    if unchanged:
        return
    await hass.config_entries.async_reload(entry.entry_id)


@callback
def _update_pairing_notification(hass: HomeAssistant, bridge: EnergyBridge) -> None:
    if bridge.paired:
        persistent_notification.async_dismiss(hass, NOTIFICATION_ID)
        return
    pin = bridge.pincode or "?"
    uri = bridge.setup_uri or ""
    persistent_notification.async_create(
        hass,
        (
            "Controller Energy Bridge is not paired yet.\n\n"
            f"Open Controller for HomeKit, add an accessory and enter the code **{pin}**.\n\n"
            f"Setup URI: `{uri}`\n\n"
            'The Apple Home app shows this accessory as "Not Supported"; that is expected.'
        ),
        title="Controller Energy Bridge: pairing code",
        notification_id=NOTIFICATION_ID,
    )


async def _async_register_energy_listener(hass: HomeAssistant) -> None:
    """Follow Energy dashboard edits. The manager offers no unsubscribe, so register once."""
    domain_data: dict[str, Any] = hass.data.setdefault(DOMAIN, {})
    if domain_data.get(_DATA_MANAGER_LISTENER):
        return
    domain_data[_DATA_MANAGER_LISTENER] = True
    manager = await async_get_manager(hass)

    async def _on_energy_update() -> None:
        entries = [
            entry
            for entry in hass.config_entries.async_entries(DOMAIN)
            if entry.state is ConfigEntryState.LOADED
        ]
        if not entries or manager.data is None:
            return
        entry = entries[0]
        previous = _nodes_from_entry(entry)
        merged = merge_user_settings(
            build_nodes_from_prefs(manager.data, friendly_names(hass)), previous
        )
        new_nodes = [node.to_dict() for node in merged]
        if new_nodes == list(entry.data.get(CONF_NODES, [])):
            return
        _LOGGER.info("Energy dashboard changed, rebuilding %d nodes", len(new_nodes))
        hass.config_entries.async_update_entry(entry, data={**entry.data, CONF_NODES: new_nodes})

    manager.async_listen_updates(_on_energy_update)
