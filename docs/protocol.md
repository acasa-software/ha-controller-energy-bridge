# Controller Energy Bridge Protocol

**Schema version: 1** (additive extension §13: history) · Status: 1.0

This document is the contract between the Home Assistant integration ("the bridge") and Controller for HomeKit ("the app"). The bridge exposes the Home Assistant Energy dashboard as a single HomeKit accessory with Controller-specific services and characteristics. The app renders an energy-flow view from it. Both sides are implemented and tested against this document; neither side relies on behaviour that is not written here.

This document in the `ha-controller-energy-bridge` repository is the authoritative version. Controller for HomeKit keeps a copy of it in the app repository.

## 1. Scope

In scope for schema version 1:

- Live power per energy node (grid, solar, battery, home, device).
- Cumulative energy meter readings per node (kWh).
- Battery state of charge and capacity.
- Per-node availability.

Out of scope for schema version 1 (roles or fields are reserved where noted):

- Cost, tariffs. (History: only the bounded, pre-aggregated form of §13.)
- Device hierarchy (Home Assistant `included_in_stat`).
- Gas and water: roles are reserved and the bridge may expose them; the app ignores them.

## 2. Transport

Standard HomeKit Accessory Protocol (HAP) over IP. The bridge runs its own HAP server, separate from the Home Assistant core HomeKit Bridge, and pairs as one accessory. Remote access works through the user's HomeKit home hub like any other accessory; nothing in this protocol depends on the network path.

Custom characteristic units are not transported by HomeKit (the `unit` field is dropped for non-Apple units). Units are therefore fixed by this document per characteristic.

HAP `float` is 32-bit. Energy meters are expressed in kWh, never Wh, so a 10 MWh meter still resolves to 0.01 kWh.

## 3. Identifiers

All Controller types share one suffix and differ only in the leading 16-bit number, mirroring Apple's layout:

```
0000NNNN-ACA5-4C0E-8E5F-2D0E71F0E5B1
```

| Range | Use |
|---|---|
| `0001`–`00FF` | Services |
| `0101`–`01FF` | Characteristics |

Apple-defined characteristics reused by this protocol keep their Apple UUIDs (`0000XXXX-0000-1000-8000-0026BB765291`).

## 4. Accessory

Exactly one accessory, no HAP bridge, `aid` 1. Category `Other` (1).

Accessory Information service (Apple `0000003E`):

| Characteristic | Value |
|---|---|
| Name | `Controller Energy` (user may rename in HomeKit; the app must not depend on it) |
| Manufacturer | `Acasa Software` |
| Model | `Controller Energy Bridge` |
| Serial Number | First 12 hex characters of the Home Assistant instance UUID |
| Firmware Revision | Bridge integration version (semver) |

**Detection.** The app treats an accessory as an energy bridge if and only if it contains exactly one Energy Bridge Info service (§5.1). Manufacturer and model strings are informational.

Service instance IDs (`iid`) are persisted by the bridge across restarts so that the app's HomeKit cache stays valid. Adding or removing nodes increments the HAP configuration number.

## 5. Services

### 5.1 Energy Bridge Info — `00000001-ACA5-4C0E-8E5F-2D0E71F0E5B1`

Exactly one per accessory. Marked as the primary service.

| Characteristic | UUID | Format | Perms | Value |
|---|---|---|---|---|
| Schema Version | `00000101-…` | uint16 | pr | `1` |
| Update Interval | `00000102-…` | uint16 | pr | Minimum seconds between notifications per characteristic, default `5`, range 1–300 |
| Source | `00000103-…` | string (≤32) | pr | `home-assistant` |

### 5.2 Energy Node — `00000002-ACA5-4C0E-8E5F-2D0E71F0E5B1`

One per energy node. At most 64 per accessory.

| Characteristic | UUID | Format | Perms | Unit | Notes |
|---|---|---|---|---|---|
| Name | Apple `00000023` | string (≤64) | pr | | Display name, from the Energy dashboard or the entity's friendly name |
| Node Role | `00000111-…` | uint8 | pr | | Enum, see §6 |
| Node Index | `00000112-…` | uint8 | pr | | Stable sort key within the accessory, 0-based, unique |
| Power | `00000113-…` | float | pr, ev | W | Signed, see §7. Range −1 000 000 … 1 000 000, step 1 |
| Energy From | `00000114-…` | float | pr, ev | kWh | Cumulative, monotonically non-decreasing except on meter reset. Range 0 … 1 000 000 000, step 0.001 |
| Energy To | `00000115-…` | float | pr, ev | kWh | Same as Energy From, opposite direction |
| State Of Charge | `00000116-…` | float | pr, ev | % | 0 … 100, step 0.1 |
| Capacity | `00000117-…` | float | pr | kWh | 0 … 100 000, step 0.01 |
| Status Fault | Apple `00000077` | uint8 | pr, ev | | `0` no fault, `1` general fault (§8) |
| Source Entity | `00000118-…` | string (≤128) | pr | | Home Assistant entity id feeding **Power**; diagnostic only |
| Energy History | `00000119-…` | data | pr | | 30 days of hourly energy, §13 |
| Power History | `0000011A-…` | data | pr | | 24 hours of 15-minute mean power, §13 |

