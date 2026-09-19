"""Config and options flow: import the Energy dashboard, then ask for power sources."""

from __future__ import annotations

import logging
from dataclasses import replace
from typing import Any

import voluptuous as vol
from homeassistant.components.energy.data import async_get_manager
from homeassistant.components.sensor import SensorDeviceClass
from homeassistant.config_entries import ConfigEntry, ConfigFlow, ConfigFlowResult, OptionsFlow
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.selector import (
    BooleanSelector,
    EntitySelector,
    EntitySelectorConfig,
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
    TextSelector,
)

from .const import (
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
    DEFAULT_PORT,
    DEFAULT_UPDATE_INTERVAL,
    DOMAIN,
    MAX_UPDATE_INTERVAL,
    MIN_UPDATE_INTERVAL,
    NodeRole,
)
from .energy_import import NodeConfig, build_nodes_from_prefs, merge_user_settings, reindex

_LOGGER = logging.getLogger(__name__)

_ROLE_LABELS = {
    NodeRole.GRID: "Grid",
    NodeRole.SOLAR: "Solar",
    NodeRole.BATTERY: "Battery",
    NodeRole.HOME: "Home",
    NodeRole.DEVICE: "Device",
    NodeRole.GAS: "Gas",
    NodeRole.WATER: "Water",
}

POWER_SELECTOR = EntitySelector(
    EntitySelectorConfig(domain="sensor", device_class=SensorDeviceClass.POWER)
)
ENERGY_SELECTOR = EntitySelector(
    EntitySelectorConfig(domain="sensor", device_class=SensorDeviceClass.ENERGY)
)
SOC_SELECTOR = EntitySelector(
    EntitySelectorConfig(domain="sensor", device_class=SensorDeviceClass.BATTERY)
)


def friendly_names(hass: HomeAssistant) -> dict[str, str]:
    return {state.entity_id: state.name for state in hass.states.async_all("sensor")}


def suggest_power_entity(hass: HomeAssistant, energy_entity: str | None) -> str | None:
    """First power sensor on the same device as the kWh sensor, if any."""
    if not energy_entity:
        return None
    registry = er.async_get(hass)
    entry = registry.async_get(energy_entity)
    if entry is None or entry.device_id is None:
        return None
    for candidate in er.async_entries_for_device(registry, entry.device_id):
        if candidate.domain != "sensor" or candidate.disabled:
            continue
        device_class = candidate.device_class or candidate.original_device_class
        if device_class == SensorDeviceClass.POWER:
            return candidate.entity_id
    return None


def _describe_nodes(nodes: list[NodeConfig]) -> str:
    if not nodes:
        return "-"
    lines = []
    for node in nodes:
        source = node.energy_from_entity or (
            ", ".join(node.unreadable_statistics) if node.unreadable_statistics else "-"
        )
        lines.append(f"- {node.name} ({_ROLE_LABELS[node.role]}): {source}")
    return "\n".join(lines)


async def async_import_nodes(hass: HomeAssistant) -> list[NodeConfig] | None:
    """Nodes from the Energy dashboard, or None if the dashboard is not configured."""
    manager = await async_get_manager(hass)
    if manager.data is None:
        return None
    return build_nodes_from_prefs(manager.data, friendly_names(hass))


