"""Accessory structure and update rules against docs/protocol.md."""

from __future__ import annotations

import base64
from typing import Any
from uuid import UUID

import pytest
from pyhap.accessory_driver import AccessoryDriver

from custom_components.controller_energy_bridge.accessory import (
    EnergyBridgeAccessory,
    normalise_power,
)
from custom_components.controller_energy_bridge.const import (
    CHAR_CAPACITY,
    CHAR_ENERGY_FROM,
    CHAR_ENERGY_HISTORY,
    CHAR_ENERGY_TO,
    CHAR_NAME,
    CHAR_NODE_INDEX,
    CHAR_NODE_ROLE,
    CHAR_POWER,
    CHAR_POWER_HISTORY,
    CHAR_SCHEMA_VERSION,
    CHAR_SOURCE,
    CHAR_SOURCE_ENTITY,
    CHAR_STATE_OF_CHARGE,
    CHAR_STATUS_FAULT,
    CHAR_UPDATE_INTERVAL,
    ENERGY_HISTORY_BUCKET_COUNT,
    ENERGY_HISTORY_BUCKET_SECONDS,
    ENERGY_HISTORY_MAX_BYTES,
    HISTORY_KIND_ENERGY,
    HISTORY_KIND_POWER,
    POWER_HISTORY_BUCKET_COUNT,
    POWER_HISTORY_BUCKET_SECONDS,
    POWER_HISTORY_MAX_BYTES,
    SERVICE_ACCESSORY_INFORMATION,
    SERVICE_ENERGY_BRIDGE_INFO,
    SERVICE_ENERGY_NODE,
    NodeRole,
)
from custom_components.controller_energy_bridge.energy_import import NodeConfig, reindex
from custom_components.controller_energy_bridge.history import aligned_start, pack_history

SUFFIX = "-ACA5-4C0E-8E5F-2D0E71F0E5B1"


def _hap(accessory: EnergyBridgeAccessory) -> dict[str, Any]:
    return accessory.to_HAP()


def _services(hap: dict[str, Any], type_id: UUID) -> list[dict[str, Any]]:
    wanted = str(type_id).upper()
    # pyhap shortens Apple UUIDs (e.g. "3E"); custom ones stay in full.
    return [s for s in hap["services"] if _expand(s["type"]) == wanted]


def _expand(short: str) -> str:
    short = short.upper()
    if len(short) <= 8:
        return f"{int(short, 16):08X}-0000-1000-8000-0026BB765291"
    return short


def _chars(service: dict[str, Any]) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for char in service["characteristics"]:
        result[_expand(char["type"])] = char
    return result


def _u(type_id: UUID) -> str:
    return str(type_id).upper()


def _build(
    driver: AccessoryDriver, nodes: list[NodeConfig], **kwargs: Any
) -> EnergyBridgeAccessory:
    """Build with contiguous indices so single-node tests can address index 0."""
    accessory = EnergyBridgeAccessory(
        driver,
        reindex(nodes),
        update_interval=kwargs.pop("update_interval", 5),
        serial_number=kwargs.pop("serial_number", "abcdef012345"),
        firmware_revision=kwargs.pop("firmware_revision", "1.0.0"),
        **kwargs,
    )
    driver.add_accessory(accessory)
    return accessory


GRID = NodeConfig(NodeRole.GRID, 0, "Netz", "sensor.imp", "sensor.exp", "sensor.grid_p")
SOLAR = NodeConfig(NodeRole.SOLAR, 1, "Solar", "sensor.pv_e", None, "sensor.pv_p")
BATTERY = NodeConfig(
    NodeRole.BATTERY, 2, "Speicher", "sensor.dis", "sensor.chg", "sensor.bat_p", "sensor.soc", 10.0
)
DEVICE = NodeConfig(NodeRole.DEVICE, 3, "Well pump", "sensor.pump_e", None, "sensor.pump_p")


