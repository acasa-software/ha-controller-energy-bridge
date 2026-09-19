"""Mapping of Energy dashboard preferences to nodes (protocol §11)."""

from __future__ import annotations

from dataclasses import replace

from custom_components.controller_energy_bridge.const import NodeRole
from custom_components.controller_energy_bridge.energy_import import (
    NodeConfig,
    build_nodes_from_prefs,
    is_entity_id,
    merge_user_settings,
    reindex,
)

from .conftest import REFERENCE_PREFS


def test_reference_installation() -> None:
    nodes = build_nodes_from_prefs(REFERENCE_PREFS)
    assert [(n.role, n.index, n.name) for n in nodes] == [
        (NodeRole.GRID, 0, "Netz"),
        (NodeRole.SOLAR, 1, "Solar"),
        (NodeRole.BATTERY, 2, "Speicher"),
        (NodeRole.DEVICE, 3, "Well pump"),
    ]
    grid, solar, battery, device = nodes
    assert grid.energy_from_entity == "sensor.grid_import_energy"
    assert grid.energy_to_entity == "sensor.grid_export_energy"
    assert grid.power_entity is None
    assert solar.power_entity == "sensor.pv_power"
    assert solar.energy_to_entity is None
    assert battery.soc_entity == "sensor.battery_soc"
    assert battery.capacity == 10.0
    assert device.energy_from_entity == "sensor.well_pump_energy"
    assert device.role is NodeRole.DEVICE
    assert all(not n.unreadable_statistics for n in nodes)


def test_empty_or_missing_prefs() -> None:
    assert build_nodes_from_prefs(None) == []
    assert build_nodes_from_prefs({}) == []
    assert build_nodes_from_prefs({"energy_sources": [], "device_consumption": []}) == []


def test_legacy_grid_format() -> None:
    prefs = {
        "energy_sources": [
            {
                "type": "grid",
                "flow_from": [
                    {"stat_energy_from": "sensor.import_a", "stat_cost": None},
                    {"stat_energy_from": "sensor.import_b", "stat_cost": None},
                ],
                "flow_to": [{"stat_energy_to": "sensor.export_a", "stat_compensation": None}],
                "power": [{"stat_rate": "sensor.grid_power"}],
                "cost_adjustment_day": 0,
            }
        ],
        "device_consumption": [],
    }
    nodes = build_nodes_from_prefs(prefs, {"sensor.import_a": "Hauptzähler"})
    assert len(nodes) == 2
    assert nodes[0].energy_from_entity == "sensor.import_a"
    assert nodes[0].energy_to_entity == "sensor.export_a"
    assert nodes[0].power_entity == "sensor.grid_power"
    assert nodes[0].name == "Grid"
    assert nodes[1].energy_from_entity == "sensor.import_b"
    assert nodes[1].energy_to_entity is None
    assert nodes[1].power_entity is None
    assert nodes[1].name == "Grid 2"
    assert all(n.name_is_default for n in nodes)
    assert [n.index for n in nodes] == [0, 1]


def test_power_config_inverted_and_two_sensor() -> None:
    prefs = {
        "energy_sources": [
            {
                "type": "battery",
                "stat_energy_from": "sensor.dis",
                "stat_energy_to": "sensor.chg",
                "power_config": {"stat_rate_inverted": "sensor.bat_power_inv"},
            },
            {
                "type": "grid",
                "stat_energy_from": "sensor.imp",
                "stat_energy_to": None,
                "power_config": {"stat_rate_from": "sensor.a", "stat_rate_to": "sensor.b"},
            },
        ]
    }
    battery = next(n for n in build_nodes_from_prefs(prefs) if n.role is NodeRole.BATTERY)
    assert battery.power_entity == "sensor.bat_power_inv"
    assert battery.inverted is True
    grid = next(n for n in build_nodes_from_prefs(prefs) if n.role is NodeRole.GRID)
    # Two-sensor configs have no single live entity; HA provides stat_rate for those.
    assert grid.power_entity is None


