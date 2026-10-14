# Reference

Everything technical about omarchy-aircon. For a quick start, see the [README](../README.md).

- [Components](#components)
- [Install, update and uninstall](#install-update-and-uninstall)
- [Setup from the command line](#setup-from-the-command-line)
- [Command line](#command-line)
- [Several air conditioners](#several-air-conditioners)
- [Configuration file](#configuration-file)
- [Bar widget and keybinding](#bar-widget-and-keybinding)
- [How it works](#how-it-works)
- [Socket API](#socket-api)
- [Haier SmartAir2 notes](#haier-smartair2-notes)
- [Midea LAN notes](#midea-lan-notes)
- [Development](#development)

## Components

- `aircond`: a small daemon (systemd user service) that keeps one connection per air
  conditioner and publishes their state over a unix socket.
- `aircon`: the command-line client.
- the Omarchy shell (Quickshell) plugin: `manifest.json` at the repository root and the QML in
  `plugin/` (bar indicator, control panel, in-panel setup).
- Omarchy menu entries for quick actions.

Requirements: Omarchy 4 (Quickshell shell) and the system `python3` (3.12 or newer). There are
Haier uses only the standard library; Midea uses optional `msmart-ng`. Nothing is installed
system-wide.

## Install, update and uninstall

The repository is an Omarchy plugin, so the usual way is:

```bash
omarchy plugin add https://github.com/seromenho/omarchy-aircon --enable
```

`omarchy plugin add` clones the repository into
`~/.config/omarchy/plugins/io.github.seromenho.aircon/` and enables the bar widget, but it does
not run install scripts. Until the rest is installed the widget shows a download icon, and its
panel offers **Finish install**, which runs the plugin's own `install.sh` in a floating
terminal. That script:

1. writes two small wrappers, `~/.local/bin/aircon` and `~/.local/bin/aircond`, that run the
   code from the plugin folder with `python3`;
2. installs, enables and (re)starts the `aircond` systemd user service;
3. makes sure the widget is enabled on the right side of the bar;
4. asks whether to add the "Air Conditioner" entries to the Omarchy menu. They go into a marked
   block of `~/.config/omarchy/extensions/omarchy-menu.jsonc` (`aircon menu sync`; the original
   file is kept as `omarchy-menu.jsonc.aircon.bak`).

The menu file is yours, so it is only changed with your consent:

- `install.sh --menu` adds the entries and `install.sh --no-menu` removes them, without asking;
- without either flag, an install in a terminal asks, and a non-interactive one leaves the file
  alone and prints how to add them later (`aircon menu sync`);
- entries that are already there are kept up to date by `install.sh --update`, setup, rename and
  remove; when they are not there, nothing adds them;
- `aircon menu status` tells whether they are there, `aircon menu remove` drops them.

It is safe to re-run. It also replaces installs from earlier versions (installed with
`uv tool`, or as an `aircon` or `<user>.aircon` plugin): it turns the old widget off, moves the
old folder away and enables the new one. Only folders whose manifest is named "Air Conditioner"
are touched.

**Without `omarchy plugin add`:** clone the repository anywhere and run `./install.sh`; it
copies the code into the plugin folder first and then does the same steps.

**Updating:** `omarchy plugin update io.github.seromenho.aircon` pulls the new code. The widget
notices that the running service reports an older version and runs `install.sh --update` once
by itself.

**Upgrading from 0.2:** the plugin id changed from `aircon` to `io.github.seromenho.aircon` in
0.3 (ids in the plugin marketplace are namespaced). The old widget cannot update itself across
the rename, so run once:

```bash
omarchy plugin update aircon                                 # pulls 0.3 into the old folder
~/.config/omarchy/plugins/aircon/install.sh --update         # moves it to the new id
```

If you installed from a clone with `./install.sh`, pull the clone and run `./install.sh`
instead. Keybindings that call `omarchy-shell shell toggle aircon` need the new id (see below).

**Uninstalling:**

```bash
# the service, the commands and the menu entries
~/.config/omarchy/plugins/io.github.seromenho.aircon/install.sh --uninstall
# the widget and the code
omarchy plugin remove io.github.seromenho.aircon
```

Your configuration in `~/.config/aircon/` and state in `~/.local/state/aircon/` are kept.

Setup itself happens in the bar panel (or with `aircon setup`, below).

## Setup from the command line

The panel and interactive `aircon setup` first let you choose Midea / NetHome Plus or
Haier / SmartAir2. Midea setup discovers the device ID and protocol version; V3 units ask for
a private token/key TOML file. For Haier, they scan the local network for devices
answering on TCP port 56800, show what was found (address, MAC, current state), ask for a
name and, if you agree, test the optional features (left/right swing, health, sleep). That
test changes the unit for about a minute and then restores it. The result is written to
`~/.config/aircon/config.toml` (keeping a backup) and `aircond` is restarted.

Each run adds one air conditioner; adding a unit with an already known MAC updates it.

```bash
aircon setup                                    # interactive
aircon setup discover [--json] [--hosts IP ...]
aircon setup apply --host IP --mac MAC [--name NAME] [--validate] [--json] [--no-restart]
```

## Command line

```
aircon [-d ID] status [--json]
aircon on | off | toggle
aircon set [--mode auto|cool|heat|fan|dry] [--temp 24] [--fan low|mid|high|auto]
           [--swing off|vertical|horizontal|both] [--health on|off] [--sleep on|off]
aircon temp +1 | -1 | 24
aircon timer on|off (--at 07:30 | --in 90m)
aircon timer cancel on|off
aircon sleep-curve start [--hours 8] [--off-at-end] | aircon sleep-curve cancel
aircon filter reset
aircon setup
aircon devices | rename ID NAME | remove ID
aircon all on|off
aircon menu sync | remove | status
aircon watch            # JSON lines, used by the plugin
aircon is-on            # exit status only
```

## Several air conditioners

```bash
aircon devices                    # list them (id, name, address)
aircon -d bedroom set --temp 22   # target one unit by id
aircon all off                    # every unit
aircon rename bedroom "Kids room" # the id stays the same
aircon remove bedroom
```

Without `-d`, commands act on the default unit (`default = "<id>"` in the config, otherwise
the first). The bar shows the unit selected in the panel, plus `·N` when N units are running.
The panel has one tab per unit; Rename and Remove are in its Settings (gear icon). Each unit
has its own timers, sleep curve and filter counter.

## Configuration file

`~/.config/aircon/config.toml` is written by setup; it can also be edited by hand:

```toml
default = "living-room"         # optional

[[device]]
id = "living-room"              # a-z, 0-9 and -; derived from the name when omitted
name = "Living room"
backend = "haier_smartair2"
host = "192.168.1.50"           # give the module a fixed IP on the router
mac = "04FA83A1B2C3"            # printed on the Wi-Fi module
swing_style = "bits"
features = ["health", "sleep", "swing_horizontal"]

[[device]]
id = "bedroom"
name = "Bedroom"
backend = "haier_smartair2"
host = "192.168.1.51"
mac = "04FA83AABBCC"
options = { }                   # backend-specific settings (keys, tokens, …)

[filter]
threshold_hours = 300
```

After editing by hand run `systemctl --user restart aircond` (and `aircon menu sync` if you use
the menu entries). If the file has a mistake, the panel says so instead of showing your units.

Good to know:

- Haier units need their `mac` (12 hex digits, printed on the Wi-Fi module).
- Setup, rename and remove rewrite the file, so comments are not kept. The file is written
  with owner-only permissions (it may hold keys in `options`), and the last three versions
  are kept as `config.toml.bak.*`. If the file is a symlink (dotfiles), the link is kept and
  its target is updated.

## Bar widget and keybinding

Left click opens the panel, right click toggles power, the scroll wheel changes the target
temperature. Inside the panel, `+`/`-` change the temperature and `p` toggles power.

To open the panel with a key, add to `~/.config/hypr/bindings.lua`:

```lua
o.bind("SUPER + CTRL + A", "Air conditioner", "omarchy-shell shell toggle io.github.seromenho.aircon")
```

## How it works

```
plugin (QML) ── aircon watch / aircon set ──► aircond ── Backend ──► device
Omarchy menu ── aircon … ───────────────────┘   ├─ scheduler: timers, sleep curve
                                                └─ filter-hours counter
```

Timers, the sleep curve and the filter counter are persisted per device in
`~/.local/state/aircon/state.json`.

- **Timer**: power on or off at a time or after a delay.
- **Sleep curve**: in cool mode the target rises 1 °C after one hour and 2 °C after two
  (falls in heat mode); optionally powers off at the end. A manual temperature change cancels
  it. The unit's own sleep bit is set while it runs.
- **Filter**: counts hours of operation and reminds you to clean the filter.

## Socket API

`aircond` listens on `$XDG_RUNTIME_DIR/aircon.sock` and speaks JSON lines: one request object
per line, one reply per line. Every request may carry `"device": "<id>"`; without it the
default device is used.

| Request | Effect |
|---|---|
| `{"op": "get"}` | full snapshot |
| `{"op": "watch"}` | snapshot now, then one line per change (the connection stays open) |
| `{"op": "set", "power": true, "mode": "cool", "target_temp": 24, "fan": "auto", "swing": "off", "health": false, "sleep": false}` | any subset of these fields; `"device": "all"` accepts `power` only |
| `{"op": "timer", "action": "off", "in_minutes": 90}` (or `"at": "<ISO-8601>"`) | schedule power on/off |
| `{"op": "timer_cancel", "action": "off"}` | cancel a timer |
| `{"op": "sleep_curve", "hours": 8, "off_at_end": false}`, `{"op": "sleep_curve_cancel"}` | sleep curve |
| `{"op": "filter_reset"}` | reset the filter counter |

Replies are `{"ok": true, "state": <snapshot>}` or `{"ok": false, "error": "…"}`. A snapshot
is `{"configured": true, "default": "<id>", "updated_at": "…", "devices": [...]}`, where each
device carries `id`, `name`, `backend`, `host`, `connected`, `capabilities`, the generic state
(`power`, `mode`, `fan`, `target_temp`, `room_temp`, `room_humidity`, `swing`, `health`,
`sleep`, `alarm`), `timers`, `sleep_curve`, `filter` and `last_error`.

## Haier SmartAir2 notes

Works with Haier units fitted with the SmartAir2 USB Wi-Fi module (KZW-W002 / MK-QTWIFI
family), the one that pairs with the **HaierSmartAir2** app (not hOn). Pair it once with that
app; afterwards neither the app nor the internet is needed. The module listens on TCP port
56800 without encryption or authentication: anything on your network that answers on that port is trusted, so keep the unit on a network you control.

Behaviour observed on a unit with module firmware 2.3.12:

| Feature | Result |
|---|---|
| Power on/off (`4D 02` / `4D 03`) | works |
| Target temperature 16–30 °C | works |
| Modes auto, cool, heat, fan, dry | work |
| Fan speeds low, mid, high, auto | work, except auto in fan-only mode (the unit rejects the whole command; the backend sends mid instead) |
| Swing vertical, horizontal, both, off | work with `swing_style = "bits"` |
| Health and sleep bits | work |
| Second concurrent TCP connection | accepted |

Community reports suggest newer module firmware (2.5.x) may not answer locally.

To check another unit:

```bash
uv run python tools/haier_probe.py --host <ip> --mac <mac> status
uv run python tools/haier_probe.py --host <ip> --mac <mac> validate   # changes the AC, then restores it
```

## Midea LAN notes

The `midea_lan` backend uses the optional
[msmart-ng library](https://github.com/mill1000/midea-msmart) to control residential Midea
ACs (device type `0xAC`) over the LAN. NetHome Plus and Midea Air Wi-Fi modules using V2 or
V3 are supported by the library. V1, commercial `0xCC` units and Tuya-based modules are not
handled by this backend. This integration has been tested with offline fakes and the
library API, **not on a real Midea unit**.

Pair the unit with NetHome Plus first and reserve its address in your router. Install from
the clone containing this backend:

```bash
./install.sh --midea
aircon setup discover --backend midea_lan --json
# Or discover a known address:
aircon setup discover --backend midea_lan --hosts 192.168.1.50
```

`--midea` installs `msmart-ng` in `${XDG_DATA_HOME:-~/.local/share}/aircon/midea-venv/` using
`uv`, or Python's `venv` and `pip` if `uv` is unavailable. Both installed commands use that
environment when present. It lives outside the plugin folder, survives plugin updates and
is kept during uninstall. Remove it manually for a clean uninstall. Other backends do not
need this dependency. For development use `uv sync --extra midea` and
`uv run --extra midea python -m aircon.cli ...`.

Discovery returns the address, device ID and protocol version. When no supported device
answers the broadcast, setup also sends direct discovery requests to hosts on the local
networks carrying the default route (at most 1024 hosts per network). This handles firewalls
such as UFW that drop broadcast replies but accept responses to direct requests. No firewall
changes are needed. It then retries known local neighbors to recover replies lost while the
router resolves new addresses; allow about 15–30 seconds for the complete search.
Discovery explicitly disables
automatic connection: it sends only local discovery requests, never contacts the cloud,
authenticates or changes settings. Choose **Midea / NetHome Plus** in the panel's **+ Add**
wizard, or run `aircon setup --backend midea_lan`. V3 setup asks for the path to your private
credentials file, with `~` supported. Configured Midea units use the normal
panel, timers, sleep curve and menu.

For a known device, save optional discovery preferences in `~/.config/aircon/setup.toml`:

```toml
backend = "midea_lan"
hosts = ["192.168.1.50"]
```

The panel and `aircon setup discover` then probe that address directly, which is more reliable
on networks that lose broadcast or subnet-wide replies. Explicit `--hosts` overrides this
preference; remove the `hosts` line to search the full local network again. This file does
not configure control of the AC and never holds credentials.

V2 needs only the device ID:

```bash
aircon setup apply --backend midea_lan --host 192.168.1.50 \
  --device-id 123456789 --protocol-version 2 --name "Living room"
```

V3 additionally needs a token and key. Obtain those once using the upstream `msmart-ng`
discovery tool (`msmart-ng discover --help` for account/region options, then
`msmart-ng discover 192.168.1.50` for a known address). The installer puts that executable in the environment's `bin/`
directory. **That upstream command can contact Midea's cloud** to obtain credentials;
aircon itself never does. Keep the output private and save the successful token/key for
future use without internet access. If the module is paired again, new credentials may be
needed.

Put the values in a private TOML file outside the clone, for example
`~/.config/aircon/midea-options.toml` (run `chmod 600` on it):

```toml
device_id = "123456789"
protocol_version = 3
token = "REPLACE_WITH_HEXADECIMAL_TOKEN"
key = "REPLACE_WITH_64_HEXADECIMAL_CHARACTERS"
```

Then add the unit without putting secrets in command arguments:

```bash
aircon setup apply --backend midea_lan --host 192.168.1.50 \
  --options-file ~/.config/aircon/midea-options.toml --name "Living room"
aircon status
```

`setup apply` validates and saves these settings without probing the unit; the restarted
daemon connects in the background. Use `--no-restart` to save without connecting. The config
and its backups are written with owner-only permissions. Setup output and daemon snapshots
never include tokens or keys. Re-adding the same device ID updates it and preserves existing
credentials when they are omitted.

Alternatively, add a block to `~/.config/aircon/config.toml`:

```toml
[[device]]
id = "midea-living-room"
name = "Living room"
backend = "midea_lan"
host = "192.168.1.50"
port = 6444
features = []
options = { device_id = "123456789", protocol_version = 3, token = "REPLACE_WITH_HEX_TOKEN", key = "REPLACE_WITH_HEX_KEY" }
```

For V2 set `protocol_version = 2` and omit token/key. With no version, a supplied token/key
selects V3; otherwise V2 is used. An omitted port uses Midea's default `6444`. Restart
`aircond` after manual edits and run `aircon menu sync` if you use menu entries.

Power, available modes, low/mid/high/auto fan speeds, vertical swing, target and indoor
temperatures map to the generic model. The library queries device capabilities; older
modules that omit capabilities use the library's defaults, so controls still need hardware
verification. Custom fan percentages are shown as the nearest generic band. Target commands
use whole degrees; half-degree temperatures reported by the unit are preserved when other
settings change. Extra settings such as eco mode and Fahrenheit display are retained.
Nonzero device error codes are shown as alarms. Humidity is not exposed.

Horizontal swing, device sleep and purifier/health are disabled by default. After confirming
them on the unit, add `"swing_horizontal"`, `"sleep"` or `"health"` to `features` respectively;
horizontal swing and purifier must also be supported by the library's capability report.
`--validate` is currently Haier-only. No automatic feature tests change a Midea unit.

Requests are serialized, polled and retried/reconnected by the library and backend. Missing
replies are treated as failures rather than successful changes. A missing optional package
or unreachable unit leaves that device offline while the daemon continues serving others.

## Development

```bash
uv sync
uv run pytest
uv run ruff check src tests tools
omarchy plugin validate ~/.config/omarchy/plugins/io.github.seromenho.aircon   # the installed copy (a dev .venv has symlinks)
```

Tests use an in-process fake of the Haier module (`tests/fake_dongle.py`) and never touch the
real device or the user's files. Contributor and agent rules are in
[AGENTS.md](../AGENTS.md).
