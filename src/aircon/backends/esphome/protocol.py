"""Mapping between an ESPHome climate entity and the aircon model. No I/O.

ESPHome's `web_server` component describes a climate entity as JSON, both in
`GET /climate/<name>` and in the `state` events of the `/events` stream:

    {"id": "climate/Air conditioner", "name": "Air conditioner",
     "modes": ["OFF", "HEAT_COOL", "COOL", "HEAT", "FAN_ONLY", "DRY"],
     "fan_modes": ["AUTO", "LOW", "MEDIUM", "HIGH"],
     "swing_modes": ["OFF", "BOTH", "VERTICAL", "HORIZONTAL"],
     "min_temp": "16", "max_temp": "30", "step": 1,
     "mode": "COOL", "fan_mode": "LOW", "swing_mode": "OFF",
     "current_temperature": "27.0", "target_temperature": "24"}

Changes go back as query parameters of `POST /climate/<name>/set`.
"""

from __future__ import annotations

import math
from dataclasses import replace

from aircon.model import FAN_SPEEDS, MODES, SWING_POSITIONS, Capabilities, Command, State

MODE_FROM_ESPHOME = {
    "HEAT_COOL": "auto",
    "AUTO": "auto",
    "COOL": "cool",
    "HEAT": "heat",
    "FAN_ONLY": "fan",
    "DRY": "dry",
}
FAN_FROM_ESPHOME = {"AUTO": "auto", "LOW": "low", "MEDIUM": "mid", "HIGH": "high"}
SWING_FROM_ESPHOME = {
    "OFF": "off",
    "VERTICAL": "vertical",
    "HORIZONTAL": "horizontal",
    "BOTH": "both",
}
FAN_TO_ESPHOME = {ours: theirs for theirs, ours in FAN_FROM_ESPHOME.items()}
SWING_TO_ESPHOME = {ours: theirs for theirs, ours in SWING_FROM_ESPHOME.items()}

# Haier units ignore a fan-only command with automatic speed, whatever the board in between.
FAN_ONLY_FALLBACK_SPEED = "mid"


def is_climate(entity: dict) -> bool:
    return entity.get("domain") == "climate" or str(entity.get("id", "")).startswith("climate/")


def entity_name(entity: dict) -> str:
    """The name used in URLs: `climate/Air conditioner` -> `Air conditioner`."""
    name = entity.get("name")
    if isinstance(name, str) and name:
        return name
    return str(entity.get("id", "")).partition("/")[2]


def _number(value: object) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        number = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None  # "NA", "nan" and friends
    return number if math.isfinite(number) else None


def _whole(value: object) -> int | None:
    number = _number(value)
    return None if number is None else int(round(number))


def _ours(values: object, table: dict[str, str], order: tuple[str, ...]) -> list[str]:
    known = {table[v] for v in values if v in table} if isinstance(values, list) else set()
    return [name for name in order if name in known]


def decode_capabilities(entity: dict, fallback: Capabilities) -> Capabilities:
    """What the board says the unit supports; anything it doesn't describe keeps `fallback`."""
    modes = _ours(entity.get("modes"), MODE_FROM_ESPHOME, MODES) or fallback.modes
    fans = _ours(entity.get("fan_modes"), FAN_FROM_ESPHOME, FAN_SPEEDS) or fallback.fan_speeds
    swing = _ours(entity.get("swing_modes"), SWING_FROM_ESPHOME, SWING_POSITIONS)
    if "swing_modes" not in entity:
        swing = fallback.swing
    elif "off" not in swing:
        swing = ["off", *swing]
    low = _whole(entity.get("min_temp"))
    high = _whole(entity.get("max_temp"))
    step = _whole(entity.get("step"))
    return Capabilities(
        modes=list(modes),
        fan_speeds=list(fans),
        swing=swing,
        temp_min=low if low is not None else fallback.temp_min,
        temp_max=high if high is not None else fallback.temp_max,
        temp_step=max(1, step) if step is not None else fallback.temp_step,
        features=list(fallback.features),
    )


