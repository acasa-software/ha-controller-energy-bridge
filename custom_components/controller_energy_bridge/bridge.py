"""HAP server lifecycle and Home Assistant state forwarding."""

from __future__ import annotations

import logging
import socket
import time
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

from homeassistant.components import network, zeroconf
from homeassistant.components.recorder import DOMAIN as RECORDER_DOMAIN
from homeassistant.components.recorder import get_instance
from homeassistant.components.recorder.statistics import (
    get_metadata,
    statistics_during_period,
)
from homeassistant.const import (
    ATTR_UNIT_OF_MEASUREMENT,
    EVENT_HOMEASSISTANT_STOP,
    STATE_UNAVAILABLE,
    STATE_UNKNOWN,
)
from homeassistant.core import (
    CALLBACK_TYPE,
    Event,
    EventStateChangedData,
    HomeAssistant,
    State,
    callback,
)
from homeassistant.helpers.event import (
    async_call_later,
    async_track_state_change_event,
    async_track_time_change,
    async_track_time_interval,
)
from homeassistant.util import dt as dt_util
from pyhap import util as pyhap_util
from pyhap.accessory_driver import AccessoryDriver
from pyhap.loader import get_loader

from .accessory import EnergyBridgeAccessory
from .const import (
    ENERGY_HISTORY_BUCKET_COUNT,
    ENERGY_HISTORY_BUCKET_SECONDS,
    ENERGY_HISTORY_REBUILD_MINUTE,
    HISTORY_KIND_ENERGY,
    HISTORY_KIND_POWER,
    PERSIST_FILE,
    POWER_HISTORY_BUCKET_COUNT,
    POWER_HISTORY_BUCKET_SECONDS,
    POWER_HISTORY_REBUILD_SECONDS,
)
from .energy_import import NodeConfig
from .history import (
    aligned_start,
    energy_series_from_statistics,
    nan_count,
    pack_history,
    power_series_from_statistics,
)
from .units import energy_to_kwh, parse_numeric, power_to_watts, soc_to_percent

_LOGGER = logging.getLogger(__name__)

# Bind like the core HomeKit integration; pyhap would otherwise probe the network for one IP.
_BIND_ADDRESSES = ["0.0.0.0", "::"] if socket.has_ipv6 else ["0.0.0.0"]


class SharedZeroconfDriver(AccessoryDriver):
    """AccessoryDriver that leaves Home Assistant's shared Zeroconf instance open on stop."""

    async def async_stop(self) -> None:
        self.stop_event.set()
        await self.advertiser.async_unregister_service(self.mdns_service_info)
        self.aio_stop_event.set()
        self.http_server.async_stop()
        await self.accessory.stop()


@dataclass(slots=True)
class _NodeRuntime:
    """Per-node throttle state: pending values are flushed at most once per interval."""

    config: NodeConfig
    pending: dict[str, Any] = field(default_factory=dict)
    last_flush: float = 0.0
    timer: CALLBACK_TYPE | None = None


@dataclass(slots=True)
class HistoryStatus:
    """What the last rebuild of one blob produced, for diagnostics."""

    buckets: int = 0
    nan: int = 0
    size: int = 0
    rebuilt_at: datetime | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "buckets": self.buckets,
            "nan": self.nan,
            "size": self.size,
            "rebuilt_at": self.rebuilt_at.isoformat() if self.rebuilt_at else None,
        }


@dataclass(frozen=True, slots=True)
class SourceReading:
    """A decoded sensor state: `value` is None when the state is not numeric."""

    value: float | None
    faulted: bool


def read_state(state: State | None) -> SourceReading:
    """Decode a state object into a numeric value plus fault flag (§8)."""
    if state is None or state.state in (STATE_UNAVAILABLE, STATE_UNKNOWN):
        return SourceReading(None, True)
    value = parse_numeric(state.state)
    if value is None:
        return SourceReading(None, True)
    return SourceReading(value, False)


def _unit(state: State | None) -> str | None:
    if state is None:
        return None
    unit = state.attributes.get(ATTR_UNIT_OF_MEASUREMENT)
    return str(unit) if unit is not None else None