The set of characteristics present depends on the role (§6). Characteristics that are not present are simply omitted from the service; the bridge never publishes placeholder values such as `0` for data it does not have.

## 6. Node roles

| Value | Role | Required | Optional | App v1 |
|---|---|---|---|---|
| `1` | Grid | Name, Node Role, Node Index, Status Fault, Power | Energy From (import), Energy To (export), Source Entity | shown |
| `2` | Solar | Name, Node Role, Node Index, Status Fault, Power | Energy From (production), Source Entity | shown |
| `3` | Battery | Name, Node Role, Node Index, Status Fault, Power | Energy From (discharge), Energy To (charge), State Of Charge, Capacity, Source Entity | shown |
| `4` | Home | Name, Node Role, Node Index, Status Fault, Power | Energy From (consumption), Source Entity | shown |
| `5` | Device | Name, Node Role, Node Index, Status Fault, Power | Energy From (consumption), Source Entity | shown |
| `6` | Gas | Name, Node Role, Node Index, Status Fault | Energy From (m³ or kWh as configured in HA; unit ambiguity is why the app ignores it), Power (flow rate) | ignored |
| `7` | Water | Name, Node Role, Node Index, Status Fault | Energy From (L), Power (flow rate) | ignored |

Rules:

- The app hides nodes with unknown role values.
- At most one Grid, one Solar, one Battery and one Home node are meaningful for the flow view. If the bridge exposes several nodes of one of these roles (several grid meters, two inverters), the app sums their Power for the flow arrows and lists them individually in detail views.
- **Home is optional.** If no Home node is present, the app derives home consumption as `grid + solar + battery` using the sign conventions of §7, clamped at 0. The bridge never computes a Home node itself; it exposes one only when Home Assistant has a real sensor for it.
- Device nodes are independent consumers below Home. The app does not verify that they sum to Home.

## 7. Sign conventions

Home Assistant's conventions are adopted unchanged so that the bridge forwards sensor values as they are.

| Role | Power > 0 | Power < 0 |
|---|---|---|
| Grid | importing from grid | exporting to grid |
| Solar | producing | never (bridge clamps to 0) |
| Battery | discharging into the home | charging |
| Home | consuming | never (bridge clamps to 0) |
| Device | consuming | never (bridge clamps to 0) |

If the user's Home Assistant sensor uses the inverted convention, the bridge offers an "inverted" toggle per node in its configuration and normalises before publishing. The app never inverts.

Energy From / Energy To follow the same directions: for Grid, From is import and To is export; for Battery, From is discharge and To is charge.

## 8. Availability

`Status Fault` is `1` while the node's Power source is unavailable, unknown, or non-numeric in Home Assistant, and `0` otherwise. While faulted, the bridge keeps publishing the last known Power and energy values so the app can render them dimmed rather than blank.

If the bridge itself is down, HomeKit reports the accessory as unreachable; the app treats that as all nodes faulted.

## 9. Update behaviour

- The bridge reads sources on Home Assistant state changes and publishes at most once per `Update Interval` per characteristic.
- A value is published only if it changed by at least the threshold: Power 1 W, Energy 0.001 kWh, State Of Charge 0.5 %. Status Fault publishes on every transition.
- Name, Node Role, Node Index, Capacity and Source Entity are static for the lifetime of a configuration. Changing them (rename, reorder, remove a node) is a configuration change: the bridge rebuilds the service list and increments the HAP configuration number.
- Energy From / Energy To may decrease after a meter reset in Home Assistant. The app must not assume monotonicity when computing deltas; it treats a decrease as a reset and starts a new interval.

## 10. Versioning

- `Schema Version` is a single integer. Version 1 is this document.
- Additive changes (new optional characteristics, new role values, new services) do not change the version. The app ignores what it does not know; the bridge always publishes the required characteristics of §6.
- Only incompatible changes (changed semantics of an existing characteristic, changed sign convention, removed required characteristic) increment the version. The app supports the versions listed in its release notes and shows a "bridge update required" or "app update required" hint otherwise.
- Reserved and never reused: UUID numbers of removed characteristics.

## 11. Mapping from the Home Assistant Energy dashboard

Informative. The bridge's configuration flow imports the Energy dashboard preferences and maps them as follows; power sources are asked for separately because the dashboard rarely contains them.