def test_uuid_layout() -> None:
    assert _u(SERVICE_ENERGY_BRIDGE_INFO) == "00000001" + SUFFIX
    assert _u(SERVICE_ENERGY_NODE) == "00000002" + SUFFIX
    assert _u(CHAR_SCHEMA_VERSION) == "00000101" + SUFFIX
    assert _u(CHAR_UPDATE_INTERVAL) == "00000102" + SUFFIX
    assert _u(CHAR_SOURCE) == "00000103" + SUFFIX
    assert _u(CHAR_NODE_ROLE) == "00000111" + SUFFIX
    assert _u(CHAR_NODE_INDEX) == "00000112" + SUFFIX
    assert _u(CHAR_POWER) == "00000113" + SUFFIX
    assert _u(CHAR_ENERGY_FROM) == "00000114" + SUFFIX
    assert _u(CHAR_ENERGY_TO) == "00000115" + SUFFIX
    assert _u(CHAR_STATE_OF_CHARGE) == "00000116" + SUFFIX
    assert _u(CHAR_CAPACITY) == "00000117" + SUFFIX
    assert _u(CHAR_SOURCE_ENTITY) == "00000118" + SUFFIX
    assert _u(CHAR_ENERGY_HISTORY) == "00000119" + SUFFIX
    assert _u(CHAR_POWER_HISTORY) == "0000011A" + SUFFIX
    assert _u(CHAR_NAME) == "00000023-0000-1000-8000-0026BB765291"
    assert _u(CHAR_STATUS_FAULT) == "00000077-0000-1000-8000-0026BB765291"


def test_accessory_information_and_category(hap_driver: AccessoryDriver) -> None:
    accessory = _build(hap_driver, [GRID])
    hap = _hap(accessory)
    assert hap["aid"] == 1
    assert accessory.category == 1
    info = _chars(_services(hap, SERVICE_ACCESSORY_INFORMATION)[0])
    values = {
        c["description"] if "description" in c else c["type"]: c.get("value") for c in info.values()
    }
    assert "Acasa Software" in values.values()
    assert "Controller Energy Bridge" in values.values()
    assert "Controller Energy" in values.values()
    assert "abcdef012345" in values.values()
    assert "1.0.0" in values.values()


def test_info_service_is_primary_and_exact(hap_driver: AccessoryDriver) -> None:
    accessory = _build(hap_driver, [GRID], update_interval=7)
    services = _services(_hap(accessory), SERVICE_ENERGY_BRIDGE_INFO)
    assert len(services) == 1
    assert services[0]["primary"] is True
    chars = _chars(services[0])
    assert set(chars) == {_u(CHAR_SCHEMA_VERSION), _u(CHAR_UPDATE_INTERVAL), _u(CHAR_SOURCE)}
    schema = chars[_u(CHAR_SCHEMA_VERSION)]
    assert (schema["format"], schema["perms"], schema["value"]) == ("uint16", ["pr"], 1)
    interval = chars[_u(CHAR_UPDATE_INTERVAL)]
    assert (interval["format"], interval["perms"], interval["value"]) == ("uint16", ["pr"], 7)
    assert (interval["minValue"], interval["maxValue"]) == (1, 300)
    source = chars[_u(CHAR_SOURCE)]
    assert (source["format"], source["perms"], source["value"]) == (
        "string",
        ["pr"],
        "home-assistant",
    )
    assert source["maxLen"] == 32


def test_update_interval_is_clamped(hap_driver: AccessoryDriver) -> None:
    accessory = _build(hap_driver, [], update_interval=900)
    chars = _chars(_services(_hap(accessory), SERVICE_ENERGY_BRIDGE_INFO)[0])
    assert chars[_u(CHAR_UPDATE_INTERVAL)]["value"] == 300


def test_node_services_in_index_order_with_stable_iids(hap_driver: AccessoryDriver) -> None:
    accessory = _build(hap_driver, [DEVICE, BATTERY, GRID, SOLAR])
    services = _services(_hap(accessory), SERVICE_ENERGY_NODE)
    indices = [_chars(s)[_u(CHAR_NODE_INDEX)]["value"] for s in services]
    assert indices == [0, 1, 2, 3]
    iids = [s["iid"] for s in services]
    assert iids == sorted(iids)


