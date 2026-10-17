# AGENTS.md

Guidance for AI coding agents (and humans) working on **aircon**: local, cloud-free control
of air conditioners on [Omarchy](https://omarchy.org/). Read this before changing anything.

## Adding support for a new air conditioner

When the user asks to add support for their air conditioner, a new brand, a new protocol or
a new backend (e.g. "add support for my Midea air conditioner"), follow
[`docs/adding-a-backend.md`](docs/adding-a-backend.md) step by step: ask what you need, research,
build the backend with offline tests, and only touch their real unit with permission.

---

> **Technical reference** (CLI, configuration file, socket API, internals, Haier protocol
> notes): [`docs/reference.md`](docs/reference.md). The README is the user-facing landing
> page; keep it short and non-technical and put details in the reference.

## What is in the repository

```
src/aircon/
  model.py            brand-independent State, Capabilities and Command
  config.py           ~/.config/aircon/config.toml: [[device]] list, ids, options
  backends/
    base.py           the Backend protocol every brand implements
    __init__.py       BACKENDS registry: name -> factory
    haier_smartair2/  reference backend (protocol.py = pure codec, backend.py = asyncio client)
    esphome/          any ESPHome climate board via its web_server (protocol.py, http.py, backend.py)
  daemon.py           aircond: one backend + scheduler per device, unix socket (JSON lines)
  scheduler.py        timers, sleep curve, filter hours (backend-independent)
  store.py            per-device persisted state
  cli.py              the `aircon` command
  setup.py            discovery, feature validation, config editing
  setup_cli.py        `aircon setup`
  menu.py             managed block in the Omarchy menu file
manifest.json         Omarchy plugin manifest (the repo itself is the plugin)
preview.png           screenshot for the README and the Omarchy plugin marketplace
plugin/               Omarchy shell (Quickshell/QML) bar widget, panel and in-panel setup
install.sh            install / --update / --uninstall (wrappers, systemd unit, menu)
tests/                pytest suite; fake_dongle.py fakes the Haier module, fake_esphome.py a board
tools/haier_probe.py  on-device validation for Haier units (changes the unit, then restores it)
docs/reference.md     technical reference (CLI, config, socket API, internals)
docs/adding-a-backend.md  how an agent adds support for a new air conditioner
docs/esphome.md       building an ESPHome board for a Haier unit; docs/esphome/*.yaml firmware
```

Data flow: plugin / menu / CLI → `aircon` → unix socket → `aircond` → `Backend` → device.
The socket protocol and snapshot shape are documented in docs/reference.md ("Socket API").

## Commands

```bash
uv sync                              # dev environment
uv run pytest                        # full test suite (fast, no network, no real device)
uv run ruff check src tests tools    # lint
omarchy plugin validate ~/.config/omarchy/plugins/io.github.seromenho.aircon   # the installed copy (a dev .venv has symlinks)
./install.sh                         # install from this clone (copies into the plugin folder)
```

## Rules

- **English only** in the repository: code, comments, docs, commit messages and UI strings,
  whatever language you chat in.
- **No personal data**: no real IP addresses, MAC addresses, names, tokens or keys in code,
  tests or docs. Use placeholders such as `192.168.1.50` and `04FA83A1B2C3`.
- **Runtime dependencies: stdlib only.** A backend that truly needs a third-party library must
  make it optional (import inside the backend) and say so in the README.
- **Tests never touch the user's files or devices.** `tests/conftest.py` points
  `AIRCON_CONFIG`, `AIRCON_MENU_FILE` and `AIRCON_SOCKET` at temporary paths; keep it that way.
  Use fakes for devices.
- **Hardware is touched only by explicit, owner-approved tools** (like
  `tools/haier_probe.py`), and every change to a real unit is restored afterwards.
- **Brand-specific code stays inside its backend.** The model, daemon, CLI, menu and plugin
  must keep working for every backend; they learn what a device supports from `Capabilities`.
- **The plugin id `io.github.seromenho.aircon` is permanent**: it is how the Omarchy plugin
  marketplace, `shell.json`, the menu entries and keybindings find the plugin. Never change it.
- **The Omarchy menu file belongs to the user.** Only `aircon menu sync`, `install.sh --menu`
  or a yes to the install question may add our entries; everything else only refreshes
  entries that are already there (`menu.is_installed`).
- **Plugin**: no hard-coded colors (use `Color.*`, `bar.*` or `AcPalette`), strings in
  English, no symlinks in the plugin folder.
- **Releases:** bump the version in `manifest.json`, `pyproject.toml`,
  `src/aircon/__init__.py` and `plugin/Service.qml` together (a test checks they match). After
  `omarchy plugin update`, the widget compares it with the version the daemon reports and runs
  `install.sh --update` when they differ.
- Keep the repo green: `uv run pytest` and `uv run ruff check` before every commit.

## Plugin gotchas

- Never name a QML id `palette`: every Item already has a `palette` property.
- Inside `PanelHero.trailingControl` the name `root` does not reach the panel; go through an
  id defined in `Panel.qml` (see the `actions` object).
- The shell caches QML components. After changing plugin files run `./install.sh` and then
  `omarchy restart shell`; `rescanPlugins` alone may keep the old code.
- If the installed plugin folder is a git clone made by `omarchy plugin add`, `./install.sh`
  runs that clone's own script and your local changes are not copied. Run
  `omarchy plugin remove io.github.seromenho.aircon` first to test a working copy.
- Shell logs: `/run/user/$UID/quickshell/by-id/<instance>/log.log`.
