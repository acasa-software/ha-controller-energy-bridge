# Security Policy

## Reporting a vulnerability

Please report security issues through GitHub's private vulnerability reporting: open the
repository's **Security** tab and choose **Report a vulnerability**. Do not open a public GitHub
issue for vulnerabilities. Include the integration version, Home Assistant version and steps to
reproduce; you will receive a reply within a few working days, and a fix is published as a
regular release with a note in `CHANGELOG.md`.

## Scope

The bridge runs its own HomeKit Accessory Protocol server on the local network only. It does
not open connections to the internet, does not use any cloud service and sends no telemetry.
Pairing uses the standard HomeKit pairing (SRP with a PIN); the pairing state is stored in
`<config>/.controller_energy_bridge.state` and is never included in diagnostics.

Supported versions: the latest release on the `main` branch.
