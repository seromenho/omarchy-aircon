# Reference

Everything technical about omarchy-aircon. For a quick start, see the [README](../README.md).

- [Components](#components)
- [What the installer does](#what-the-installer-does)
- [Setup from the command line](#setup-from-the-command-line)
- [Command line](#command-line)
- [Several air conditioners](#several-air-conditioners)
- [Configuration file](#configuration-file)
- [Bar widget and keybinding](#bar-widget-and-keybinding)
- [How it works](#how-it-works)
- [Socket API](#socket-api)
- [Haier SmartAir2 notes](#haier-smartair2-notes)
- [Development](#development)

## Components

- `aircond`: a small daemon (systemd user service) that keeps one connection per air
  conditioner and publishes their state over a unix socket.
- `aircon`: the command-line client.
- `plugin/aircon/`: the Omarchy shell (Quickshell) plugin: bar indicator, control panel and
  in-panel setup.
- Omarchy menu entries for quick actions.

Requirements: Omarchy 4 (Quickshell shell), `uv`, Python 3.12+. No runtime Python
dependencies beyond the standard library.

## What the installer does

`./install.sh` (safe to re-run after pulling changes):

1. installs `aircon` and `aircond` into `~/.local/bin` with `uv tool install`;
2. installs and starts the `aircond` systemd user service;
3. copies the plugin to `~/.config/omarchy/plugins/aircon/` and enables it on the right side
   of the bar (replacing a plugin installed under an older `<user>.aircon` id);
4. writes the "Air Conditioner" entries into a marked block of
   `~/.config/omarchy/extensions/omarchy-menu.jsonc` (`aircon menu sync`; the original file
   is kept as `omarchy-menu.jsonc.aircon.bak`).

Setup itself happens in the bar panel (or with `aircon setup`, below).

## Setup from the command line

The panel's setup and `aircon setup` do the same thing: scan the local network for devices
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
aircon menu sync
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

After editing by hand run `aircon menu sync` and `systemctl --user restart aircond`.

## Bar widget and keybinding

Left click opens the panel, right click toggles power, the scroll wheel changes the target
temperature. Inside the panel, `+`/`-` change the temperature and `p` toggles power.

To open the panel with a key, add to `~/.config/hypr/bindings.lua`:

```lua
o.bind("SUPER + CTRL + A", "Air conditioner", "omarchy-shell shell toggle aircon")
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
56800 without encryption.

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

## Development

```bash
uv sync
uv run pytest
uv run ruff check src tests tools
omarchy plugin validate plugin/aircon
```

Tests use an in-process fake of the Haier module (`tests/fake_dongle.py`) and never touch the
real device or the user's files. Contributor and agent rules are in
[AGENTS.md](../AGENTS.md).
