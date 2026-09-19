"""Constants for the Controller Energy Bridge integration.

UUIDs, roles, ranges and thresholds mirror docs/protocol.md (schema version 1).
"""

from __future__ import annotations

from enum import IntEnum
from uuid import UUID

DOMAIN = "controller_energy_bridge"

SCHEMA_VERSION = 1
SOURCE_ID = "home-assistant"

ACCESSORY_NAME = "Controller Energy"
MANUFACTURER = "Acasa Software"
MODEL = "Controller Energy Bridge"

DEFAULT_PORT = 21066
DEFAULT_UPDATE_INTERVAL = 5
MIN_UPDATE_INTERVAL = 1
MAX_UPDATE_INTERVAL = 300
MAX_NODES = 64

PERSIST_FILE = ".controller_energy_bridge.state"
NOTIFICATION_ID = f"{DOMAIN}_pairing"

# Config entry keys
CONF_PORT = "port"
CONF_UPDATE_INTERVAL = "update_interval"
CONF_NODES = "nodes"
CONF_PUBLISHED_SIGNATURE = "published_signature"

# Node dict keys (serialised NodeConfig)
CONF_ROLE = "role"
CONF_INDEX = "index"
CONF_NAME = "name"
CONF_NAME_IS_DEFAULT = "name_is_default"
CONF_ENERGY_FROM = "energy_from_entity"
CONF_ENERGY_TO = "energy_to_entity"
CONF_POWER = "power_entity"
CONF_SOC = "soc_entity"
CONF_CAPACITY = "capacity"
CONF_INVERTED = "inverted"
CONF_UNREADABLE = "unreadable_statistics"
CONF_ENERGY_FROM_STATISTIC = "energy_from_statistic"
CONF_ENERGY_TO_STATISTIC = "energy_to_statistic"

# Flow-only keys
CONF_CONFIRM = "confirm"
CONF_HOME_POWER = "home_power_entity"
CONF_HOME_ENERGY = "home_energy_entity"


class NodeRole(IntEnum):
    """Energy node roles (protocol §6)."""

    GRID = 1
    SOLAR = 2
    BATTERY = 3
    HOME = 4
    DEVICE = 5
    GAS = 6
    WATER = 7


# Roles where Power is a required characteristic (§6).
POWER_REQUIRED_ROLES = frozenset(
    {NodeRole.GRID, NodeRole.SOLAR, NodeRole.BATTERY, NodeRole.HOME, NodeRole.DEVICE}
)
# Roles where the bridge clamps negative Power to 0 (§7).
POWER_NON_NEGATIVE_ROLES = frozenset({NodeRole.SOLAR, NodeRole.HOME, NodeRole.DEVICE})
# Roles that carry an Energy To meter (§6).
ENERGY_TO_ROLES = frozenset({NodeRole.GRID, NodeRole.BATTERY})

ROLE_ORDER = {
    NodeRole.GRID: 0,
    NodeRole.SOLAR: 1,
    NodeRole.BATTERY: 2,
    NodeRole.HOME: 3,
    NodeRole.DEVICE: 4,
    NodeRole.GAS: 5,
    NodeRole.WATER: 6,
}

_SUFFIX = "-ACA5-4C0E-8E5F-2D0E71F0E5B1"
_APPLE_SUFFIX = "-0000-1000-8000-0026BB765291"


def _controller_uuid(number: int) -> UUID:
    return UUID(f"{number:08X}{_SUFFIX}")


def _apple_uuid(number: int) -> UUID:
    return UUID(f"{number:08X}{_APPLE_SUFFIX}")


# Services (§5)
SERVICE_ENERGY_BRIDGE_INFO = _controller_uuid(0x0001)
SERVICE_ENERGY_NODE = _controller_uuid(0x0002)

# Info characteristics (§5.1)
CHAR_SCHEMA_VERSION = _controller_uuid(0x0101)
CHAR_UPDATE_INTERVAL = _controller_uuid(0x0102)
CHAR_SOURCE = _controller_uuid(0x0103)

# Node characteristics (§5.2)
CHAR_NODE_ROLE = _controller_uuid(0x0111)
CHAR_NODE_INDEX = _controller_uuid(0x0112)
CHAR_POWER = _controller_uuid(0x0113)
CHAR_ENERGY_FROM = _controller_uuid(0x0114)
CHAR_ENERGY_TO = _controller_uuid(0x0115)
CHAR_STATE_OF_CHARGE = _controller_uuid(0x0116)
CHAR_CAPACITY = _controller_uuid(0x0117)
CHAR_SOURCE_ENTITY = _controller_uuid(0x0118)
CHAR_ENERGY_HISTORY = _controller_uuid(0x0119)
CHAR_POWER_HISTORY = _controller_uuid(0x011A)

# Apple characteristics reused (§5.2)
CHAR_NAME = _apple_uuid(0x0023)
CHAR_STATUS_FAULT = _apple_uuid(0x0077)
SERVICE_ACCESSORY_INFORMATION = _apple_uuid(0x003E)

# Ranges (§5)
POWER_MIN = -1_000_000.0
POWER_MAX = 1_000_000.0
POWER_STEP = 1.0
ENERGY_MIN = 0.0
ENERGY_MAX = 1_000_000_000.0
ENERGY_STEP = 0.001
SOC_MIN = 0.0
SOC_MAX = 100.0
SOC_STEP = 0.1
CAPACITY_MIN = 0.0
CAPACITY_MAX = 100_000.0
CAPACITY_STEP = 0.01
NAME_MAX_LEN = 64
SOURCE_MAX_LEN = 32
SOURCE_ENTITY_MAX_LEN = 128

# Publish thresholds (§9)
THRESHOLD_POWER_W = 1.0
THRESHOLD_ENERGY_KWH = 0.001
THRESHOLD_SOC_PERCENT = 0.5

STATUS_FAULT_NONE = 0
STATUS_FAULT_GENERAL = 1

# History blobs (§13)
HISTORY_BLOB_VERSION = 1
HISTORY_KIND_ENERGY = 1
HISTORY_KIND_POWER = 2
HISTORY_HEADER_SIZE = 12
ENERGY_HISTORY_BUCKET_SECONDS = 3600
ENERGY_HISTORY_BUCKET_COUNT = 720
ENERGY_HISTORY_MAX_BYTES = HISTORY_HEADER_SIZE + ENERGY_HISTORY_BUCKET_COUNT * 4 * 2
POWER_HISTORY_BUCKET_SECONDS = 900
POWER_HISTORY_BUCKET_COUNT = 96
POWER_HISTORY_MAX_BYTES = HISTORY_HEADER_SIZE + POWER_HISTORY_BUCKET_COUNT * 4
# Rebuild schedule: energy at :15 after HA compiled the hour, power every bucket width.
ENERGY_HISTORY_REBUILD_MINUTE = 15
POWER_HISTORY_REBUILD_SECONDS = POWER_HISTORY_BUCKET_SECONDS
