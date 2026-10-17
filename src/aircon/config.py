"""Configuration loaded from ~/.config/aircon/config.toml."""

from __future__ import annotations

import json
import os
import re
import stat
import tomllib
import unicodedata
from collections.abc import Iterable
from dataclasses import dataclass, field, fields
from pathlib import Path

ID_PATTERN = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
MAC_PATTERN = re.compile(r"^[0-9A-F]{12}$")
SWING_STYLES = ("bits", "byte")


def default_config_path() -> Path:
    override = os.environ.get("AIRCON_CONFIG")
    if override:
        return Path(override)
    base = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    return Path(base) / "aircon" / "config.toml"


def default_socket_path() -> Path:
    override = os.environ.get("AIRCON_SOCKET")
    if override:
        return Path(override)
    runtime = os.environ.get("XDG_RUNTIME_DIR")
    if runtime:
        return Path(runtime) / "aircon.sock"
    # Shared /tmp: only use a directory that is ours and private.
    fallback = Path(f"/tmp/aircon-{os.getuid()}")
    fallback.mkdir(mode=0o700, exist_ok=True)
    info = os.lstat(fallback)
    if (not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid()
            or info.st_mode & 0o077):
        raise PermissionError(f"refusing to use {fallback}: not a private directory of yours")
    return fallback / "aircon.sock"


def slugify(name: str) -> str:
    """'Café lounge' -> 'cafe-lounge'; falls back to 'ac'."""
    ascii_name = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode()
    slug = re.sub(r"[^a-z0-9]+", "-", ascii_name.lower()).strip("-")
    return slug or "ac"


def unique_id(base: str, taken: Iterable[str]) -> str:
    taken = set(taken)
    if base not in taken:
        return base
    n = 2
    while f"{base}-{n}" in taken:
        n += 1
    return f"{base}-{n}"


@dataclass
class DeviceConfig:
    name: str
    backend: str
    host: str
    mac: str = ""
    # 0 means the backend's default port (Haier SmartAir2: 56800, ESPHome: 80).
    port: int = 0
    # Haier only: how swing is encoded ("bits" or "byte"), see haier_smartair2/protocol.py.
    swing_style: str = "bits"
    # Optional capabilities confirmed on the device, e.g. "health", "sleep", "swing_horizontal".
    features: list[str] = field(default_factory=list)
    # Stable identifier used by the CLI, the socket, the menu and the state file.
    id: str = ""
    # Backend-specific settings (e.g. a local key or token), as scalars only.
    options: dict[str, str | int | float | bool] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.id:
            self.id = slugify(self.name)


@dataclass
class Config:
    devices: list[DeviceConfig] = field(default_factory=list)
    default: str | None = None
    filter_threshold_hours: int = 300

    @property
    def default_id(self) -> str | None:
        if self.default and any(d.id == self.default for d in self.devices):
            return self.default
        return self.devices[0].id if self.devices else None

    @property
    def device(self) -> DeviceConfig:
        """The default device."""
        found = self.get(self.default_id or "")
        if found is None:
            raise LookupError("no devices configured")
        return found

    def get(self, device_id: str) -> DeviceConfig | None:
        return next((d for d in self.devices if d.id == device_id), None)


def _device_from_table(table: dict, path: Path) -> DeviceConfig:
    allowed = {f.name for f in fields(DeviceConfig)}
    unknown = sorted(set(table) - allowed)
    if unknown:
        raise ValueError(f"{path}: unknown device key(s): {', '.join(unknown)}")
    missing = [key for key in ("name", "backend", "host") if key not in table]
    if missing:
        raise ValueError(f"{path}: a device is missing: {', '.join(missing)}")
    options = table.get("options", {})
    if not isinstance(options, dict) or any(
        isinstance(v, (dict, list)) for v in options.values()
    ):
        raise ValueError(f"{path}: device options must be a table of plain values")
    for key in ("name", "backend", "host"):
        if not isinstance(table[key], str) or not table[key].strip():
            raise ValueError(f"{path}: device {key} must be a non-empty string")
    device = DeviceConfig(**table)
    if not ID_PATTERN.match(device.id):
        raise ValueError(f"{path}: invalid device id {device.id!r} (use a-z, 0-9 and -)")
    from aircon.backends import BACKENDS  # late import: the backends import this module

    if device.backend not in BACKENDS:
        raise ValueError(f"{path}: unknown backend {device.backend!r} "
                         f"(available: {', '.join(sorted(BACKENDS))})")
    if device.swing_style not in SWING_STYLES:
        raise ValueError(f"{path}: swing_style must be one of: {', '.join(SWING_STYLES)}")
    if device.mac:
        device.mac = device.mac.replace(":", "").replace("-", "").upper()
        if not MAC_PATTERN.match(device.mac):
            raise ValueError(f"{path}: invalid mac {table['mac']!r} (12 hex digits)")
    return device


def load_config(path: Path | None = None) -> Config:
    path = path or default_config_path()
    if not path.exists():
        raise FileNotFoundError(f"no config at {path}; run: aircon setup")
    with path.open("rb") as fh:
        data = tomllib.load(fh)

    raw = data.get("device")
    if isinstance(raw, dict):  # legacy single [device] table
        tables = [raw]
    elif isinstance(raw, list):
        tables = raw
    else:
        tables = []
    devices = [_device_from_table(table, path) for table in tables]

    ids = [d.id for d in devices]
    duplicates = sorted({i for i in ids if ids.count(i) > 1})
    if duplicates:
        raise ValueError(f"{path}: duplicate device id(s): {', '.join(duplicates)}")
    default = data.get("default")
    if default is not None and default not in ids:
        raise ValueError(f"{path}: default {default!r} is not a configured device")

    filter_table = data.get("filter", {})
    if not isinstance(filter_table, dict):
        raise ValueError(f"{path}: [filter] must be a table")
    threshold = int(filter_table.get("threshold_hours", 300))
    return Config(devices=devices, default=default, filter_threshold_hours=threshold)


def _toml_value(value: str | int | float | bool) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return repr(value)
    return json.dumps(str(value))  # ASCII escapes are valid TOML for any character


def _inline_table(values: dict) -> str:
    items = []
    for key, value in values.items():
        name = key if re.fullmatch(r"[A-Za-z0-9_-]+", key) else json.dumps(key)
        items.append(f"{name} = {_toml_value(value)}")
    return "{ " + ", ".join(items) + " }"


def dump_config(config: Config, header: str = "") -> str:
    """Serialize to TOML (always the [[device]] list form)."""

    def q(value: str) -> str:
        return json.dumps(value)  # ASCII escapes are valid TOML for any character

    lines = [header.rstrip(), ""] if header else []
    if config.default:
        lines += [f"default = {q(config.default)}", ""]
    for device in config.devices:
        features = ", ".join(q(f) for f in device.features)
        lines += [
            "[[device]]",
            f"id = {q(device.id)}",
            f"name = {q(device.name)}",
            f"backend = {q(device.backend)}",
            f"host = {q(device.host)}",
            f"mac = {q(device.mac)}",
        ]
        if device.port:
            lines.append(f"port = {int(device.port)}")
        lines += [
            f"swing_style = {q(device.swing_style)}",
            f"features = [{features}]",
        ]
        if device.options:
            lines.append(f"options = {_inline_table(device.options)}")
        lines.append("")
    lines += ["[filter]", f"threshold_hours = {int(config.filter_threshold_hours)}", ""]
    return "\n".join(lines)