class EnergyBridge:
    """Owns the HAP driver and forwards Home Assistant state to the accessory."""

    def __init__(
        self,
        hass: HomeAssistant,
        nodes: Sequence[NodeConfig],
        *,
        port: int,
        update_interval: int,
        serial_number: str,
        firmware_revision: str,
    ) -> None:
        self.hass = hass
        self.nodes = list(nodes)
        self.port = port
        self.update_interval = max(1, int(update_interval))
        self.serial_number = serial_number
        self.firmware_revision = firmware_revision
        self.driver: SharedZeroconfDriver | None = None
        self.accessory: EnergyBridgeAccessory | None = None
        self._runtime: dict[int, _NodeRuntime] = {
            node.index: _NodeRuntime(node) for node in self.nodes
        }
        self._entity_to_nodes: dict[str, list[int]] = {}
        for node in self.nodes:
            for entity_id in node.source_entities:
                self._entity_to_nodes.setdefault(entity_id, []).append(node.index)
        self._unsubscribers: list[CALLBACK_TYPE] = []
        self._started = False
        self.energy_history: dict[int, HistoryStatus] = {}
        self.power_history: dict[int, HistoryStatus] = {}

    # --- lifecycle ----------------------------------------------------------

    async def async_setup(self) -> None:
        """Build driver and accessory; does not start the HAP server yet."""
        hass = self.hass
        self.driver = SharedZeroconfDriver(
            loop=hass.loop,
            address=_BIND_ADDRESSES,
            port=self.port,
            persist_file=hass.config.path(PERSIST_FILE),
            advertised_address=await network.async_get_announce_addresses(hass),
            async_zeroconf_instance=await zeroconf.async_get_async_instance(hass),
            loader=await hass.async_add_executor_job(get_loader),
            mac=pyhap_util.generate_mac(),
        )
        self.accessory = EnergyBridgeAccessory(
            self.driver,
            self.nodes,
            update_interval=self.update_interval,
            serial_number=self.serial_number,
            firmware_revision=self.firmware_revision,
        )
        # add_accessory persists to disk.
        await hass.async_add_executor_job(self.driver.add_accessory, self.accessory)

    async def async_start(self) -> None:
        assert self.driver is not None
        await self.driver.async_start()
        self._started = True
        if self._entity_to_nodes:
            self._unsubscribers.append(
                async_track_state_change_event(
                    self.hass, list(self._entity_to_nodes), self._handle_state_change
                )
            )
        self._unsubscribers.append(
            self.hass.bus.async_listen_once(EVENT_HOMEASSISTANT_STOP, self._handle_hass_stop)
        )
        # Seed every node from current states so the first read is not all-faulted.
        for node in self.nodes:
            self._collect_node(node.index)
            self._flush(node.index)
        await self._async_start_history()

    async def _handle_hass_stop(self, _event: Event) -> None:
        await self.async_stop()

    async def async_stop(self) -> None:
        for unsubscribe in self._unsubscribers:
            unsubscribe()
        self._unsubscribers.clear()
        for runtime in self._runtime.values():
            if runtime.timer is not None:
                runtime.timer()
                runtime.timer = None
        if self.driver is not None and self._started:
            self._started = False
            try:
                await self.driver.async_stop()
            except Exception:  # noqa: BLE001 - shutdown must not raise into HA
                _LOGGER.exception("Error while stopping the HAP driver")

    def bump_config_version(self) -> None:
        """Bump the HAP configuration number (§4, §9) before the server starts.

        Never via pyhap's `config_changed` on a running driver: its deferred advertisement
        update can re-add the mDNS name after a reload unregistered it (orphan record,
        ServiceNameAlreadyRegistered on every later start until HA restarts).
        """
        assert self.driver is not None and not self._started
        self.driver.state.increment_config_version()
        self.driver.persist()

    # --- pairing info -------------------------------------------------------

    @property
    def paired(self) -> bool:
        return bool(self.driver and self.driver.state.paired)

    @property
    def pincode(self) -> str | None:
        if self.driver is None:
            return None
        return self.driver.state.pincode.decode()

    @property
    def setup_uri(self) -> str | None:
        if self.accessory is None:
            return None
        try:
            return self.accessory.xhm_uri()
        except NameError:
            # pyhap imports base36 lazily; without it there is no X-HM URI, only the PIN.
            return None

    # --- state forwarding ---------------------------------------------------

    @callback
    def _handle_state_change(self, event: Event[EventStateChangedData]) -> None:
        entity_id = event.data["entity_id"]
        for index in self._entity_to_nodes.get(entity_id, ()):
            self._collect_node(index)
            self._schedule_flush(index)

    def _collect_node(self, index: int) -> None:
        """Read all sources of a node into its pending buffer."""
        runtime = self._runtime[index]
        config = runtime.config
        pending = runtime.pending
        states = self.hass.states

        if config.power_entity:
            state = states.get(config.power_entity)
            reading = read_state(state)
            pending["fault"] = reading.faulted
            if reading.value is not None:
                pending["power"] = power_to_watts(reading.value, _unit(state))
        else:
            # No live power source: the node is permanently faulted (§8, §11).
            pending["fault"] = True

        if config.energy_from_entity:
            state = states.get(config.energy_from_entity)
            reading = read_state(state)
            if reading.value is not None:
                pending["energy_from"] = energy_to_kwh(reading.value, _unit(state))

        if config.energy_to_entity:
            state = states.get(config.energy_to_entity)
            reading = read_state(state)
            if reading.value is not None:
                pending["energy_to"] = energy_to_kwh(reading.value, _unit(state))

        if config.soc_entity:
            state = states.get(config.soc_entity)
            reading = read_state(state)
            if reading.value is not None:
                pending["soc"] = soc_to_percent(reading.value, _unit(state))

    @callback
    def _schedule_flush(self, index: int) -> None:
        runtime = self._runtime[index]
        now = time.monotonic()
        elapsed = now - runtime.last_flush
        if elapsed >= self.update_interval:
            self._flush(index)
            return
        if runtime.timer is None:
            delay = self.update_interval - elapsed

            @callback
            def _fire(_now: Any) -> None:
                runtime.timer = None
                self._flush(index)

            runtime.timer = async_call_later(self.hass, delay, _fire)

    @callback
    def _flush(self, index: int) -> None:
        runtime = self._runtime[index]
        if self.accessory is None or not runtime.pending:
            return
        pending, runtime.pending = runtime.pending, {}
        runtime.last_flush = time.monotonic()
        self.accessory.update_node(
            index,
            power=pending.get("power"),
            energy_from=pending.get("energy_from"),
            energy_to=pending.get("energy_to"),
            soc=pending.get("soc"),
            fault=pending.get("fault"),
        )

    # --- history (§13) ------------------------------------------------------

    @property
    def history_available(self) -> bool:
        return RECORDER_DOMAIN in self.hass.config.components

    async def _async_start_history(self) -> None:
        if not self.history_available:
            _LOGGER.warning("Recorder not loaded, history characteristics stay empty")
            return
        if not any(n.history_energy_from or n.power_entity for n in self.nodes):
            return
        await self.async_rebuild_energy_history()
        await self.async_rebuild_power_history()
        self._unsubscribers.append(
            async_track_time_change(
                self.hass,
                self._scheduled_energy_rebuild,
                minute=ENERGY_HISTORY_REBUILD_MINUTE,
                second=0,
            )
        )
        self._unsubscribers.append(
            async_track_time_interval(
                self.hass,
                self._scheduled_power_rebuild,
                timedelta(seconds=POWER_HISTORY_REBUILD_SECONDS),
            )
        )

    async def _scheduled_energy_rebuild(self, _now: datetime) -> None:
        await self.async_rebuild_energy_history()

    async def _scheduled_power_rebuild(self, _now: datetime) -> None:
        await self.async_rebuild_power_history()

    async def _async_statistics(
        self,
        statistic_ids: set[str],
        start: int,
        period: str,
        types: set[str],
    ) -> tuple[dict[str, list[dict[str, Any]]], dict[str, str | None]]:
        """Rows per statistic id plus each id's unit, read on the recorder's executor."""
        start_time = dt_util.utc_from_timestamp(start)
        instance = get_instance(self.hass)
        rows = await instance.async_add_executor_job(
            statistics_during_period,
            self.hass,
            start_time,
            None,
            statistic_ids,
            period,
            None,
            types,
        )
        metadata = await instance.async_add_executor_job(
            lambda: get_metadata(self.hass, statistic_ids=statistic_ids)
        )
        units = {sid: meta.get("unit_of_measurement") for sid, (_id, meta) in metadata.items()}
        return rows, units

    async def async_rebuild_energy_history(self) -> None:
        """Rebuild Energy History (hourly `change`, 30 days) for every node with energy sources."""
        if self.accessory is None:
            return
        targets = [n for n in self.nodes if n.history_energy_from]
        if not targets:
            return
        ids = {
            sid
            for node in targets
            for sid in (node.history_energy_from, node.history_energy_to)
            if sid
        }
        start = aligned_start(
            time.time(), ENERGY_HISTORY_BUCKET_SECONDS, ENERGY_HISTORY_BUCKET_COUNT
        )
        try:
            rows, units = await self._async_statistics(ids, start, "hour", {"change"})
        except Exception:  # noqa: BLE001 - a failed rebuild keeps the previous blob
            _LOGGER.exception("Reading energy statistics failed")
            return
        now = dt_util.utcnow()
        for node in targets:
            series = [
                energy_series_from_statistics(
                    rows.get(node.history_energy_from, ()),
                    start,
                    ENERGY_HISTORY_BUCKET_SECONDS,
                    ENERGY_HISTORY_BUCKET_COUNT,
                    units.get(node.history_energy_from),
                )
            ]
            if node.history_energy_to:
                series.append(
                    energy_series_from_statistics(
                        rows.get(node.history_energy_to, ()),
                        start,
                        ENERGY_HISTORY_BUCKET_SECONDS,
                        ENERGY_HISTORY_BUCKET_COUNT,
                        units.get(node.history_energy_to),
                    )
                )
            blob = pack_history(HISTORY_KIND_ENERGY, ENERGY_HISTORY_BUCKET_SECONDS, start, series)
            self.accessory.set_history(node.index, energy_blob=blob)
            self.energy_history[node.index] = HistoryStatus(
                ENERGY_HISTORY_BUCKET_COUNT, nan_count(series), len(blob), now
            )

    async def async_rebuild_power_history(self) -> None:
        """Rebuild Power History (5-minute `mean` folded into 15-minute buckets, 24 hours)."""
        if self.accessory is None:
            return
        targets = [n for n in self.nodes if n.power_entity]
        if not targets:
            return
        ids = {n.power_entity for n in targets if n.power_entity}
        start = aligned_start(time.time(), POWER_HISTORY_BUCKET_SECONDS, POWER_HISTORY_BUCKET_COUNT)
        try:
            rows, units = await self._async_statistics(ids, start, "5minute", {"mean"})
        except Exception:  # noqa: BLE001 - a failed rebuild keeps the previous blob
            _LOGGER.exception("Reading power statistics failed")
            return
        now = dt_util.utcnow()
        for node in targets:
            series = [
                power_series_from_statistics(
                    rows.get(node.power_entity, ()),
                    start,
                    POWER_HISTORY_BUCKET_SECONDS,
                    POWER_HISTORY_BUCKET_COUNT,
                    units.get(node.power_entity),
                    role=node.role,
                    inverted=node.inverted,
                )
            ]
            blob = pack_history(HISTORY_KIND_POWER, POWER_HISTORY_BUCKET_SECONDS, start, series)
            self.accessory.set_history(node.index, power_blob=blob)
            self.power_history[node.index] = HistoryStatus(
                POWER_HISTORY_BUCKET_COUNT, nan_count(series), len(blob), now
            )
