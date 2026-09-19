# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this
project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html). Beta builds are
published as GitHub pre-releases from tags such as `v1.1.0-beta.1`; `manifest.json` keeps the
plain version number of the release the beta leads up to.

## [Unreleased]

## [1.0.0] - 2026-09-19

Requires Controller for HomeKit 10.1 or newer (protocol schema 1).

### Added

- Import of the Home Assistant Energy dashboard: grid, solar, battery, gas and water sources
  and device consumption become energy nodes with their kWh sources; changes to the dashboard
  are picked up automatically and user settings are carried over by role and kWh entity.
- Config and options flow: HomeKit port, update interval, per-node name, live power sensor
  (pre-filled from `stat_rate` or the same device), inverted sign, battery state of charge and
  capacity, optional real home-consumption sensors.
- HomeKit accessory served by an integration-owned HAP server (HAP-python 5): one Energy
  Bridge Info service and one Energy Node service per node with power, energy from/to, state of
  charge, capacity and status fault, following `docs/protocol.md` schema version 1, including
  sign conventions, unit normalisation, throttling and availability handling.
- History characteristics (protocol §13): 30 days of hourly energy and 24 hours of 15-minute
  power buckets built from recorder statistics, published as read-only `data` characteristics
  and refreshed on a schedule.
- Pairing notification with PIN and setup URI, diagnostics download, English and German
  translations.

[Unreleased]: https://github.com/acasa-software/ha-controller-energy-bridge/compare/v1.0.0...HEAD
[1.0.0]: https://github.com/acasa-software/ha-controller-energy-bridge/releases/tag/v1.0.0