def test_grid_node_characteristics(hap_driver: AccessoryDriver) -> None:
    accessory = _build(hap_driver, [GRID])
    service = _services(_hap(accessory), SERVICE_ENERGY_NODE)[0]
    chars = _chars(service)
    assert set(chars) == {
        _u(CHAR_NAME),
        _u(CHAR_NODE_ROLE),
        _u(CHAR_NODE_INDEX),
        _u(CHAR_POWER),
        _u(CHAR_ENERGY_FROM),
        _u(CHAR_ENERGY_TO),
        _u(CHAR_STATUS_FAULT),
        _u(CHAR_SOURCE_ENTITY),
        _u(CHAR_ENERGY_HISTORY),
        _u(CHAR_POWER_HISTORY),
    }
    assert chars[_u(CHAR_NAME)]["value"] == "Netz"
    assert chars[_u(CHAR_NAME)]["perms"] == ["pr"]
    role = chars[_u(CHAR_NODE_ROLE)]
    assert (role["format"], role["perms"], role["value"]) == ("uint8", ["pr"], 1)
    index = chars[_u(CHAR_NODE_INDEX)]
    assert (index["format"], index["perms"], index["value"]) == ("uint8", ["pr"], 0)
    power = chars[_u(CHAR_POWER)]
    assert power["format"] == "float"
    assert sorted(power["perms"]) == ["ev", "pr"]
    assert (power["minValue"], power["maxValue"], power["minStep"]) == (-1_000_000, 1_000_000, 1)
    for key in (CHAR_ENERGY_FROM, CHAR_ENERGY_TO):
        energy = chars[_u(key)]
        assert energy["format"] == "float"
        assert sorted(energy["perms"]) == ["ev", "pr"]
        assert (energy["minValue"], energy["maxValue"]) == (0, 1_000_000_000)
        assert energy["minStep"] == pytest.approx(0.001)
    fault = chars[_u(CHAR_STATUS_FAULT)]
    assert fault["format"] == "uint8"
    assert sorted(fault["perms"]) == ["ev", "pr"]
    assert fault["value"] == 1  # faulted until the first live value
    source = chars[_u(CHAR_SOURCE_ENTITY)]
    assert (source["format"], source["perms"], source["value"]) == (
        "string",
        ["pr"],
        "sensor.grid_p",
    )
    assert source["maxLen"] == 128


def test_solar_has_no_energy_to_even_if_configured(hap_driver: AccessoryDriver) -> None:
    solar = NodeConfig(NodeRole.SOLAR, 0, "PV", "sensor.pv_e", "sensor.bogus", "sensor.pv_p")
    accessory = _build(hap_driver, [solar])
    chars = _chars(_services(_hap(accessory), SERVICE_ENERGY_NODE)[0])
    assert _u(CHAR_ENERGY_TO) not in chars
    assert _u(CHAR_ENERGY_FROM) in chars
    assert _u(CHAR_STATE_OF_CHARGE) not in chars
    assert _u(CHAR_CAPACITY) not in chars


def test_battery_node_characteristics(hap_driver: AccessoryDriver) -> None:
    accessory = _build(hap_driver, [BATTERY])
    chars = _chars(_services(_hap(accessory), SERVICE_ENERGY_NODE)[0])
    soc = chars[_u(CHAR_STATE_OF_CHARGE)]
    assert soc["format"] == "float"
    assert sorted(soc["perms"]) == ["ev", "pr"]
    assert (soc["minValue"], soc["maxValue"]) == (0, 100)
    assert soc["minStep"] == pytest.approx(0.1)
    capacity = chars[_u(CHAR_CAPACITY)]
    assert (capacity["format"], capacity["perms"]) == ("float", ["pr"])
    assert (capacity["minValue"], capacity["maxValue"]) == (0, 100_000)
    assert capacity["minStep"] == pytest.approx(0.01)
    assert capacity["value"] == pytest.approx(10.0)
    assert _u(CHAR_ENERGY_TO) in chars


def test_optional_characteristics_are_omitted_not_zeroed(hap_driver: AccessoryDriver) -> None:
    battery = NodeConfig(NodeRole.BATTERY, 0, "Akku", None, None, "sensor.p")
    device_without_power = NodeConfig(NodeRole.DEVICE, 1, "Plug", "sensor.plug_e")
    accessory = _build(hap_driver, [battery, device_without_power])
    services = _services(_hap(accessory), SERVICE_ENERGY_NODE)
    battery_chars = _chars(services[0])
    assert _u(CHAR_ENERGY_FROM) not in battery_chars
    assert _u(CHAR_ENERGY_TO) not in battery_chars
    assert _u(CHAR_STATE_OF_CHARGE) not in battery_chars
    assert _u(CHAR_CAPACITY) not in battery_chars
    device_chars = _chars(services[1])
    # Power is required for Device even without a source; Source Entity is not.
    assert _u(CHAR_POWER) in device_chars
    assert _u(CHAR_SOURCE_ENTITY) not in device_chars
    assert device_chars[_u(CHAR_STATUS_FAULT)]["value"] == 1