def test_external_statistics_are_unreadable() -> None:
    prefs = {
        "energy_sources": [
            {"type": "solar", "stat_energy_from": "pvoutput:daily_yield"},
            {
                "type": "grid",
                "stat_energy_from": "meter:import",
                "stat_energy_to": "sensor.export",
            },
        ],
        "device_consumption": [{"stat_consumption": "shelly:plug_1"}],
    }
    nodes = build_nodes_from_prefs(prefs)
    grid, solar, device = nodes
    assert grid.energy_from_entity is None
    assert grid.energy_to_entity == "sensor.export"
    assert grid.unreadable_statistics == ("meter:import",)
    assert grid.energy_from_statistic == "meter:import"
    assert grid.history_energy_from == "meter:import"
    assert solar.energy_from_entity is None
    assert solar.unreadable_statistics == ("pvoutput:daily_yield",)
    assert solar.energy_from_statistic == "pvoutput:daily_yield"
    assert solar.name == "Solar"
    assert device.unreadable_statistics == ("shelly:plug_1",)
    assert device.energy_from_statistic == "shelly:plug_1"
    assert device.history_energy_to is None
    assert device.name == "Device"


def test_gas_and_water_are_kept_with_reserved_roles() -> None:
    prefs = {
        "energy_sources": [
            {"type": "water", "stat_energy_from": "sensor.water", "stat_rate": "sensor.flow"},
            {"type": "gas", "stat_energy_from": "sensor.gas", "stat_cost": None},
            {"type": "solar", "stat_energy_from": "sensor.pv"},
        ]
    }
    nodes = build_nodes_from_prefs(prefs)
    assert [n.role for n in nodes] == [NodeRole.SOLAR, NodeRole.GAS, NodeRole.WATER]
    assert nodes[2].power_entity == "sensor.flow"


def test_unknown_source_type_is_ignored() -> None:
    prefs = {"energy_sources": [{"type": "fusion", "stat_energy_from": "sensor.x"}]}
    assert build_nodes_from_prefs(prefs) == []


def test_role_order_is_stable_within_role() -> None:
    prefs = {
        "energy_sources": [
            {"type": "solar", "stat_energy_from": "sensor.pv_b", "name": "B"},
            {"type": "grid", "stat_energy_from": "sensor.g", "stat_energy_to": None},
            {"type": "solar", "stat_energy_from": "sensor.pv_a", "name": "A"},
        ],
        "device_consumption": [
            {"stat_consumption": "sensor.d2", "name": "D2"},
            {"stat_consumption": "sensor.d1", "name": "D1"},
        ],
    }
    names = [n.name for n in build_nodes_from_prefs(prefs)]
    assert names == ["Grid", "B", "A", "D2", "D1"]


def test_default_role_names_ignore_friendly_names() -> None:
    prefs = {
        "energy_sources": [
            {"type": "grid", "stat_energy_from": "sensor.grid_import", "stat_energy_to": None},
            {"type": "solar", "stat_energy_from": "sensor.pv_yield"},
            {"type": "solar", "stat_energy_from": "sensor.pv_yield_2"},
            {"type": "battery", "stat_energy_from": "sensor.dis", "stat_energy_to": "sensor.chg"},
            {"type": "gas", "stat_energy_from": "sensor.gas", "stat_cost": None},
            {"type": "water", "stat_energy_from": "sensor.water", "stat_cost": None},
        ],
        "device_consumption": [{"stat_consumption": "sensor.plug_energy"}],
    }
    friendly = {
        "sensor.grid_import": "Grid meter import",
        "sensor.pv_yield": 'Inverter {"Hostname":"inv"} Energy Yield Total',
        "sensor.plug_energy": "Steckdose Energie",
    }
    nodes = build_nodes_from_prefs(prefs, friendly)
    assert [n.name for n in nodes] == [
        "Grid",
        "Solar",
        "Solar 2",
        "Battery",
        "Steckdose Energie",
        "Gas",
        "Water",
    ]
    assert all(n.name_is_default for n in nodes)


def test_dashboard_names_are_not_numbered() -> None:
    prefs = {
        "energy_sources": [
            {"type": "solar", "stat_energy_from": "sensor.a", "name": "Dach"},
            {"type": "solar", "stat_energy_from": "sensor.b", "name": "Dach"},
            {"type": "solar", "stat_energy_from": "sensor.c"},
        ]
    }
    nodes = build_nodes_from_prefs(prefs)
    assert [(n.name, n.name_is_default) for n in nodes] == [
        ("Dach", False),
        ("Dach", False),
        ("Solar", True),
    ]


def test_is_entity_id() -> None:
    assert is_entity_id("sensor.grid_power")
    assert not is_entity_id("pvoutput:daily_yield")
    assert not is_entity_id("sensor.Grid")
    assert not is_entity_id(None)
    assert not is_entity_id("")


