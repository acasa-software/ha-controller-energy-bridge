"""HAP accessory exposing the Energy dashboard (protocol §4 to §9)."""

from __future__ import annotations

import base64
import logging
from collections.abc import Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any
from uuid import UUID

from pyhap.accessory import Accessory
from pyhap.characteristic import Characteristic
from pyhap.const import CATEGORY_OTHER
from pyhap.service import Service

from .const import (
    ACCESSORY_NAME,
    CAPACITY_MAX,
    CAPACITY_MIN,
    CAPACITY_STEP,
    CHAR_CAPACITY,
    CHAR_ENERGY_FROM,
    CHAR_ENERGY_HISTORY,
    CHAR_ENERGY_TO,
    CHAR_NODE_INDEX,
    CHAR_NODE_ROLE,
    CHAR_POWER,
    CHAR_POWER_HISTORY,
    CHAR_SCHEMA_VERSION,
    CHAR_SOURCE,
    CHAR_SOURCE_ENTITY,
    CHAR_STATE_OF_CHARGE,
    CHAR_UPDATE_INTERVAL,
    ENERGY_MAX,
    ENERGY_MIN,
    ENERGY_STEP,
    ENERGY_TO_ROLES,
    MANUFACTURER,
    MAX_UPDATE_INTERVAL,
    MIN_UPDATE_INTERVAL,
    MODEL,
    NAME_MAX_LEN,
    POWER_MAX,
    POWER_MIN,
    POWER_NON_NEGATIVE_ROLES,
    POWER_REQUIRED_ROLES,
    POWER_STEP,
    SCHEMA_VERSION,
    SERVICE_ENERGY_BRIDGE_INFO,
    SERVICE_ENERGY_NODE,
    SOC_MAX,
    SOC_MIN,
    SOC_STEP,
    SOURCE_ENTITY_MAX_LEN,
    SOURCE_ID,
    SOURCE_MAX_LEN,
    STATUS_FAULT_GENERAL,
    STATUS_FAULT_NONE,
    THRESHOLD_ENERGY_KWH,
    THRESHOLD_POWER_W,
    THRESHOLD_SOC_PERCENT,
    NodeRole,
)
from .energy_import import NodeConfig

if TYPE_CHECKING:
    from pyhap.accessory_driver import AccessoryDriver

_LOGGER = logging.getLogger(__name__)

PERM_READ = "pr"
PERM_EVENT = "ev"


def _char(
    name: str,
    type_id: UUID,
    fmt: str,
    perms: Sequence[str],
    **props: Any,
) -> Characteristic:
    return Characteristic(
        name,
        type_id,
        {"Format": fmt, "Permissions": list(perms), **props},
    )


def _float_char(
    name: str, type_id: UUID, minimum: float, maximum: float, step: float, *, event: bool
) -> Characteristic:
    perms = (PERM_READ, PERM_EVENT) if event else (PERM_READ,)
    return _char(name, type_id, "float", perms, minValue=minimum, maxValue=maximum, minStep=step)


def clamp(value: float, minimum: float, maximum: float) -> float:
    return max(minimum, min(maximum, value))


def normalise_power(role: NodeRole, power: float, inverted: bool) -> float:
    """Apply the inverted toggle and the role's sign rules (§7), then clamp to range."""
    if inverted:
        power = -power
    if role in POWER_NON_NEGATIVE_ROLES and power < 0:
        power = 0.0
    return clamp(power, POWER_MIN, POWER_MAX)


@dataclass(slots=True)
class NodeChars:
    """Characteristics of one Energy Node service, None where the node lacks them."""

    config: NodeConfig
    service: Service
    power: Characteristic | None
    energy_from: Characteristic | None
    energy_to: Characteristic | None
    soc: Characteristic | None
    status_fault: Characteristic
    # History blobs (§13), None where the node lacks the sources.
    energy_history: Characteristic | None = None
    power_history: Characteristic | None = None
    # Last values pushed to HAP, used for the publish thresholds (§9).
    last_power: float | None = None
    last_energy_from: float | None = None
    last_energy_to: float | None = None
    last_soc: float | None = None