def test_external_statistic_node_has_no_energy_and_faults(hap_driver: AccessoryDriver) -> None:
    node = NodeConfig(NodeRole.SOLAR, 0, "PV", None, None, None, unreadable_statistics=("pv:ext",))
    accessory = _build(hap_driver, [node])
    chars = _chars(_services(_hap(accessory), SERVICE_ENERGY_NODE)[0])
    assert _u(CHAR_ENERGY_FROM) not in chars
    assert _u(CHAR_POWER) in chars  # required for Solar
    assert chars[_u(CHAR_STATUS_FAULT)]["value"] == 1


def test_gas_without_power_source_omits_power(hap_driver: AccessoryDriver) -> None:
    gas = NodeConfig(NodeRole.GAS, 0, "Gas", "sensor.gas")
    water = NodeConfig(NodeRole.WATER, 1, "Wasser", "sensor.water", None, "sensor.flow")
    accessory = _build(hap_driver, [gas, water])
    services = _services(_hap(accessory), SERVICE_ENERGY_NODE)
    assert _u(CHAR_POWER) not in _chars(services[0])
    assert _u(CHAR_POWER) in _chars(services[1])
    assert _chars(services[0])[_u(CHAR_NODE_ROLE)]["value"] == 6
    assert _chars(services[1])[_u(CHAR_NODE_ROLE)]["value"] == 7


def test_name_is_truncated_to_64(hap_driver: AccessoryDriver) -> None:
    node = NodeConfig(NodeRole.DEVICE, 0, "x" * 100, "sensor.e", None, "sensor.p")
    accessory = _build(hap_driver, [node])
    chars = _chars(_services(_hap(accessory), SERVICE_ENERGY_NODE)[0])
    assert len(chars[_u(CHAR_NAME)]["value"]) == 64


# --- sign conventions (§7) ---------------------------------------------------


@pytest.mark.parametrize(
    ("role", "power", "inverted", "expected"),
    [
        (NodeRole.GRID, -1500.0, False, -1500.0),
        (NodeRole.GRID, -1500.0, True, 1500.0),
        (NodeRole.BATTERY, -400.0, False, -400.0),
        (NodeRole.BATTERY, 400.0, True, -400.0),
        (NodeRole.SOLAR, -50.0, False, 0.0),
        (NodeRole.SOLAR, 50.0, True, 0.0),
        (NodeRole.SOLAR, -2500.0, True, 2500.0),
        (NodeRole.HOME, -10.0, False, 0.0),
        (NodeRole.DEVICE, -10.0, False, 0.0),
        (NodeRole.DEVICE, 10.0, False, 10.0),
        (NodeRole.GRID, 5_000_000.0, False, 1_000_000.0),
        (NodeRole.GRID, -5_000_000.0, False, -1_000_000.0),
    ],
)
def test_normalise_power(role: NodeRole, power: float, inverted: bool, expected: float) -> None:
    assert normalise_power(role, power, inverted) == expected


# --- update thresholds and clamping (§9, §5) -----------------------------------


def _values(accessory: EnergyBridgeAccessory, index: int) -> dict[str, Any]:
    node = accessory.nodes[index]
    return {
        "power": node.power.get_value() if node.power else None,
        "energy_from": node.energy_from.get_value() if node.energy_from else None,
        "energy_to": node.energy_to.get_value() if node.energy_to else None,
        "soc": node.soc.get_value() if node.soc else None,
        "fault": node.status_fault.get_value(),
    }


def test_update_node_reference_example(hap_driver: AccessoryDriver) -> None:
    accessory = _build(hap_driver, [GRID, SOLAR, BATTERY, DEVICE])
    accessory.update_node(0, power=-1500, energy_from=500.0, energy_to=1200.00, fault=False)
    accessory.update_node(1, power=2500, energy_from=15000.0, fault=False)
    accessory.update_node(2, power=-400, energy_from=450.0, energy_to=520.0, soc=55.0, fault=False)
    accessory.update_node(3, power=0, energy_from=120.0, fault=False)
    assert _values(accessory, 0) == {
        "power": -1500.0,
        "energy_from": pytest.approx(500.0),
        "energy_to": pytest.approx(1200.00),
        "soc": None,
        "fault": 0,
    }
    assert _values(accessory, 2)["soc"] == pytest.approx(55.0)
    assert _values(accessory, 3)["power"] == 0.0