def test_round_trip_dict() -> None:
    node = NodeConfig(
        role=NodeRole.BATTERY,
        index=2,
        name="Speicher",
        energy_from_entity="sensor.dis",
        energy_to_entity="sensor.chg",
        power_entity="sensor.p",
        soc_entity="sensor.soc",
        capacity=10.5,
        inverted=True,
        unreadable_statistics=("ext:one",),
        energy_to_statistic="ext:one",
    )
    data = node.to_dict()
    assert data["role"] == 3
    assert data["unreadable_statistics"] == ["ext:one"]
    assert data["energy_to_statistic"] == "ext:one"
    assert node.history_energy_from == "sensor.dis"
    assert node.history_energy_to == "sensor.chg"  # live entity wins over the external id
    assert NodeConfig.from_dict(data) == node
    legacy = NodeConfig.from_dict({"role": 2, "index": 0, "name": "PV"})
    assert legacy.power_entity is None
    # Entries stored before the flag existed carry generated names: let a re-import fix them.
    assert legacy.name_is_default is True


def test_merge_user_settings_keeps_user_choices_and_home() -> None:
    previous = [
        NodeConfig(
            NodeRole.GRID, 0, "Netz", "sensor.imp", "sensor.exp", "sensor.grid_p", inverted=True
        ),
        NodeConfig(
            NodeRole.BATTERY,
            1,
            "Speicher",
            "sensor.dis",
            "sensor.chg",
            "sensor.bat_p",
            "sensor.soc",
            10.0,
        ),
        NodeConfig(NodeRole.HOME, 2, "Home", "sensor.home_e", None, "sensor.home_p"),
        NodeConfig(NodeRole.DEVICE, 3, "Gone", "sensor.gone"),
    ]
    fresh = [
        NodeConfig(NodeRole.GRID, 0, "Netz neu", "sensor.imp", "sensor.exp"),
        NodeConfig(NodeRole.BATTERY, 1, "Speicher", "sensor.dis", "sensor.chg", "sensor.dash_p"),
        NodeConfig(NodeRole.DEVICE, 2, "New", "sensor.new"),
    ]
    merged = merge_user_settings(fresh, previous)
    assert [(n.role, n.index, n.name) for n in merged] == [
        (NodeRole.GRID, 0, "Netz neu"),
        (NodeRole.BATTERY, 1, "Speicher"),
        (NodeRole.HOME, 2, "Home"),
        (NodeRole.DEVICE, 3, "New"),
    ]
    grid, battery, home, device = merged
    assert grid.power_entity == "sensor.grid_p"
    assert grid.inverted is True
    # Dashboard-provided power wins, and its (non-)inversion with it.
    assert battery.power_entity == "sensor.dash_p"
    assert battery.inverted is False
    assert battery.soc_entity == "sensor.soc"
    assert battery.capacity == 10.0
    assert home.power_entity == "sensor.home_p"
    assert device.power_entity is None


def test_merge_user_settings_name_precedence() -> None:
    previous = [
        NodeConfig(NodeRole.GRID, 0, "Hausanschluss", "sensor.imp", name_is_default=False),
        NodeConfig(NodeRole.SOLAR, 1, "Solar", "sensor.pv", name_is_default=True),
        NodeConfig(NodeRole.BATTERY, 2, "Akku", "sensor.dis", "sensor.chg", name_is_default=False),
    ]
    fresh = [
        NodeConfig(NodeRole.GRID, 0, "Grid", "sensor.imp", name_is_default=True),
        NodeConfig(NodeRole.SOLAR, 1, "Solar", "sensor.pv", name_is_default=True),
        NodeConfig(NodeRole.BATTERY, 2, "Speicher", "sensor.dis", "sensor.chg"),
    ]
    merged = merge_user_settings(fresh, previous)
    assert [(n.name, n.name_is_default) for n in merged] == [
        ("Hausanschluss", False),  # user name beats generated default
        ("Solar", True),
        ("Speicher", False),  # dashboard name beats user name
    ]


def test_reindex() -> None:
    nodes = [NodeConfig(NodeRole.SOLAR, 7, "A"), NodeConfig(NodeRole.DEVICE, 3, "B")]
    assert [n.index for n in reindex(nodes)] == [0, 1]
    assert replace(nodes[0], index=0) == reindex(nodes)[0]


def test_external_battery_statistics_keep_direction() -> None:
    prefs = {
        "energy_sources": [
            {
                "type": "battery",
                "stat_energy_from": "bms:discharge",
                "stat_energy_to": "bms:charge",
            }
        ],
        "device_consumption": [],
    }
    (battery,) = build_nodes_from_prefs(prefs)
    assert battery.unreadable_statistics == ("bms:discharge", "bms:charge")
    assert battery.history_energy_from == "bms:discharge"
    assert battery.history_energy_to == "bms:charge"