class _NodeSteps:
    """Shared step logic for config and options flow."""

    hass: HomeAssistant
    _nodes: list[NodeConfig]
    _position: int
    _port: int
    _interval: int
    _home: NodeConfig | None

    def _init_state(self, nodes: list[NodeConfig], port: int, interval: int) -> None:
        self._home = next((node for node in nodes if node.role is NodeRole.HOME), None)
        self._nodes = [node for node in nodes if node.role is not NodeRole.HOME]
        self._position = 0
        self._port = port
        self._interval = interval

    def _user_schema(self, *, port: int, interval: int) -> vol.Schema:
        return vol.Schema(
            {
                vol.Required(CONF_CONFIRM, default=True): BooleanSelector(),
                vol.Required(CONF_PORT, default=port): NumberSelector(
                    NumberSelectorConfig(min=1024, max=65535, step=1, mode=NumberSelectorMode.BOX)
                ),
                vol.Required(CONF_UPDATE_INTERVAL, default=interval): NumberSelector(
                    NumberSelectorConfig(
                        min=MIN_UPDATE_INTERVAL,
                        max=MAX_UPDATE_INTERVAL,
                        step=1,
                        mode=NumberSelectorMode.BOX,
                        unit_of_measurement="s",
                    )
                ),
            }
        )

    def _node_schema(self, node: NodeConfig) -> vol.Schema:
        suggested_power = node.power_entity or suggest_power_entity(
            self.hass, node.energy_from_entity or node.energy_to_entity
        )
        schema: dict[Any, Any] = {
            vol.Optional(CONF_NAME, description={"suggested_value": node.name}): TextSelector(),
            vol.Optional(
                CONF_POWER, description={"suggested_value": suggested_power}
            ): POWER_SELECTOR,
            vol.Required(CONF_INVERTED, default=node.inverted): BooleanSelector(),
        }
        if node.role is NodeRole.BATTERY:
            schema[vol.Optional(CONF_SOC, description={"suggested_value": node.soc_entity})] = (
                SOC_SELECTOR
            )
            schema[vol.Optional(CONF_CAPACITY, description={"suggested_value": node.capacity})] = (
                NumberSelector(
                    NumberSelectorConfig(
                        min=0,
                        max=100_000,
                        step=0.01,
                        mode=NumberSelectorMode.BOX,
                        unit_of_measurement="kWh",
                    )
                )
            )
        return vol.Schema(schema)

    def _home_schema(self) -> vol.Schema:
        home = self._home
        return vol.Schema(
            {
                vol.Optional(
                    CONF_HOME_POWER,
                    description={"suggested_value": home.power_entity if home else None},
                ): POWER_SELECTOR,
                vol.Optional(
                    CONF_HOME_ENERGY,
                    description={"suggested_value": home.energy_from_entity if home else None},
                ): ENERGY_SELECTOR,
                vol.Required(
                    CONF_INVERTED, default=home.inverted if home else False
                ): BooleanSelector(),
            }
        )

    def _apply_node_input(self, user_input: dict[str, Any]) -> None:
        node = self._nodes[self._position]
        updated = replace(
            node,
            power_entity=user_input.get(CONF_POWER) or None,
            inverted=bool(user_input.get(CONF_INVERTED, False)),
        )
        typed_name = str(user_input.get(CONF_NAME) or "").strip()
        if typed_name and typed_name != node.name:
            updated = replace(updated, name=typed_name, name_is_default=False)
        if node.role is NodeRole.BATTERY:
            capacity = user_input.get(CONF_CAPACITY)
            updated = replace(
                updated,
                soc_entity=user_input.get(CONF_SOC) or None,
                capacity=float(capacity) if capacity not in (None, "") else None,
            )
        self._nodes[self._position] = updated
        self._position += 1

    def _apply_home_input(self, user_input: dict[str, Any]) -> None:
        power = user_input.get(CONF_HOME_POWER) or None
        energy = user_input.get(CONF_HOME_ENERGY) or None
        if not power and not energy:
            self._home = None
            return
        name = self._home.name if self._home else "Home"
        self._home = NodeConfig(
            role=NodeRole.HOME,
            index=0,
            name=name,
            energy_from_entity=energy,
            power_entity=power,
            inverted=bool(user_input.get(CONF_INVERTED, False)),
        )

    def _final_nodes(self) -> list[NodeConfig]:
        nodes = list(self._nodes)
        if self._home is not None:
            # Home sits between Battery and Device in the role order.
            position = next(
                (
                    i
                    for i, node in enumerate(nodes)
                    if node.role in (NodeRole.DEVICE, NodeRole.GAS, NodeRole.WATER)
                ),
                len(nodes),
            )
            nodes.insert(position, self._home)
        return reindex(nodes)

    def _entry_data(self) -> dict[str, Any]:
        return {
            CONF_PORT: self._port,
            CONF_UPDATE_INTERVAL: self._interval,
            CONF_NODES: [node.to_dict() for node in self._final_nodes()],
        }

    def _node_placeholders(self) -> dict[str, str]:
        node = self._nodes[self._position]
        return {
            "node_name": node.name,
            "role": _ROLE_LABELS[node.role],
            "energy_entity": node.energy_from_entity or node.energy_to_entity or "-",
            "position": str(self._position + 1),
            "count": str(len(self._nodes)),
        }


