# Adding support for a new air conditioner

Instructions for a coding agent. Follow them when the user asks to support their air
conditioner, a new brand, a new protocol or a new backend (for example "add support for my
Midea air conditioner"). Read [AGENTS.md](../AGENTS.md) first; its rules apply throughout.

aircon talks to devices through **backends**. Haier units with the SmartAir2 Wi-Fi module are
supported out of the box; anything else (Midea, Gree, Daikin, Tuya-based units, an ESPHome
board, …) needs a new backend. Everything else — panel, timers, sleep curve, menu, CLI — works
with any backend unchanged.

## 1. Ask before building

Ask the user, briefly and only for what they haven't told you yet:

- brand and model of the indoor unit (or of its Wi-Fi module);
- the app they normally use to control it (e.g. Midea Air, NetHome Plus, Tuya Smart, hOn);
- whether the unit is on their home network, and its IP address if they know it;
- any project, library or notes they already found;
- whether it needs credentials (local key, token, device id). Tell them never to paste real
  secrets into the chat if they would rather keep them out of it; they go in their local
  config file, not in the repository.

## 2. Research

Look for existing local-control work for that brand or module: Python libraries (e.g.
msmart-ng, greeclimate, pydaikin, tinytuya), Home Assistant integrations, Homebridge
plugins, ESPHome components and reverse-engineering write-ups. Prefer protocols that work on
the LAN without the vendor cloud. Summarise what you found for the user and propose an
approach (own protocol code vs. an optional library) before writing code.

## 3. Build the backend

1. Study the reference backend in `src/aircon/backends/haier_smartair2/` and the `Backend`
   protocol in `src/aircon/backends/base.py`. Implement the same protocol: `name`,
   `capabilities`, `connected`, `state`, `last_error`, `start()`, `close()`, `refresh()`,
   `apply()`, `on_state()`, `on_connection()`. `start()` must return immediately and keep
   (re)connecting in the background; the daemon must keep working while the device is
   unreachable.
2. Create `src/aircon/backends/<backend_name>/` with:
   - `protocol.py`: pure encode/decode functions with no I/O, if we implement the protocol
     ourselves; or a thin adapter around an optional library imported inside the backend
     (runtime dependencies are stdlib-only, so a library must stay optional and be documented);
   - `backend.py`: the asyncio client.
3. Map the device to the generic model in `src/aircon/model.py`: modes auto/cool/heat/fan/dry,
   fan low/mid/high/auto, swing off/vertical/horizontal/both, target and room temperature,
   humidity, health, sleep. Declare in `Capabilities` only what the device really supports;
   the CLI and the panel hide everything else.
4. Register the backend in `BACKENDS` in `src/aircon/backends/__init__.py`.
5. Backend-specific settings (keys, tokens, protocol versions) go in the device's `options`
   table in `config.toml` (`DeviceConfig.options`). Never hard-code them.
6. Setup: the panel and `aircon setup` currently discover Haier SmartAir2 units only. If the
   new device can be discovered on the LAN, add discovery for it in `src/aircon/setup.py`
   without breaking the Haier flow; otherwise document how to add it with
   `aircon setup apply` or by editing `config.toml`.

## 4. Test offline

- Unit tests for the codec or mapping, using captured or documented frames with any real
  identifiers removed.
- A fake device in `tests/` that speaks the protocol, and backend tests against it: initial
  state, `apply()`, power on/off, retry/timeout, reconnect after a dropped connection, and an
  unreachable device that does not raise.
- The existing daemon and CLI tests must still pass: `uv run pytest` and
  `uv run ruff check src tests tools`.

## 5. Touch the real unit only with permission

Do not send anything to the user's air conditioner until they say so. When they do, write or
extend a probe tool like `tools/haier_probe.py` that records the current state, tests each
capability, and restores the original state at the end. Disable in `Capabilities` anything
that does not round-trip.

## 6. Document

- README.md: add one friendly line under "Supported air conditioners". Keep the README short
  and non-technical.
- docs/reference.md: config example with placeholder values, pairing steps, known
  limitations and, if validated on hardware, a short behaviour table like the Haier one.
- Never include the user's model number, addresses or keys anywhere in the repository.

## 7. Wrap up

Work in small commits with clear English messages. Finish with a summary for the user: what
works, what could not be verified, which capabilities were left disabled, and how to add
their unit (the panel's **+ Add**, `aircon setup apply`, or a `[[device]]` block with
`backend = "<backend_name>"` and its `options`, followed by `aircon menu sync` and
`systemctl --user restart aircond`). Suggest contributing the backend back, without any of
their addresses or keys.
