# Prompt: add a backend for another air conditioner

aircon talks to devices through **backends**. Haier units with the SmartAir2 Wi-Fi module are
supported out of the box; anything else (Midea, Gree, Daikin, Tuya-based units, an ESPHome
board, …) needs a new backend.

## How to use this prompt

1. Collect what you know about your unit: brand and model, how it is controlled (app name),
   whether it can be reached on the LAN, and any reverse-engineering projects or libraries
   you found (links help a lot).
2. Open a coding agent (Claude Code, Codex, …) in a clone of this repository.
3. Paste the prompt below and fill in the `<…>` placeholders.
4. Review the result, run the tests yourself, and only then let it touch your real unit.

## The prompt

```text
You are adding a new backend to aircon, a local air-conditioner controller for Omarchy.
Read AGENTS.md first and follow its rules (English only, stdlib-only runtime, no personal
data in the repo, tests must not touch my files or devices).

My air conditioner:
- Brand / model: <e.g. Midea MSAG-12HRN1>
- App it normally uses: <e.g. Midea Air / NetHome Plus / Tuya Smart>
- How it is reachable: <LAN IP if known, port if known, or "unknown">
- Known protocol details, projects or libraries: <links, e.g. msmart-ng, greeclimate,
  pydaikin, tinytuya, ESPHome component; paste notes you have>
- Credentials it may need: <e.g. local key, token, device id; do NOT paste real values>

What to build:
1. Study the reference backend in src/aircon/backends/haier_smartair2/ and the Backend
   protocol in src/aircon/backends/base.py. Your backend must implement the same protocol:
   name, capabilities, connected, state, last_error, start(), close(), refresh(), apply(),
   on_state(), on_connection(). start() must return immediately and keep (re)connecting in
   the background; the daemon must keep working while the device is unreachable.
2. Create src/aircon/backends/<backend_name>/ with:
   - protocol.py: pure encode/decode functions with no I/O (if the protocol is ours to
     implement), or a thin adapter around an optional library imported inside the backend;
   - backend.py: the asyncio client.
   Map the device to the generic model in src/aircon/model.py (modes auto/cool/heat/fan/dry,
   fan low/mid/high/auto, swing off/vertical/horizontal/both, target and room temperature,
   humidity, health, sleep). Declare in Capabilities only what the device really supports;
   the CLI and the panel hide everything else.
3. Register it in BACKENDS in src/aircon/backends/__init__.py.
4. Backend-specific settings (keys, tokens, protocol versions) go in the device's `options`
   table in config.toml (DeviceConfig.options). Never hard-code them.
5. Tests, all offline:
   - unit tests for the codec/mapping (use captured or documented frames; strip any real
     identifiers);
   - a fake device in tests/ that speaks the protocol, and backend tests against it:
     initial state, apply(), power on/off, retry/timeout, reconnect after a dropped
     connection, unreachable device does not raise;
   - make sure the existing daemon/CLI tests still pass.
6. Setup: `aircon setup` currently discovers Haier SmartAir2 units only. If the new device
   can be discovered on the LAN, add discovery for it in src/aircon/setup.py without
   breaking the Haier flow; otherwise document how to add it with
   `aircon setup apply` or by editing config.toml.
7. Documentation: add one friendly line to "Supported air conditioners" in README.md (keep
   the README short and non-technical), and put the details in docs/reference.md: config
   example with placeholder values, pairing steps, known limitations and, if you validated
   it on hardware, a short behaviour table like the Haier one. Never include your own model
   number, addresses or keys.
8. Run `uv run pytest` and `uv run ruff check src tests tools`; both must pass.

Do not touch my real device until I say so. When I do, write or extend a probe tool like
tools/haier_probe.py that records the current state, tests each capability, and restores
the original state at the end.

Work in small commits with clear English messages. When you finish, summarise what works,
what you could not verify, and which capabilities you left disabled.
```

## After the agent is done

- Add the device: `aircon setup apply --host <ip> --mac <mac> --name <name>` (Haier-style
  devices), or add a `[[device]]` block to `~/.config/aircon/config.toml` with
  `backend = "<backend_name>"` and its `options`, then run `aircon menu sync` and
  `systemctl --user restart aircond`.
- Consider contributing the backend back, without any of your own addresses or keys.
