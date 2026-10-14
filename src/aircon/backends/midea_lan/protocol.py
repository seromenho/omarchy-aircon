"""Midea settings and model mapping. Optional imports and transport details live here."""

from __future__ import annotations

import math
import re
from dataclasses import dataclass

from aircon.config import DeviceConfig
from aircon.model import Capabilities, Command, State

MODES = {"auto": "AUTO", "cool": "COOL", "heat": "HEAT", "fan": "FAN_ONLY", "dry": "DRY"}
FANS = {"low": "LOW", "mid": "MEDIUM", "high": "HIGH", "auto": "AUTO"}
SWINGS = {"off": "OFF", "vertical": "VERTICAL", "horizontal": "HORIZONTAL", "both": "BOTH"}
DEPENDENCY_HELP = "Midea support requires msmart-ng; run ./install.sh --midea"


@dataclass(frozen=True)
class Settings:
    device_id: int
    version: int
    port: int
    token: str | None
    key: str | None


def settings(device: DeviceConfig) -> Settings:
    options = device.options
    unknown = set(options) - {"device_id", "protocol_version", "token", "key"}
    if unknown:
        raise ValueError(f"unknown Midea option(s): {', '.join(sorted(unknown))}")
    raw_id = options.get("device_id", "")
    if isinstance(raw_id, bool) or not re.fullmatch(r"[0-9]+", str(raw_id)):
        raise ValueError("Midea needs options.device_id (a positive decimal integer)")
    device_id = int(raw_id)
    if not 0 < device_id < 2**48:
        raise ValueError("Midea device_id must fit in 48 bits and be positive")
    token, key = options.get("token"), options.get("key")
    version = options.get("protocol_version", 3 if token or key else 2)
    if isinstance(version, bool) or version not in (2, 3):
        raise ValueError("Midea protocol_version must be 2 or 3 (V1 is unsupported)")
    if version == 3:
        for name, value in (("token", token), ("key", key)):
            if (not isinstance(value, str) or not value or len(value) % 2
                    or not re.fullmatch(r"[0-9a-fA-F]+", value)):
                raise ValueError(f"Midea V3 requires a hexadecimal options.{name}")
        if len(key) != 64:
            raise ValueError("Midea V3 key must contain 64 hexadecimal characters")
    elif token or key:
        raise ValueError("Midea token/key require protocol_version = 3")
    # DeviceConfig's legacy default is the Haier port. Midea uses 6444.
    port = 6444 if device.port == 56800 else device.port
    if isinstance(port, bool) or not isinstance(port, int) or not 1 <= port <= 65535:
        raise ValueError("Midea port must be between 1 and 65535")
    return Settings(device_id, version, port, token, key)


def create_client(device: DeviceConfig, config: Settings):
    try:
        from msmart.device import AirConditioner
    except ImportError:
        raise RuntimeError(DEPENDENCY_HELP) from None
    return AirConditioner(ip=device.host, port=config.port, device_id=config.device_id,
                          version=config.version)


def close_client(client) -> None:
    # msmart-ng has no public close API. Keep this single private API use isolated here.
    client._lan._disconnect()


def capabilities(client, features: list[str]) -> Capabilities:
    def choices(mapping: dict, enum, supported: list) -> list[str]:
        return [name for name, member in mapping.items() if getattr(enum, member) in supported]

    optional = ["sleep"] if "sleep" in features else []
    if "health" in features and client.supports_purifier:
        optional.append("health")
    swing = choices(SWINGS, client.SwingMode, client.supported_swing_modes)
    if "swing_horizontal" not in features:
        swing = [position for position in swing if position in ("off", "vertical")]
    return Capabilities(
        modes=choices(MODES, client.OperationalMode, client.supported_operation_modes),
        fan_speeds=choices(FANS, client.FanSpeed, client.supported_fan_speeds),
        swing=swing,
        temp_min=math.ceil(client.min_target_temperature),
        temp_max=math.floor(client.max_target_temperature),
        features=optional,
    )


def decode_state(client) -> State:
    def choice(mapping: dict, value, fallback: str) -> str:
        return next((k for k, v in mapping.items() if v == value.name), fallback)

    fan = client.fan_speed
    # Some units report a custom percentage rather than one of the named speeds.
    fan_name = next((k for k, v in FANS.items() if fan == getattr(client.FanSpeed, v)), None)
    if fan_name is None:
        fan_name = "auto" if fan > 100 else "low" if fan <= 40 else "mid" if fan <= 60 else "high"
    error = client.error_code
    return State(
        power=bool(client.power_state),
        mode=choice(MODES, client.operational_mode, "dry"),
        fan=fan_name,
        target_temp=client.target_temperature,
        room_temp=client.indoor_temperature,
        swing=choice(SWINGS, client.swing_mode, "off"),
        sleep=bool(client.sleep),
        health=bool(client.purifier),
        alarm=f"Midea error {error}" if error else None,
    )


def encode_command(client, command: Command) -> None:
    """Mutate only requested settings, preserving extra Midea settings and half degrees."""
    for field, value in command.changes().items():
        if field == "mode":
            client.operational_mode = getattr(client.OperationalMode, MODES[value])
        elif field == "fan":
            client.fan_speed = getattr(client.FanSpeed, FANS[value])
        elif field == "swing":
            client.swing_mode = getattr(client.SwingMode, SWINGS[value])
        else:
            attr = {"power": "power_state", "target_temp": "target_temperature",
                    "health": "purifier", "sleep": "sleep"}[field]
            setattr(client, attr, value)
    if command.power is None and command.changes() and not client.power_state:
        client.power_state = True


async def discover(hosts=None, *, timeout: float = 5) -> list[dict]:
    try:
        from msmart.discover import Discover
    except ImportError:
        raise RuntimeError(DEPENDENCY_HELP) from None
    found: dict[int, dict] = {}
    # Discover uses class-level settings; run each target sequentially.
    for target in hosts if hosts is not None else ["255.255.255.255"]:
        for client in await Discover.discover(target=target, timeout=timeout, auto_connect=False):
            if client.type != 0xAC or client.version not in (2, 3):
                continue
            found[client.id] = {
                "host": client.ip, "port": client.port,
                "options": {"device_id": str(client.id), "protocol_version": client.version},
            }
    return list(found.values())