class ControllerEnergyBridgeConfigFlow(_NodeSteps, ConfigFlow, domain=DOMAIN):
    """Initial setup."""

    VERSION = 1

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if self._async_current_entries():
            return self.async_abort(reason="single_instance_allowed")
        nodes = await async_import_nodes(self.hass)
        if nodes is None:
            return self.async_abort(reason="no_energy_dashboard")

        if user_input is not None and user_input.get(CONF_CONFIRM):
            self._init_state(
                nodes, int(user_input[CONF_PORT]), int(user_input[CONF_UPDATE_INTERVAL])
            )
            return await self.async_step_node()

        return self.async_show_form(
            step_id="user",
            data_schema=self._user_schema(port=DEFAULT_PORT, interval=DEFAULT_UPDATE_INTERVAL),
            description_placeholders={"nodes": _describe_nodes(nodes), "count": str(len(nodes))},
            errors={"base": "confirm_required"} if user_input is not None else None,
        )

    async def async_step_node(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if user_input is not None:
            self._apply_node_input(user_input)
        if self._position >= len(self._nodes):
            return await self.async_step_home()
        return self.async_show_form(
            step_id="node",
            data_schema=self._node_schema(self._nodes[self._position]),
            description_placeholders=self._node_placeholders(),
        )

    async def async_step_home(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if user_input is not None:
            self._apply_home_input(user_input)
            return self.async_create_entry(
                title="Controller Energy Bridge", data=self._entry_data()
            )
        return self.async_show_form(step_id="home", data_schema=self._home_schema())

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> ControllerEnergyBridgeOptionsFlow:
        return ControllerEnergyBridgeOptionsFlow()


class ControllerEnergyBridgeOptionsFlow(_NodeSteps, OptionsFlow):
    """Re-run the import and power source steps; the entry reloads afterwards."""

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        fresh = await async_import_nodes(self.hass)
        if fresh is None:
            return self.async_abort(reason="no_energy_dashboard")
        entry = self.config_entry
        previous = [NodeConfig.from_dict(item) for item in entry.data.get(CONF_NODES, [])]
        nodes = merge_user_settings(fresh, previous)

        if user_input is not None and user_input.get(CONF_CONFIRM):
            self._init_state(
                nodes, int(user_input[CONF_PORT]), int(user_input[CONF_UPDATE_INTERVAL])
            )
            return await self.async_step_node()

        return self.async_show_form(
            step_id="init",
            data_schema=self._user_schema(
                port=entry.data.get(CONF_PORT, DEFAULT_PORT),
                interval=entry.data.get(CONF_UPDATE_INTERVAL, DEFAULT_UPDATE_INTERVAL),
            ),
            description_placeholders={"nodes": _describe_nodes(nodes), "count": str(len(nodes))},
            errors={"base": "confirm_required"} if user_input is not None else None,
        )

    async def async_step_node(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if user_input is not None:
            self._apply_node_input(user_input)
        if self._position >= len(self._nodes):
            return await self.async_step_home()
        return self.async_show_form(
            step_id="node",
            data_schema=self._node_schema(self._nodes[self._position]),
            description_placeholders=self._node_placeholders(),
        )

    async def async_step_home(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if user_input is not None:
            self._apply_home_input(user_input)
            self.hass.config_entries.async_update_entry(
                self.config_entry, data={**self.config_entry.data, **self._entry_data()}
            )
            return self.async_create_entry(data={})
        return self.async_show_form(step_id="home", data_schema=self._home_schema())