def test_power_threshold_one_watt(hap_driver: AccessoryDriver) -> None:
    accessory = _build(hap_driver, [GRID])
    accessory.update_node(0, power=100.0)
    accessory.update_node(0, power=100.6)
    assert _values(accessory, 0)["power"] == 100.0
    accessory.update_node(0, power=101.0)
    assert _values(accessory, 0)["power"] == 101.0
    accessory.update_node(0, power=99.9)
    assert _values(accessory, 0)["power"] == 100.0


def test_energy_threshold(hap_driver: AccessoryDriver) -> None:
    accessory = _build(hap_driver, [GRID])
    accessory.update_node(0, energy_from=100.0, energy_to=5.0)
    accessory.update_node(0, energy_from=100.0004, energy_to=5.0009)
    assert _values(accessory, 0)["energy_from"] == pytest.approx(100.0)
    assert _values(accessory, 0)["energy_to"] == pytest.approx(5.0)
    accessory.update_node(0, energy_from=100.001, energy_to=5.002)
    assert _values(accessory, 0)["energy_from"] == pytest.approx(100.001)
    assert _values(accessory, 0)["energy_to"] == pytest.approx(5.002)


def test_energy_may_decrease_on_meter_reset(hap_driver: AccessoryDriver) -> None:
    accessory = _build(hap_driver, [SOLAR])
    accessory.update_node(0, energy_from=500.0)
    accessory.update_node(0, energy_from=0.2)
    assert _values(accessory, 0)["energy_from"] == pytest.approx(0.2)


def test_soc_threshold_half_percent(hap_driver: AccessoryDriver) -> None:
    accessory = _build(hap_driver, [BATTERY])
    accessory.update_node(0, soc=50.0)
    accessory.update_node(0, soc=50.4)
    assert _values(accessory, 0)["soc"] == pytest.approx(50.0)
    accessory.update_node(0, soc=50.5)
    assert _values(accessory, 0)["soc"] == pytest.approx(50.5)


def test_fault_publishes_on_every_transition_and_keeps_values(hap_driver: AccessoryDriver) -> None:
    accessory = _build(hap_driver, [GRID])
    accessory.update_node(0, power=250.0, energy_from=1.0, fault=False)
    accessory.update_node(0, fault=True)
    assert _values(accessory, 0)["fault"] == 1
    assert _values(accessory, 0)["power"] == 250.0
    assert _values(accessory, 0)["energy_from"] == pytest.approx(1.0)
    accessory.update_node(0, fault=False)
    assert _values(accessory, 0)["fault"] == 0


def test_clamping_of_ranges(hap_driver: AccessoryDriver) -> None:
    accessory = _build(hap_driver, [BATTERY])
    accessory.update_node(0, energy_from=-5.0, energy_to=2e9, soc=140.0)
    values = _values(accessory, 0)
    assert values["energy_from"] == 0.0
    assert values["energy_to"] == 1_000_000_000.0
    assert values["soc"] == 100.0
    accessory.update_node(0, soc=-3.0)
    assert _values(accessory, 0)["soc"] == 0.0


def test_inverted_node_is_normalised_before_publish(hap_driver: AccessoryDriver) -> None:
    inverted_grid = NodeConfig(NodeRole.GRID, 0, "Netz", None, None, "sensor.p", inverted=True)
    accessory = _build(hap_driver, [inverted_grid])
    accessory.update_node(0, power=1500.0)
    assert _values(accessory, 0)["power"] == -1500.0


def test_none_leaves_values_untouched_and_unknown_index_is_ignored(
    hap_driver: AccessoryDriver,
) -> None:
    accessory = _build(hap_driver, [GRID])
    accessory.update_node(0, power=10.0, fault=False)
    accessory.update_node(0)
    accessory.update_node(99, power=1.0)
    assert _values(accessory, 0) == {
        "power": 10.0,
        "energy_from": 0.0,
        "energy_to": 0.0,
        "soc": None,
        "fault": 0,
    }