| Energy dashboard | Role | Energy From | Energy To | Power |
|---|---|---|---|---|
| `energy_sources[type=grid]` | Grid | `stat_energy_from` | `stat_energy_to` | `stat_rate` if set, else user-selected |
| `energy_sources[type=solar]` | Solar | `stat_energy_from` | | `stat_rate` if set, else user-selected |
| `energy_sources[type=battery]` | Battery | `stat_energy_from` | `stat_energy_to` | `stat_rate` if set, else user-selected; SoC from `stat_soc` or user-selected |
| `device_consumption[]` | Device | `stat_consumption` | | `stat_rate` if set, else user-selected or omitted |
| not in the dashboard | Home | user-selected | | user-selected |

Statistic ids that are not entity ids (external statistics, `domain:object`) cannot be read live and are exposed with Status Fault `1` and without Energy characteristics.

## 12. Example

One accessory, four nodes (example values):

| iid | Service | Name | Role | Index | Power | Energy From | Energy To | SoC |
|---|---|---|---|---|---|---|---|---|
| 10 | Energy Bridge Info | | | | | | | |
| 20 | Energy Node | Grid | 1 | 0 | −1500 W | 500.0 kWh | 1200.0 kWh | |
| 30 | Energy Node | Solar | 2 | 1 | 2500 W | 15 000.0 kWh | | |
| 40 | Energy Node | Battery | 3 | 2 | −400 W | 450.0 kWh | 520.0 kWh | 55.0 % |
| 50 | Energy Node | Well pump | 5 | 3 | 0 W | 120.0 kWh | | |

No Home node: the app derives home = −1500 + 2500 + (−400) = 600 W.

## 13. History (additive, schema version 1)

Home Assistant already keeps aggregated statistics for every energy and power sensor (long-term hourly sums, short-term 5-minute means). The bridge publishes a bounded window of them as two read-only `data` characteristics per node, so the app gets a recent history without any live stream and without its own backend. Both are optional; a bridge that cannot produce them omits them. They have no `ev` permission: the app reads them on demand (when the history view opens, at most once per bucket width) instead of subscribing.

### 13.1 Blob layout

Little-endian, no padding.

| Offset | Type | Field |
|---|---|---|
| 0 | uint8 | Blob version, `1` |
| 1 | uint8 | Kind: `1` energy, `2` power |
| 2 | uint8 | Series count `S` (1 or 2) |
| 3 | uint8 | Reserved, `0` |
| 4 | uint16 | Bucket width in seconds |
| 6 | uint32 | Unix time (UTC) of the **start** of the first bucket, aligned to the bucket width |
| 10 | uint16 | Bucket count `N` |
| 12 | float32 × N × S | Values, series-major: all `N` values of series 1, then all of series 2 |

A value is `NaN` when the bucket has no data (sensor unavailable, statistics not yet compiled, bucket older than the recorder keeps). Buckets are contiguous: bucket `i` starts at `start + i × width`. The last bucket is the one containing "now" and is partial.

### 13.2 Energy History

- Width 3600 s, `N` = 720 (30 days). Value = energy delivered **within** the bucket in kWh (the difference of the cumulative meter, i.e. Home Assistant's long-term statistics `change`), never the meter reading itself.
- Series 1 = the **Energy From** direction, series 2 = **Energy To** (only for nodes that publish Energy To: grid, battery). `S` therefore matches the node's energy characteristics.
- Rebuilt once per hour after Home Assistant has compiled the hour's statistics (they are compiled a few minutes past the hour; the bridge rebuilds at :15).
- Maximum size: 12 + 720 × 4 × 2 = 5772 bytes.

### 13.3 Power History

- Width 900 s, `N` = 96 (24 hours). Value = mean power in W over the bucket, with the node's sign convention (§7), derived from the short-term 5-minute statistics `mean` of the Power source (three 5-minute means averaged; missing ones ignored). `S` = 1.
- Rebuilt every 15 minutes.
- Size: 12 + 96 × 4 = 396 bytes.

### 13.4 Sources and edge cases

- Sources are the same statistic ids as the live characteristics. Because statistics are read from the recorder, **external statistics** (`domain:object`, §11) are readable here even though they have no live value; such a node publishes Energy History but keeps Status Fault `1` for the live values.
- If the recorder keeps fewer than 30 days, the missing head is `NaN`, `N` stays 720.
- Nodes without Power source omit Power History; nodes without energy characteristics omit Energy History.
- A meter reset shows as an ordinary bucket: the statistics `change` is computed by Home Assistant with reset handling, so the app does not need §9's reset logic for history values.
- The app never derives history for a missing Home node (§6): home history is shown only when the bridge publishes a Home node with history.

### 13.5 Reading

`data` values travel base64-encoded in HAP; iOS delivers them as `NSData`. The app reads with a plain characteristic read; a stale cached value is acceptable within one bucket width. Reads go through the home hub when remote, so a full set for a six-node home is under 25 KB.
