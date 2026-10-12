"""Brand-independent climate model shared by backends, the daemon, the CLI and the plugin."""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass, field, replace

MODES = ("auto", "cool", "heat", "fan", "dry")
FAN_SPEEDS = ("low", "mid", "high", "auto")
SWING_POSITIONS = ("off", "vertical", "horizontal", "both")

WRITABLE_FIELDS = ("power", "mode", "fan", "target_temp", "swing", "health", "sleep")
OPTIONAL_FEATURES = ("health", "sleep")


@dataclass
class Capabilities:
    """What a backend can do; the UI and the CLI only offer what is listed here."""

    modes: list[str]
    fan_speeds: list[str]
    swing: list[str]
    temp_min: int
    temp_max: int
    temp_step: int = 1
    features: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class State:
    power: bool = False
    mode: str = "cool"
    fan: str = "auto"
    target_temp: int = 24
    room_temp: int | None = None
    room_humidity: int | None = None
    swing: str = "off"
    health: bool = False
    sleep: bool = False
    alarm: str | None = None

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class Command:
    """A partial change: None means "leave as is"."""

    power: bool | None = None
    mode: str | None = None
    fan: str | None = None
    target_temp: int | None = None
    swing: str | None = None
    health: bool | None = None
    sleep: bool | None = None

    @classmethod
    def from_dict(cls, data: dict) -> Command:
        unknown = sorted(set(data) - set(WRITABLE_FIELDS))
        if unknown:
            raise ValueError(f"unknown field(s): {', '.join(unknown)}")
        return cls(**{k: data[k] for k in WRITABLE_FIELDS if data.get(k) is not None})

    def changes(self) -> dict:
        return {k: getattr(self, k) for k in WRITABLE_FIELDS if getattr(self, k) is not None}

    def is_empty(self) -> bool:
        return not self.changes()

    def validate(self, caps: Capabilities) -> Command:
        """Return a normalized copy, or raise ValueError with a user-facing message."""
        out: dict = {}
        for key, value in self.changes().items():
            if key in ("power", "health", "sleep"):
                if not isinstance(value, bool):
                    raise ValueError(f"{key} must be true or false")
                if key in OPTIONAL_FEATURES and key not in caps.features:
                    raise ValueError(f"{key} is not supported by this device")
            elif key == "mode":
                _require_choice("mode", value, caps.modes)
            elif key == "fan":
                _require_choice("fan", value, caps.fan_speeds)
            elif key == "swing":
                _require_choice("swing", value, caps.swing)
            elif key == "target_temp":
                value = _clamp_temperature(value, caps)
            out[key] = value
        return Command(**out)


def _require_choice(name: str, value: object, allowed: list[str]) -> None:
    if value not in allowed:
        raise ValueError(f"{name} must be one of: {', '.join(allowed)}")


def _clamp_temperature(value: object, caps: Capabilities) -> int:
    if isinstance(value, bool):
        raise ValueError("target_temp must be a number")
    try:
        number = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        raise ValueError("target_temp must be a number") from None
    if not math.isfinite(number):
        raise ValueError("target_temp must be a number")
    step = max(1, caps.temp_step)
    rounded = int(round(number / step) * step)
    return max(caps.temp_min, min(caps.temp_max, rounded))


def apply_command(state: State, cmd: Command) -> State:
    return replace(state, **cmd.changes())