def test_max_64_nodes_supported(hap_driver: AccessoryDriver) -> None:
    nodes = [NodeConfig(NodeRole.DEVICE, i, f"D{i}", None, None, f"sensor.p{i}") for i in range(64)]
    accessory = _build(hap_driver, nodes)
    assert len(_services(_hap(accessory), SERVICE_ENERGY_NODE)) == 64
    assert accessory.node_indices() == list(range(64))


# --- history (§13) -----------------------------------------------------------


def test_history_characteristics_are_data_read_only(hap_driver: AccessoryDriver) -> None:
    accessory = _build(hap_driver, [GRID])
    chars = _chars(_services(_hap(accessory), SERVICE_ENERGY_NODE)[0])
    for key in (CHAR_ENERGY_HISTORY, CHAR_POWER_HISTORY):
        char = chars[_u(key)]
        assert char["format"] == "data"
        assert char["perms"] == ["pr"]
        assert char["value"] == ""  # empty until the first rebuild


@pytest.mark.parametrize(
    ("node", "energy", "power"),
    [
        (GRID, True, True),
        (SOLAR, True, True),
        (NodeConfig(NodeRole.DEVICE, 0, "Plug", "sensor.plug_e"), True, False),
        (NodeConfig(NodeRole.HOME, 0, "Home", None, None, "sensor.home_p"), False, True),
        (NodeConfig(NodeRole.GAS, 0, "Gas"), False, False),
        # External statistics: no live energy, but history from the recorder (§13.4).
        (
            NodeConfig(NodeRole.SOLAR, 0, "PV", None, None, None, energy_from_statistic="pv:e"),
            True,
            False,
        ),
    ],
)
def test_history_presence_follows_sources(
    hap_driver: AccessoryDriver, node: NodeConfig, energy: bool, power: bool
) -> None:
    accessory = _build(hap_driver, [node])
    chars = _chars(_services(_hap(accessory), SERVICE_ENERGY_NODE)[0])
    assert (_u(CHAR_ENERGY_HISTORY) in chars) is energy
    assert (_u(CHAR_POWER_HISTORY) in chars) is power


def test_set_history_publishes_base64_within_size_limits(hap_driver: AccessoryDriver) -> None:
    accessory = _build(hap_driver, [GRID, SOLAR])
    start = aligned_start(1_700_000_000, ENERGY_HISTORY_BUCKET_SECONDS, ENERGY_HISTORY_BUCKET_COUNT)
    energy_blob = pack_history(
        HISTORY_KIND_ENERGY,
        ENERGY_HISTORY_BUCKET_SECONDS,
        start,
        [[1.0] * ENERGY_HISTORY_BUCKET_COUNT, [None] * ENERGY_HISTORY_BUCKET_COUNT],
    )
    power_start = aligned_start(
        1_700_000_000, POWER_HISTORY_BUCKET_SECONDS, POWER_HISTORY_BUCKET_COUNT
    )
    power_blob = pack_history(
        HISTORY_KIND_POWER,
        POWER_HISTORY_BUCKET_SECONDS,
        power_start,
        [[5.0] * POWER_HISTORY_BUCKET_COUNT],
    )
    assert len(energy_blob) == ENERGY_HISTORY_MAX_BYTES == 5772
    assert len(power_blob) == POWER_HISTORY_MAX_BYTES == 396

    accessory.set_history(0, energy_blob, power_blob)
    grid = accessory.nodes[0]
    assert base64.b64decode(grid.energy_history.get_value()) == energy_blob
    assert base64.b64decode(grid.power_history.get_value()) == power_blob
    # HAP JSON carries the base64 text unchanged.
    chars = _chars(_services(_hap(accessory), SERVICE_ENERGY_NODE)[0])
    assert chars[_u(CHAR_ENERGY_HISTORY)]["value"] == grid.energy_history.get_value()

    # None leaves a blob untouched; unknown indices are ignored.
    accessory.set_history(0, None, None)
    assert base64.b64decode(grid.energy_history.get_value()) == energy_blob
    accessory.set_history(99, energy_blob, power_blob)
    solar = accessory.nodes[1]
    assert solar.energy_history.get_value() == ""