class EnergyBridgeAccessory(Accessory):
    """The single accessory of the bridge (§4)."""

    category = CATEGORY_OTHER

    def __init__(
        self,
        driver: AccessoryDriver,
        nodes: Sequence[NodeConfig],
        *,
        update_interval: int,
        serial_number: str,
        firmware_revision: str,
        display_name: str = ACCESSORY_NAME,
    ) -> None:
        super().__init__(driver, display_name)
        self.set_info_service(
            firmware_revision=firmware_revision,
            manufacturer=MANUFACTURER,
            model=MODEL,
            serial_number=serial_number,
        )
        self.update_interval = int(clamp(update_interval, MIN_UPDATE_INTERVAL, MAX_UPDATE_INTERVAL))
        self.nodes: dict[int, NodeChars] = {}

        info = self._build_info_service()
        self.add_service(info)
        self.set_primary_service(info)

        # IIDs are assigned in add order, so a stable node order keeps them stable (§4).
        for config in sorted(nodes, key=lambda node: node.index):
            self.add_service(self._build_node_service(config))

    # --- construction -------------------------------------------------------

    def _build_info_service(self) -> Service:
        service = Service(SERVICE_ENERGY_BRIDGE_INFO, "Energy Bridge Info")
        schema = _char(
            "Schema Version",
            CHAR_SCHEMA_VERSION,
            "uint16",
            (PERM_READ,),
            minValue=0,
            maxValue=65535,
        )
        interval = _char(
            "Update Interval",
            CHAR_UPDATE_INTERVAL,
            "uint16",
            (PERM_READ,),
            minValue=MIN_UPDATE_INTERVAL,
            maxValue=MAX_UPDATE_INTERVAL,
        )
        source = _char("Source", CHAR_SOURCE, "string", (PERM_READ,), maxLen=SOURCE_MAX_LEN)
        service.add_characteristic(schema, interval, source)
        schema.set_value(SCHEMA_VERSION, should_notify=False)
        interval.set_value(self.update_interval, should_notify=False)
        source.set_value(SOURCE_ID, should_notify=False)
        return service

    def _build_node_service(self, config: NodeConfig) -> Service:
        service = Service(SERVICE_ENERGY_NODE, config.name)
        chars: list[Characteristic] = []

        name = self.driver.loader.get_char("Name")
        name.override_properties(properties={"maxLen": NAME_MAX_LEN})
        chars.append(name)

        role = _char("Node Role", CHAR_NODE_ROLE, "uint8", (PERM_READ,), minValue=0, maxValue=255)
        index = _char(
            "Node Index", CHAR_NODE_INDEX, "uint8", (PERM_READ,), minValue=0, maxValue=255
        )
        chars.extend((role, index))

        power: Characteristic | None = None
        if config.role in POWER_REQUIRED_ROLES or config.power_entity:
            power = _float_char("Power", CHAR_POWER, POWER_MIN, POWER_MAX, POWER_STEP, event=True)
            chars.append(power)

        energy_from: Characteristic | None = None
        if config.energy_from_entity:
            energy_from = _float_char(
                "Energy From", CHAR_ENERGY_FROM, ENERGY_MIN, ENERGY_MAX, ENERGY_STEP, event=True
            )
            chars.append(energy_from)

        energy_to: Characteristic | None = None
        if config.energy_to_entity and config.role in ENERGY_TO_ROLES:
            energy_to = _float_char(
                "Energy To", CHAR_ENERGY_TO, ENERGY_MIN, ENERGY_MAX, ENERGY_STEP, event=True
            )
            chars.append(energy_to)

        soc: Characteristic | None = None
        capacity: Characteristic | None = None
        if config.role is NodeRole.BATTERY:
            if config.soc_entity:
                soc = _float_char(
                    "State Of Charge", CHAR_STATE_OF_CHARGE, SOC_MIN, SOC_MAX, SOC_STEP, event=True
                )
                chars.append(soc)
            if config.capacity is not None:
                capacity = _float_char(
                    "Capacity",
                    CHAR_CAPACITY,
                    CAPACITY_MIN,
                    CAPACITY_MAX,
                    CAPACITY_STEP,
                    event=False,
                )
                chars.append(capacity)

        status_fault = self.driver.loader.get_char("StatusFault")
        chars.append(status_fault)

        source_entity: Characteristic | None = None
        if config.power_entity:
            source_entity = _char(
                "Source Entity",
                CHAR_SOURCE_ENTITY,
                "string",
                (PERM_READ,),
                maxLen=SOURCE_ENTITY_MAX_LEN,
            )
            chars.append(source_entity)

        energy_history: Characteristic | None = None
        if config.history_energy_from:
            energy_history = _char("Energy History", CHAR_ENERGY_HISTORY, "data", (PERM_READ,))
            chars.append(energy_history)

        power_history: Characteristic | None = None
        if config.power_entity:
            power_history = _char("Power History", CHAR_POWER_HISTORY, "data", (PERM_READ,))
            chars.append(power_history)

        service.add_characteristic(*chars)

        name.set_value(config.name[:NAME_MAX_LEN], should_notify=False)
        role.set_value(int(config.role), should_notify=False)
        index.set_value(config.index, should_notify=False)
        if capacity is not None and config.capacity is not None:
            capacity.set_value(
                clamp(config.capacity, CAPACITY_MIN, CAPACITY_MAX), should_notify=False
            )
        if source_entity is not None and config.power_entity:
            source_entity.set_value(config.power_entity, should_notify=False)
        # Faulted until the first live value arrives (§8).
        status_fault.set_value(STATUS_FAULT_GENERAL, should_notify=False)

        self.nodes[config.index] = NodeChars(
            config=config,
            service=service,
            power=power,
            energy_from=energy_from,
            energy_to=energy_to,
            soc=soc,
            status_fault=status_fault,
            energy_history=energy_history,
            power_history=power_history,
        )
        return service

    # --- runtime updates ----------------------------------------------------

    def update_node(
        self,
        index: int,
        *,
        power: float | None = None,
        energy_from: float | None = None,
        energy_to: float | None = None,
        soc: float | None = None,
        fault: bool | None = None,
    ) -> None:
        """Push new values for a node, honouring sign rules (§7) and thresholds (§9).

        `None` leaves a value untouched. Values are already unit-normalised
        (W, kWh, %) but not yet inverted or clamped.
        """
        node = self.nodes.get(index)
        if node is None:
            _LOGGER.debug("update_node: unknown node index %s", index)
            return
        config = node.config

        if power is not None and node.power is not None:
            value = normalise_power(config.role, power, config.inverted)
            if node.last_power is None or abs(value - node.last_power) >= THRESHOLD_POWER_W:
                node.power.set_value(value)
                node.last_power = value

        if energy_from is not None and node.energy_from is not None:
            value = clamp(energy_from, ENERGY_MIN, ENERGY_MAX)
            if (
                node.last_energy_from is None
                or abs(value - node.last_energy_from) >= THRESHOLD_ENERGY_KWH
            ):
                node.energy_from.set_value(value)
                node.last_energy_from = value

        if energy_to is not None and node.energy_to is not None:
            value = clamp(energy_to, ENERGY_MIN, ENERGY_MAX)
            if (
                node.last_energy_to is None
                or abs(value - node.last_energy_to) >= THRESHOLD_ENERGY_KWH
            ):
                node.energy_to.set_value(value)
                node.last_energy_to = value

        if soc is not None and node.soc is not None:
            value = clamp(soc, SOC_MIN, SOC_MAX)
            if node.last_soc is None or abs(value - node.last_soc) >= THRESHOLD_SOC_PERCENT:
                node.soc.set_value(value)
                node.last_soc = value

        if fault is not None:
            # pyhap only notifies on change, which matches "publish on every transition".
            node.status_fault.set_value(STATUS_FAULT_GENERAL if fault else STATUS_FAULT_NONE)

    def set_history(
        self, index: int, energy_blob: bytes | None = None, power_blob: bytes | None = None
    ) -> None:
        """Publish history blobs (§13). `None` leaves a blob untouched.

        pyhap serialises `data` values straight into the HAP JSON, so the value must
        already be the base64 text HAP expects, not raw bytes. No `ev` permission, so
        nothing is notified.
        """
        node = self.nodes.get(index)
        if node is None:
            _LOGGER.debug("set_history: unknown node index %s", index)
            return
        if energy_blob is not None and node.energy_history is not None:
            node.energy_history.set_value(
                base64.b64encode(energy_blob).decode("ascii"), should_notify=False
            )
        if power_blob is not None and node.power_history is not None:
            node.power_history.set_value(
                base64.b64encode(power_blob).decode("ascii"), should_notify=False
            )

    def node_indices(self) -> list[int]:
        return sorted(self.nodes)
