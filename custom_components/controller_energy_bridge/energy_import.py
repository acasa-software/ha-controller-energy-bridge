"""Pure mapping from Home Assistant Energy dashboard preferences to bridge nodes.

Implements protocol §11. No Home Assistant imports so the module stays trivially testable.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from dataclasses import asdict, dataclass, field, replace
from typing import Any

from .const import (
    CONF_CAPACITY,
    CONF_ENERGY_FROM,
    CONF_ENERGY_FROM_STATISTIC,
    CONF_ENERGY_TO,
    CONF_ENERGY_TO_STATISTIC,
    CONF_INDEX,
    CONF_INVERTED,
    CONF_NAME,
    CONF_NAME_IS_DEFAULT,
    CONF_POWER,
    CONF_ROLE,
    CONF_SOC,
    CONF_UNREADABLE,
    ENERGY_TO_ROLES,
    MAX_NODES,
    ROLE_ORDER,
    NodeRole,
)

# Same shape Home Assistant's valid_entity_id accepts.
_ENTITY_ID_RE = re.compile(r"^(?!.+__)(?!_)[\da-z_]+(?<!_)\.(?!_)[\da-z_]+(?<!_)$")

_SOURCE_TYPE_TO_ROLE = {
    "grid": NodeRole.GRID,
    "solar": NodeRole.SOLAR,
    "battery": NodeRole.BATTERY,
    "gas": NodeRole.GAS,
    "water": NodeRole.WATER,
}

_ROLE_DEFAULT_NAME = {
    NodeRole.GRID: "Grid",
    NodeRole.SOLAR: "Solar",
    NodeRole.BATTERY: "Battery",
    NodeRole.HOME: "Home",
    NodeRole.DEVICE: "Device",
    NodeRole.GAS: "Gas",
    NodeRole.WATER: "Water",
}


def is_entity_id(statistic_id: object) -> bool:
    """True if the statistic id is a live-readable entity id, not an external `domain:object`."""
    return isinstance(statistic_id, str) and bool(_ENTITY_ID_RE.match(statistic_id))


@dataclass(frozen=True, slots=True)
class NodeConfig:
    """One Energy Node service as configured in the bridge."""

    role: NodeRole
    index: int
    name: str
    energy_from_entity: str | None = None
    energy_to_entity: str | None = None
    power_entity: str | None = None
    soc_entity: str | None = None
    capacity: float | None = None
    inverted: bool = False
    # Statistic ids from the dashboard that cannot be read live (protocol §11).
    unreadable_statistics: tuple[str, ...] = field(default_factory=tuple)
    # External statistic ids per direction; readable from the recorder for history (§13.4).
    energy_from_statistic: str | None = None
    energy_to_statistic: str | None = None
    # True while `name` is a generated fallback, so a re-import may replace it.
    name_is_default: bool = False

    @property
    def source_entities(self) -> tuple[str, ...]:
        """Entity ids whose state changes drive this node."""
        return tuple(
            entity
            for entity in (
                self.power_entity,
                self.energy_from_entity,
                self.energy_to_entity,
                self.soc_entity,
            )
            if entity
        )

    @property
    def history_energy_from(self) -> str | None:
        """Statistic id feeding the Energy History From series (§13.4)."""
        return self.energy_from_entity or self.energy_from_statistic

    @property
    def history_energy_to(self) -> str | None:
        """Statistic id feeding the Energy History To series, only for Energy To roles."""
        if self.role not in ENERGY_TO_ROLES:
            return None
        return self.energy_to_entity or self.energy_to_statistic

    @property
    def identity(self) -> tuple[int, str]:
        """Key used to carry user settings across dashboard re-imports."""
        return (int(self.role), self.energy_from_entity or self.energy_to_entity or self.name)

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data[CONF_ROLE] = int(self.role)
        data[CONF_UNREADABLE] = list(self.unreadable_statistics)
        return data

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> NodeConfig:
        capacity = data.get(CONF_CAPACITY)
        return cls(
            role=NodeRole(int(data[CONF_ROLE])),
            index=int(data[CONF_INDEX]),
            name=str(data[CONF_NAME]),
            energy_from_entity=data.get(CONF_ENERGY_FROM) or None,
            energy_to_entity=data.get(CONF_ENERGY_TO) or None,
            power_entity=data.get(CONF_POWER) or None,
            soc_entity=data.get(CONF_SOC) or None,
            capacity=float(capacity) if capacity not in (None, "") else None,
            inverted=bool(data.get(CONF_INVERTED, False)),
            unreadable_statistics=tuple(data.get(CONF_UNREADABLE, ())),
            energy_from_statistic=data.get(CONF_ENERGY_FROM_STATISTIC) or None,
            energy_to_statistic=data.get(CONF_ENERGY_TO_STATISTIC) or None,
            name_is_default=bool(data.get(CONF_NAME_IS_DEFAULT, True)),
        )


def _split_readable(statistic_id: object) -> tuple[str | None, list[str]]:
    """Return (entity_id or None, [unreadable ids])."""
    if not statistic_id:
        return None, []
    if is_entity_id(statistic_id):
        return str(statistic_id), []
    return None, [str(statistic_id)]


def _first(ids: list[str]) -> str | None:
    return ids[0] if ids else None


def _power_from_source(source: Mapping[str, Any]) -> tuple[str | None, bool]:
    """Resolve the power entity and inversion from a source, honouring `power_config`."""
    rate = source.get("stat_rate")
    if is_entity_id(rate):
        return str(rate), False
    power_config = source.get("power_config") or {}
    if is_entity_id(power_config.get("stat_rate")):
        return str(power_config["stat_rate"]), False
    if is_entity_id(power_config.get("stat_rate_inverted")):
        return str(power_config["stat_rate_inverted"]), True
    return None, False


def _device_fallback_name(entity_id: str | None, names: dict[str, str]) -> str:
    if entity_id and entity_id in names:
        return names[entity_id]
    if entity_id:
        object_id = entity_id.split(".", 1)[-1]
        return object_id.replace("_", " ").strip().title() or _ROLE_DEFAULT_NAME[NodeRole.DEVICE]
    return _ROLE_DEFAULT_NAME[NodeRole.DEVICE]


def _named(role: NodeRole, dashboard_name: object) -> tuple[str, bool]:
    """(name, name_is_default): dashboard name if given, else the English role name."""
    if isinstance(dashboard_name, str) and dashboard_name.strip():
        return dashboard_name.strip(), False
    return _ROLE_DEFAULT_NAME[role], True


def _grid_nodes(source: Mapping[str, Any], names: dict[str, str]) -> list[NodeConfig]:
    if "flow_from" in source or "flow_to" in source:
        return _legacy_grid_nodes(source, names)
    energy_from, unreadable_from = _split_readable(source.get("stat_energy_from"))
    energy_to, unreadable_to = _split_readable(source.get("stat_energy_to"))
    power, inverted = _power_from_source(source)
    name, is_default = _named(NodeRole.GRID, source.get("name"))
    return [
        NodeConfig(
            role=NodeRole.GRID,
            index=0,
            name=name,
            energy_from_entity=energy_from,
            energy_to_entity=energy_to,
            power_entity=power,
            inverted=inverted,
            unreadable_statistics=tuple(unreadable_from + unreadable_to),
            energy_from_statistic=_first(unreadable_from),
            energy_to_statistic=_first(unreadable_to),
            name_is_default=is_default,
        )
    ]


def _legacy_grid_nodes(source: Mapping[str, Any], names: dict[str, str]) -> list[NodeConfig]:
    """Legacy grid: `flow_from`/`flow_to` lists are paired positionally into nodes."""
    flows_from = list(source.get("flow_from") or [])
    flows_to = list(source.get("flow_to") or [])
    powers = list(source.get("power") or [])
    count = max(len(flows_from), len(flows_to), 1)
    nodes: list[NodeConfig] = []
    for position in range(count):
        energy_from, unreadable_from = _split_readable(
            flows_from[position].get("stat_energy_from") if position < len(flows_from) else None
        )
        energy_to, unreadable_to = _split_readable(
            flows_to[position].get("stat_energy_to") if position < len(flows_to) else None
        )
        power, inverted = (
            _power_from_source(powers[position]) if position < len(powers) else (None, False)
        )
        nodes.append(
            NodeConfig(
                role=NodeRole.GRID,
                index=0,
                name=_ROLE_DEFAULT_NAME[NodeRole.GRID],
                energy_from_entity=energy_from,
                energy_to_entity=energy_to,
                power_entity=power,
                inverted=inverted,
                unreadable_statistics=tuple(unreadable_from + unreadable_to),
                energy_from_statistic=_first(unreadable_from),
                energy_to_statistic=_first(unreadable_to),
                name_is_default=True,
            )
        )
    return nodes


def _simple_source_node(
    role: NodeRole, source: Mapping[str, Any], names: dict[str, str]
) -> NodeConfig:
    energy_from, unreadable_from = _split_readable(source.get("stat_energy_from"))
    energy_to: str | None = None
    unreadable_to: list[str] = []
    if role is NodeRole.BATTERY:
        energy_to, unreadable_to = _split_readable(source.get("stat_energy_to"))
    power, inverted = _power_from_source(source)
    soc: str | None = None
    capacity: float | None = None
    if role is NodeRole.BATTERY:
        soc_id = source.get("stat_soc")
        soc = str(soc_id) if is_entity_id(soc_id) else None
        raw_capacity = source.get("capacity")
        capacity = float(raw_capacity) if isinstance(raw_capacity, int | float) else None
    name, is_default = _named(role, source.get("name"))
    return NodeConfig(
        role=role,
        index=0,
        name=name,
        energy_from_entity=energy_from,
        energy_to_entity=energy_to,
        power_entity=power,
        soc_entity=soc,
        capacity=capacity,
        inverted=inverted,
        unreadable_statistics=tuple(unreadable_from + unreadable_to),
        energy_from_statistic=_first(unreadable_from),
        energy_to_statistic=_first(unreadable_to),
        name_is_default=is_default,
    )


def _device_node(device: Mapping[str, Any], names: dict[str, str]) -> NodeConfig:
    energy_from, unreadable = _split_readable(device.get("stat_consumption"))
    power, inverted = _power_from_source(device)
    dashboard_name = device.get("name")
    has_name = isinstance(dashboard_name, str) and bool(dashboard_name.strip())
    return NodeConfig(
        role=NodeRole.DEVICE,
        index=0,
        name=dashboard_name.strip() if has_name else _device_fallback_name(energy_from, names),
        energy_from_entity=energy_from,
        power_entity=power,
        inverted=inverted,
        unreadable_statistics=tuple(unreadable),
        energy_from_statistic=_first(unreadable),
        name_is_default=not has_name,
    )


def build_nodes_from_prefs(
    prefs: Mapping[str, Any] | None,
    friendly_names: Mapping[str, str] | None = None,
) -> list[NodeConfig]:
    """Map Energy dashboard preferences to nodes, ordered by role then dashboard order.

    `friendly_names` maps entity ids to display names for nodes without a dashboard name.
    Indices are assigned 0-based in the returned order and are unique.
    """
    if not prefs:
        return []
    names = dict(friendly_names or {})
    unordered: list[NodeConfig] = []
    for source in prefs.get("energy_sources") or []:
        source_type = source.get("type")
        if source_type == "grid":
            unordered.extend(_grid_nodes(source, names))
        elif source_type in _SOURCE_TYPE_TO_ROLE:
            unordered.append(_simple_source_node(_SOURCE_TYPE_TO_ROLE[source_type], source, names))
    for device in prefs.get("device_consumption") or []:
        unordered.append(_device_node(device, names))

    # Stable sort: role order first, dashboard order within a role.
    ordered = sorted(unordered, key=lambda node: ROLE_ORDER[node.role])
    return reindex(number_default_names(ordered[:MAX_NODES]))


def number_default_names(nodes: Iterable[NodeConfig]) -> list[NodeConfig]:
    """Disambiguate repeated default names within a role ("Grid", "Grid 2", ...)."""
    seen: dict[tuple[NodeRole, str], int] = {}
    result: list[NodeConfig] = []
    for node in nodes:
        if not node.name_is_default or node.role is NodeRole.DEVICE:
            result.append(node)
            continue
        key = (node.role, node.name)
        count = seen.get(key, 0) + 1
        seen[key] = count
        result.append(node if count == 1 else replace(node, name=f"{node.name} {count}"))
    return result


def reindex(nodes: Iterable[NodeConfig]) -> list[NodeConfig]:
    """Assign contiguous 0-based indices in iteration order."""
    return [replace(node, index=position) for position, node in enumerate(nodes)]


def merge_user_settings(
    fresh: Iterable[NodeConfig], previous: Iterable[NodeConfig]
) -> list[NodeConfig]:
    """Carry user-chosen power/SoC/inversion settings from `previous` onto re-imported nodes.

    A Home node is never part of a dashboard import, so previous Home nodes are appended as-is.
    Dashboard-provided values (name, stat_rate, stat_soc, capacity) win over stored user choices;
    a generated default name yields to a name the user typed earlier.
    """
    previous_by_identity = {node.identity: node for node in previous}
    merged: list[NodeConfig] = []
    for node in fresh:
        old = previous_by_identity.pop(node.identity, None)
        if old is None:
            merged.append(node)
            continue
        merged.append(
            replace(
                node,
                power_entity=node.power_entity or old.power_entity,
                soc_entity=node.soc_entity or old.soc_entity,
                capacity=node.capacity if node.capacity is not None else old.capacity,
                inverted=old.inverted if node.power_entity is None else node.inverted,
                name=old.name if node.name_is_default and not old.name_is_default else node.name,
                name_is_default=node.name_is_default and old.name_is_default,
            )
        )
    merged.extend(node for node in previous_by_identity.values() if node.role is NodeRole.HOME)
    merged.sort(key=lambda node: ROLE_ORDER[node.role])
    return reindex(merged[:MAX_NODES])