def decode_state(entity: dict, previous: State) -> State:
    """Merge the entity into `previous`; fields the board leaves out or can't read stay as they
    were. While the unit is off, ESPHome reports mode OFF, so the last real mode is kept."""
    raw_mode = entity.get("mode")
    power = previous.power if raw_mode is None else raw_mode != "OFF"
    target = _whole(entity.get("target_temperature"))
    humidity = _whole(entity.get("current_humidity"))
    return replace(
        previous,
        power=power,
        mode=MODE_FROM_ESPHOME.get(str(raw_mode), previous.mode),
        fan=FAN_FROM_ESPHOME.get(str(entity.get("fan_mode")), previous.fan),
        swing=SWING_FROM_ESPHOME.get(str(entity.get("swing_mode")), previous.swing),
        target_temp=target if target is not None else previous.target_temp,
        room_temp=_whole(entity.get("current_temperature")),
        room_humidity=humidity if humidity is not None else previous.room_humidity,
    )


def esphome_mode(mode: str, raw_modes: list[str]) -> str:
    """Our mode name in the board's vocabulary ("auto" is HEAT_COOL on most climates)."""
    if mode == "auto":
        return "AUTO" if "AUTO" in raw_modes and "HEAT_COOL" not in raw_modes else "HEAT_COOL"
    return {"cool": "COOL", "heat": "HEAT", "fan": "FAN_ONLY", "dry": "DRY"}[mode]


def encode_command(state: State, cmd: Command, raw_modes: list[str]) -> dict[str, str]:
    """Query parameters for `/set` that turn `state` into `state + cmd`.

    Any change while the unit is off also switches it on, like the remote does.
    """
    if cmd.power is False:
        return {"mode": "OFF"}
    settings = {k: v for k, v in cmd.changes().items() if k != "power"}
    target = replace(state, **settings)
    params: dict[str, str] = {}
    if "mode" in settings or not state.power:
        params["mode"] = esphome_mode(target.mode, raw_modes)
    if "fan" in settings:
        params["fan_mode"] = FAN_TO_ESPHOME[target.fan]
    if target.mode == "fan" and target.fan == "auto" and ("mode" in params or "fan" in settings):
        params["fan_mode"] = FAN_TO_ESPHOME[FAN_ONLY_FALLBACK_SPEED]
    if "target_temp" in settings:
        params["target_temperature"] = str(int(target.target_temp))
    if "swing" in settings:
        params["swing_mode"] = SWING_TO_ESPHOME[target.swing]
    return params


def reached(state: State, params: dict[str, str]) -> bool:
    """Whether `state` already shows every change in `params`."""
    for key, value in params.items():
        if key == "mode":
            if value == "OFF":
                if state.power:
                    return False
            elif not state.power or MODE_FROM_ESPHOME.get(value) != state.mode:
                return False
        elif key == "fan_mode" and FAN_FROM_ESPHOME.get(value) != state.fan:
            return False
        elif key == "swing_mode" and SWING_FROM_ESPHOME.get(value) != state.swing:
            return False
        elif key == "target_temperature" and str(state.target_temp) != value:
            return False
    return True


def parse_events(buffer: str) -> tuple[list[tuple[str, str]], str]:
    """Split a Server-Sent Events text buffer into (event, data) pairs and the unparsed rest."""
    events: list[tuple[str, str]] = []
    buffer = buffer.replace("\r\n", "\n")
    while "\n\n" in buffer:
        block, buffer = buffer.split("\n\n", 1)
        event, data = "message", []
        for line in block.split("\n"):
            if not line or line.startswith(":"):
                continue
            field, _, value = line.partition(":")
            value = value[1:] if value.startswith(" ") else value
            if field == "event":
                event = value
            elif field == "data":
                data.append(value)
        if data:
            events.append((event, "\n".join(data)))
    return events, buffer
