# Contributing

Thanks for helping with the Controller Energy Bridge. Bug reports and feature requests go
through the GitHub issue templates; questions about the Controller for HomeKit app itself
belong to the app support at https://acasa-software.de.

## Development setup

```bash
uv venv --python 3.14 .venv
uv pip install -r requirements_test.txt
.venv/bin/pytest -q
.venv/bin/ruff check custom_components tests
.venv/bin/ruff format custom_components tests
```

Home Assistant 2026.9 requires Python 3.14. `pytest-homeassistant-custom-component` is pinned
in `requirements_test.txt` to the release matching that Home Assistant version; CI also runs
the tests against the latest harness as an early warning, without blocking.

For manual testing copy or symlink `custom_components/controller_energy_bridge/` into a Home
Assistant `config/custom_components/` directory and restart. The pairing PIN appears as a
persistent notification.

## The protocol is the interface to the app

`docs/protocol.md` is the contract between this integration and Controller for HomeKit. This
repository holds the authoritative version; the app keeps a copy. Both sides are tested against
it, so:

- Do not change UUIDs, formats, permissions, ranges, sign conventions or blob layouts in a pull
  request on its own. Open an issue first; changes are coordinated with the app.
- Additive changes (new optional characteristics, new fields) keep the schema version. Anything
  a current app version could misread bumps the schema version.
- Every protocol change ships with tests in `tests/test_accessory.py` or `tests/test_history.py`
  that pin the wire format.

## Pull requests

- Branch from `main`, keep the change focused, add or update tests.
- `pytest`, `ruff check` and `ruff format --check` must pass; CI runs them together with
  hassfest and the HACS validation.
- Add a line under `[Unreleased]` in `CHANGELOG.md`.
- Commit messages follow Conventional Commits (`feat:`, `fix:`, `docs:`, `test:`, `chore:`),
  imperative mood, English.
- Comments describe the current behaviour, not the change history.

## Translations

UI text lives in `custom_components/controller_energy_bridge/strings.json` (source of truth)
and `translations/<lang>.json`. Add new keys to `strings.json` and to every existing
translation file; a missing key falls back to English at runtime but fails hassfest. New
languages are welcome as a full translation file.

## Releases

Maintainers bump `version` in both `custom_components/controller_energy_bridge/manifest.json`
and `pyproject.toml`, move the `[Unreleased]` notes to a version section in `CHANGELOG.md` and
push a `vX.Y.Z` tag. The release workflow checks that the three versions agree and creates the
GitHub release from the changelog section; tags containing `-beta` or `-rc` become pre-releases.

## Branding

`brand/icon.png` is a placeholder. The icon shown in Home Assistant comes from the
[home-assistant/brands](https://github.com/home-assistant/brands) repository, which needs a
separate pull request there (domain `controller_energy_bridge`) once a final icon exists.
