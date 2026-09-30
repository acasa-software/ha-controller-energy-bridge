# Controller Energy Bridge

[![CI](https://github.com/acasa-software/ha-controller-energy-bridge/actions/workflows/ci.yml/badge.svg)](https://github.com/acasa-software/ha-controller-energy-bridge/actions/workflows/ci.yml)
[![Validate](https://github.com/acasa-software/ha-controller-energy-bridge/actions/workflows/validate.yml/badge.svg)](https://github.com/acasa-software/ha-controller-energy-bridge/actions/workflows/validate.yml)
[![HACS](https://img.shields.io/badge/HACS-Custom-41BDF5.svg)](https://github.com/hacs/integration)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

<!-- TODO: open the pull request in home-assistant/brands with brand/icon.png and brand/icon@2x.png (see CONTRIBUTING.md), then drop `ignore: brands` from .github/workflows/validate.yml. -->

Home Assistant custom integration that exposes the **Energy dashboard** as a single HomeKit
accessory for **Controller for HomeKit** (Acasa Software). The app renders an energy-flow view
(grid, solar, battery, home, devices) from live power and cumulative energy meters.

The wire format is defined in [`docs/protocol.md`](docs/protocol.md) (schema version 1). This
repository holds the authoritative version; the Controller for HomeKit app keeps a copy.

## How it works

The integration reads the sources configured in the Home Assistant Energy dashboard and turns
each of them into an energy node with live power and cumulative energy. It runs its own small
HomeKit accessory server inside Home Assistant, independent of the core HomeKit Bridge, and
publishes the nodes as one accessory. Controller for HomeKit pairs with that accessory and
renders the energy-flow view and history from it.

<!-- TODO: screenshots (config flow, pairing notification, energy-flow view in Controller for HomeKit) -->

> **Apple Home shows the accessory as "Not Supported".** That is expected. The bridge uses
> Controller-specific HomeKit services that only Controller for HomeKit understands. Pair it in
> Controller for HomeKit, not in the Home app.

## Requirements

- **Controller for HomeKit 10.1 or newer** on iPhone or iPad. Older versions pair the bridge but
  show its values only as plain characteristics, without the energy-flow view or history. See
  [Compatibility](#compatibility).
- Home Assistant 2025.6 or newer (developed and tested against 2026.9).
- A configured Energy dashboard (Settings > Dashboards > Energy).
- Power sensors (`device_class: power`) for the nodes you want to see live.
- A free TCP port for the bridge's own HomeKit server (default `21066`). The bridge runs
  independently of the core HomeKit Bridge integration; both can coexist.

## Installation via HACS

1. HACS > Integrations > three-dot menu > **Custom repositories**.
2. Add this repository URL, category **Integration**.
3. Install **Controller Energy Bridge** and restart Home Assistant.

Manual installation: copy `custom_components/controller_energy_bridge/` into
`<config>/custom_components/` and restart.

## Configuration

Settings > Devices & services > **Add integration** > *Controller Energy Bridge*.

1. **Import Energy dashboard.** The flow lists every node it found (grid, solar, battery,
   devices, gas, water) with its kWh source. Choose the HomeKit port and the update interval
   (minimum seconds between HomeKit notifications per value, default 5).
2. **Power source per node.** Optionally give the node a name (sources without a name in the
   Energy dashboard default to the English role name: Grid, Solar, Battery, Gas, Water, numbered
   when a role appears more than once; devices fall back to the sensor's friendly name). Then
   pick the sensor that reports live power in W or kW.
   The field is pre-filled with the dashboard's `stat_rate` if present, otherwise with the first
   power sensor on the same device as the kWh sensor. Tick **Inverted sign** if your sensor uses
   the opposite convention (see below). Battery nodes additionally take a state-of-charge sensor
   and an optional capacity in kWh.
3. **Home consumption (optional).** Only select sensors here if Home Assistant has a *real*
   total-consumption sensor. Leave both fields empty and the app derives home consumption from
   grid, solar and battery.

The integration is a single instance. Re-run the same steps any time via **Configure**
(options flow); the bridge rebuilds its services and bumps the HomeKit configuration number so
paired controllers refresh.

Changes to the Energy dashboard (added or removed sources or devices) are picked up
automatically. User-selected power sensors are carried over by role and kWh entity.

### Sign conventions

Home Assistant's conventions are forwarded unchanged (protocol §7):

| Role | Power > 0 | Power < 0 |
|---|---|---|
| Grid | importing | exporting |
| Battery | discharging | charging |
| Solar / Home / Device | producing / consuming | clamped to 0 |

### Units

Sensor units are normalised from `unit_of_measurement`: W/kW/MW to **W**, Wh/kWh/MWh/GWh (and
J/kJ/MJ/GJ, cal variants) to **kWh**. Unknown units are passed through unchanged.

### Availability

A node's `Status Fault` is `1` while its power sensor is `unavailable`, `unknown` or not
numeric, and permanently `1` if the node has no power sensor. The last known values stay
published so the app can render them dimmed.

Statistic ids that are not entity ids (external statistics like `pvoutput:daily_yield`) cannot
be read live: the node is exposed without energy characteristics and with `Status Fault` `1`.

## Today

Nodes with energy statistics also publish `Energy From Today` and `Energy To Today` (protocol
§14): the energy since local midnight in kWh, the same figure the Energy dashboard shows. The
value is pinned to the recorder at start, at :15 every hour and at 00:01, and in between
follows the live meter, so it is as current as the sensor without a database query per
update. Controller for HomeKit shows it under "Today" on the energy screen and tile.

## History

Each node carries up to two read-only `data` characteristics with pre-aggregated history
(protocol §13), read from the recorder's statistics rather than from a live stream:

| Characteristic | Window | Source | Size |
|---|---|---|---|
| Energy History (`00000119-…`) | 30 days, 720 hourly buckets | long-term statistics `change` of the Energy From / Energy To sensors, in kWh | up to 5772 bytes (two series for grid and battery) |
| Power History (`0000011A-…`) | 24 hours, 96 buckets of 15 minutes | short-term 5-minute statistics `mean` of the power sensor, averaged per bucket, in W with the node's sign convention | 396 bytes |

Energy History is rebuilt at :15 every hour (after Home Assistant has compiled the hour),
Power History every 15 minutes; both are built once at start. Buckets without data are `NaN`,
so a recorder that keeps fewer than 30 days still yields 720 buckets with an empty head.
External statistics (`pvoutput:daily_yield`) have no live value but are readable from the
recorder, so such a node publishes Energy History while keeping `Status Fault` `1`.

The integration depends on the `recorder`; with the recorder unavailable the characteristics
stay empty. Nodes without a power sensor have no Power History, nodes without energy sources
no Energy History. Diagnostics list bucket count, `NaN` count, blob size and the last rebuild
per node.

## Pairing

After the first start the integration creates a persistent notification with the pairing PIN
and the `X-HM://` setup URI. In Controller for HomeKit add a new accessory and enter the code.
The notification disappears once a controller has paired.

Pairing state lives in `<config>/.controller_energy_bridge.state`. Deleting that file un-pairs
the bridge and generates a new PIN.

## Diagnostics

Settings > Devices & services > Controller Energy Bridge > **Download diagnostics** exports the
node configuration, HAP instance ids, last published values and pairing status. No pairing
secrets are included.

## Development

```bash
uv venv --python 3.14 .venv
uv pip install -r requirements_test.txt
.venv/bin/pytest -q
.venv/bin/ruff check custom_components tests
```

Home Assistant 2026.9 requires Python 3.14; `pytest-homeassistant-custom-component` is pinned
to the matching release in `requirements_test.txt`.

### Test coverage

| File | Covers |
|---|---|
| `tests/test_energy_import.py` | Dashboard preferences to nodes: new and legacy grid format, `power_config` variants, external statistics, gas/water, ordering, dict round-trip, merge of user settings across re-imports |
| `tests/test_accessory.py` | HAP structure exactly per protocol: UUIDs, formats, permissions, ranges, required/optional characteristics per role, IID ordering, sign conventions, thresholds, clamping, fault behaviour, 64 nodes, history characteristics (`data`, `pr`, presence per source, base64 value) |
| `tests/test_today.py` | Today's energy per §14: statistics baseline against the live meter, meter reset within the day and between compile and now, midnight restart, meters without a live state |
| `tests/test_history.py` | History blobs per protocol §13: exact byte layout, bucket alignment, hourly `change` series with gaps and unit conversion, 15-minute power means with inversion and clamping |
| `tests/test_units.py` | Unit normalisation and numeric parsing |
| `tests/test_bridge.py` | State decoding, unit handling in the state path, fault rules, per-node throttling with timers, history rebuilds against a mocked recorder (query parameters, external statistic ids, failure keeps the previous blob), today's energy pinned from mocked statistics and followed live |
| `tests/test_config_flow.py` | Config flow happy path, Home node placement, confirmation error, power-sensor suggestion from the device registry, single instance, options flow with carried-over settings, abort without Energy dashboard |
| `tests/test_init.py` | Entry setup with a stubbed pyhap network layer: seeding from current states, configuration-number bump on layout change, pairing notification, diagnostics, state-change forwarding, Energy dashboard follow-up and reload, listener no-op after unload, deferred start until Home Assistant is running, scheduled history rebuilds and their cancellation on unload |

Not covered by automated tests: the actual HAP network layer (mDNS advertisement, pairing,
event delivery) and the `SharedZeroconfDriver.async_stop` behaviour against a live Zeroconf
instance. The driver wiring follows a spike run against Home Assistant 2026.9 with HAP-python
5.0.0; the first deployment should confirm pairing and a clean reload.

## Privacy

All data stays in your local network. The bridge talks HomeKit Accessory Protocol over the LAN
only, uses no cloud service, phones nowhere home and collects no telemetry. Diagnostics
downloads contain node configuration and last values but no pairing secrets.

## Support

- Problems with the integration (setup, import, pairing, values): open an issue in this
  repository using the bug report template and attach the diagnostics download.
- Questions about the Controller for HomeKit app itself: use the app support at
  https://acasa-software.de.
- Security issues: see [SECURITY.md](SECURITY.md).

## Versioning

Releases follow [Semantic Versioning](https://semver.org) and are published as
[GitHub Releases](https://github.com/acasa-software/ha-controller-energy-bridge/releases) from
`vX.Y.Z` tags; `CHANGELOG.md` lists the changes. Beta builds are tagged `vX.Y.Z-beta.N` and
published as pre-releases; enable **Show beta versions** for this repository in HACS to receive
them.

## Compatibility

The bridge and the app speak the protocol in `docs/protocol.md`, identified by its schema version.
Additive changes keep the schema version; only incompatible changes raise it, and then both the
bridge and the app state the versions they support.

| Bridge | Protocol schema | Controller for HomeKit |
|---|---|---|
| 1.0.x | 1 | 10.1 or newer |

## Trademarks

HomeKit is a trademark of Apple Inc. Home Assistant is a trademark of the Open Home Foundation.
This project is not affiliated with or endorsed by either.

## License

MIT, Acasa Software. See [LICENSE](LICENSE).
