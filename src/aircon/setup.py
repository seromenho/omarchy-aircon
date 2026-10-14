"""Finding and configuring an air conditioner: LAN discovery, feature validation, config file."""

from __future__ import annotations

import asyncio
import contextlib
import ipaddress
import json
import os
import shutil
import subprocess
import time
import tomllib
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field, replace
from pathlib import Path

from aircon.backends.haier_smartair2.backend import HaierSmartAir2Backend
from aircon.config import (
    MAC_PATTERN,
    Config,
    DeviceConfig,
    dump_config,
    load_config,
    slugify,
    unique_id,
)
from aircon.model import Command, State

DEFAULT_PORT = 56800
DEFAULT_BACKEND = "haier_smartair2"
HAIER_OUIS = ("04FA83",)  # Qingdao Haier Technology
MAX_HOSTS = 1024
ALL_HAIER_FEATURES = ["health", "sleep", "swing_horizontal"]
DEFAULT_NAME = "Air conditioner"
KEEP_BACKUPS = 3


def discovery_preferences(config_path: Path) -> dict:
    """Optional local setup preferences; contains addresses, never tokens or keys."""
    path = config_path.with_name("setup.toml")
    if not path.exists():
        return {}
    with path.open("rb") as fh:
        preferences = tomllib.load(fh)
    backend = preferences.get("backend")
    if backend not in ("midea_lan", "haier_smartair2"):
        raise ValueError(f"{path}: setup backend must be midea_lan or haier_smartair2")
    hosts = preferences.get("hosts")
    if hosts is not None and (not isinstance(hosts, list) or not hosts
                              or any(not isinstance(h, str) or not h.strip() for h in hosts)):
        raise ValueError(f"{path}: setup hosts must be a non-empty list of addresses")
    return preferences


# -- network helpers ------------------------------------------------------------------


def _run_json(args: list[str]) -> list:
    try:
        result = subprocess.run(args, capture_output=True, text=True, timeout=5, check=False)
        return json.loads(result.stdout or "[]")
    except (OSError, subprocess.SubprocessError, json.JSONDecodeError):
        return []


def default_subnets() -> list[ipaddress.IPv4Network]:
    """IPv4 networks of the interfaces that carry the default route."""
    devices = {route.get("dev") for route in _run_json(["ip", "-j", "-4", "route", "show",
                                                         "default"])}
    networks: list[ipaddress.IPv4Network] = []
    for iface in _run_json(["ip", "-j", "-4", "addr", "show"]):
        if iface.get("ifname") not in devices:
            continue
        for addr in iface.get("addr_info", []):
            if addr.get("family") != "inet":
                continue
            prefix = max(int(addr.get("prefixlen", 24)), 22)  # never sweep more than /22
            network = ipaddress.ip_network(f"{addr['local']}/{prefix}", strict=False)
            if network not in networks:
                networks.append(network)
    return networks


def read_arp(path: Path = Path("/proc/net/arp")) -> dict[str, str]:
    """IP -> MAC (lowercase, colon separated) from the kernel ARP table."""
    table: dict[str, str] = {}
    try:
        lines = path.read_text().splitlines()[1:]
    except OSError:
        return table
    for line in lines:
        parts = line.split()
        if len(parts) >= 4 and parts[3] != "00:00:00:00:00:00":
            table[parts[0]] = parts[3].lower()
    return table


def compact_mac(mac: str) -> str:
    return mac.replace(":", "").replace("-", "").upper()


async def _port_open(host: str, port: int, timeout: float) -> bool:
    try:
        _reader, writer = await asyncio.wait_for(asyncio.open_connection(host, port), timeout)
    except (OSError, TimeoutError):
        return False
    writer.close()
    with contextlib.suppress(OSError):
        await writer.wait_closed()
    return True


async def scan(hosts: Iterable[str], port: int = DEFAULT_PORT, timeout: float = 0.8,
               concurrency: int = 128) -> list[str]:
    """Hosts that accept a TCP connection on `port`."""
    semaphore = asyncio.Semaphore(concurrency)

    async def probe(host: str) -> str | None:
        async with semaphore:
            return host if await _port_open(host, port, timeout) else None

    found = await asyncio.gather(*(probe(host) for host in hosts))
    return [host for host in found if host]


# -- device access ----------------------------------------------------------------------


def _backend(host: str, mac: str, port: int, swing_style: str = "bits",
             features: list[str] | None = None) -> HaierSmartAir2Backend:
    device = DeviceConfig(name="setup", backend=DEFAULT_BACKEND, host=host,
                          mac=compact_mac(mac), port=port, swing_style=swing_style,
                          features=list(features or []))
    return HaierSmartAir2Backend(device, poll_interval=3600, timeout=2.0)


async def _connected(backend: HaierSmartAir2Backend, timeout: float) -> None:
    await backend.start()
    async with asyncio.timeout(timeout):
        while not backend.connected:
            await asyncio.sleep(0.05)
    await backend.refresh()


async def read_state(host: str, mac: str, port: int = DEFAULT_PORT,
                     timeout: float = 5.0) -> State | None:
    backend = _backend(host, mac, port)
    try:
        await _connected(backend, timeout)
        return backend.state if backend.last_frame is not None else None
    except (TimeoutError, ConnectionError, OSError):
        return None
    finally:
        await backend.close()


@dataclass
class Candidate:
    host: str
    mac: str | None
    state: State | None
    port: int = DEFAULT_PORT
    configured: bool = False
    backend: str = DEFAULT_BACKEND
    options: dict = field(default_factory=dict)

    @property
    def is_haier(self) -> bool:
        return (self.backend == DEFAULT_BACKEND and bool(self.mac)
                and compact_mac(self.mac).startswith(HAIER_OUIS))

    def to_dict(self) -> dict:
        result = {
            "host": self.host,
            "mac": compact_mac(self.mac) if self.mac else None,
            "port": self.port,
            "backend": self.backend,
            "is_haier": self.is_haier,
            "configured": self.configured,
            "state": self.state.to_dict() if self.state else None,
        }
        if self.options:
            result["options"] = self.options
        return result


async def discover_midea(hosts: Iterable[str] | None = None,
                         configured_ids: Iterable[str] = (), *,
                         known_hosts: Iterable[str] = ()) -> list[Candidate]:
    """Discover metadata locally, without authentication, cloud access or state changes."""
    from aircon.backends.midea_lan import protocol

    known = {str(device_id) for device_id in configured_ids}
    hints = list(dict.fromkeys(known_hosts))
    targets = hosts if hosts is not None else (["255.255.255.255", *hints] if hints else None)
    items = await protocol.discover(targets)
    if hosts is None:
        local_hosts = [str(h) for net in default_subnets()
                       for h in list(net.hosts())[:MAX_HOSTS]]
        if local_hosts:
            # Finding a known AC must not stop the search for other devices. Saved
            # addresses supplement broadcast and the subnet sweep; only explicit
            # CLI --hosts restricts the search to particular addresses.
            items += await protocol.discover_unicast(local_hosts)
            # A sweep of unknown addresses can fill the router's ARP queue and lose
            # replies. Retry now-known neighbors on a quiet socket and merge the results.
            allowed = set(local_hosts)
            neighbors = [host for host in read_arp() if host in allowed]
            if neighbors:
                # Use the library's repeated per-host requests on separate sockets:
                # shared UDP flows to many peers can still lose replies under UFW.
                items += await protocol.discover(neighbors)
    by_id = {str(item["options"]["device_id"]): item for item in items}
    return [Candidate(host=item["host"], mac=None, state=None, port=item["port"],
                      backend="midea_lan", options=item["options"],
                      configured=str(item["options"]["device_id"]) in known)
            for item in by_id.values()]


async def discover(
    hosts: Iterable[str] | None = None,
    port: int = DEFAULT_PORT,
    mac_lookup: Callable[[str], str | None] | None = None,
    configured_macs: Iterable[str] = (),
) -> list[Candidate]:
    """Find devices answering on the SmartAir2 port and read their state."""
    known = {compact_mac(m) for m in configured_macs if m}
    if hosts is None:
        hosts = [str(h) for net in default_subnets() for h in list(net.hosts())[:MAX_HOSTS]]
    open_hosts = await scan(hosts, port)
    lookup = mac_lookup or (lambda ip: read_arp().get(ip))
    candidates = []
    macs = [lookup(host) for host in open_hosts]

    async def no_state() -> None:
        return None

    states = await asyncio.gather(*(read_state(host, mac, port) if mac else no_state()
                                    for host, mac in zip(open_hosts, macs, strict=True)))
    for host, mac, state in zip(open_hosts, macs, states, strict=True):
        configured = bool(mac) and compact_mac(mac) in known
        candidates.append(Candidate(host=host, mac=mac, state=state, port=port,
                                    configured=configured))
    candidates.sort(key=lambda c: (not c.is_haier, c.state is None,
                                   ipaddress.ip_address(c.host)))
    return candidates


# -- feature validation -------------------------------------------------------------------


@dataclass
class ValidationResult:
    swing_style: str = "bits"
    features: list[str] = field(default_factory=list)
    checks: dict[str, bool] = field(default_factory=dict)
    restored: bool = True  # False if the unit could not be put back as it was


Reporter = Callable[[str, bool], None]


async def _check(backend: HaierSmartAir2Backend, cmd: Command, field_name: str, expected,
                 settle: float) -> bool:
    try:
        await backend.apply(cmd)
        await asyncio.sleep(settle)
        state = await backend.refresh()
    except (TimeoutError, ConnectionError):
        return False
    return getattr(state, field_name) == expected


async def _restore(backend: HaierSmartAir2Backend, original: State) -> bool:
    """Put every setting back, then the power state. Returns False if the unit didn't answer."""
    try:
        await backend.apply(Command(
            mode=original.mode, fan=original.fan, target_temp=original.target_temp,
            swing=original.swing, health=original.health, sleep=original.sleep,
        ))
        if not original.power:
            await backend.apply(Command(power=False))
    except (TimeoutError, ConnectionError):
        return False
    return True


async def _swing_checks(host: str, mac: str, port: int, style: str, settle: float,
                        report: Reporter) -> dict[str, bool]:
    backend = _backend(host, mac, port, style, ALL_HAIER_FEATURES)
    await _connected(backend, 10)
    original = replace(backend.state)
    results: dict[str, bool] = {}
    try:
        if not original.power or original.mode not in ("cool", "heat"):
            await backend.apply(Command(mode="cool"))
        for position in ("vertical", "horizontal", "both", "off"):
            ok = await _check(backend, Command(swing=position), "swing", position, settle)
            results[position] = ok
            report(f"swing {position} ({style})", ok)
        if style == "bits":
            for feature in ("health", "sleep"):
                on = await _check(backend, Command(**{feature: True}), feature, True, settle)
                off = await _check(backend, Command(**{feature: False}), feature, False, settle)
                results[feature] = on and off
                report(feature, on and off)
    finally:
        results["_restored"] = await _restore(backend, original)
        await backend.close()
    return results


async def validate_features(host: str, mac: str, port: int = DEFAULT_PORT,
                            settle: float = 2.5,
                            report: Reporter = lambda label, ok: None) -> ValidationResult:
    """Exercise optional features on the real unit, then restore its state."""
    checks = await _swing_checks(host, mac, port, "bits", settle, report)
    restored = checks.pop("_restored")
    style = "bits"
    swing_ok = all(checks.get(p) for p in ("vertical", "horizontal", "both"))
    if not swing_ok:
        byte_checks = await _swing_checks(host, mac, port, "byte", settle, report)
        restored = byte_checks.pop("_restored") and restored
        if all(byte_checks.get(p) for p in ("vertical", "horizontal", "both")):
            style, swing_ok = "byte", True
            checks.update({f"{k}_byte": v for k, v in byte_checks.items()})
    features = [f for f in ("health", "sleep") if checks.get(f)]
    if swing_ok:
        features.append("swing_horizontal")
    return ValidationResult(swing_style=style, features=features, checks=checks,
                            restored=restored)


# -- config file ---------------------------------------------------------------------------


HEADER = ("# aircon configuration. `aircon setup` adds air conditioners;\n"
          "# `aircon rename` and `aircon remove` manage them.")


def load_or_empty(path: Path) -> Config:
    try:
        return load_config(path)
    except FileNotFoundError:
        return Config()


def configured_macs(path: Path) -> set[str]:
    try:
        return {compact_mac(d.mac) for d in load_or_empty(path).devices if d.mac}
    except ValueError:
        return set()


def save_config(path: Path, config: Config) -> Path | None:
    return write_config(path, dump_config(config, HEADER))


def add_device(path: Path, *, name: str | None, host: str, mac: str = "", port: int = DEFAULT_PORT,
               backend: str = DEFAULT_BACKEND,
               options: dict | None = None,
               validation: ValidationResult | None = None) -> tuple[DeviceConfig, bool]:
    """Add a device, or update the one with the same MAC. Returns (device, created).

    Without a name, an existing device keeps its name and a new one gets a default.
    """
    from aircon.backends import BACKENDS

    if backend not in BACKENDS:
        raise ValueError(f"unknown backend {backend!r}")
    if backend == DEFAULT_BACKEND and not MAC_PATTERN.match(compact_mac(mac)):
        raise ValueError(f"invalid MAC address {mac!r} (12 hex digits)")
    config = load_or_empty(path)
    existing = next((d for d in config.devices if d.backend == backend and (
        (backend == DEFAULT_BACKEND and d.mac and compact_mac(d.mac) == compact_mac(mac))
        or (backend == "midea_lan"
            and str(d.options.get("device_id")) == str((options or {}).get("device_id")))
    )), None)
    if backend == "midea_lan":
        from aircon.backends.midea_lan.protocol import settings

        options = {**(existing.options if existing else {}), **(options or {})}
        parsed = settings(DeviceConfig(name=name or DEFAULT_NAME, backend=backend,
                                       host=host, port=port, options=options))
        port = parsed.port
    if existing is not None:
        existing.host, existing.port = host, port
        if name:
            existing.name = name
        if options is not None:
            existing.options = dict(options)
        if validation is not None:
            existing.swing_style = validation.swing_style
            existing.features = list(validation.features)
        save_config(path, config)
        return existing, False
    name = name or DEFAULT_NAME
    device = DeviceConfig(
        name=name, backend=backend, host=host, mac=compact_mac(mac), port=port,
        swing_style=validation.swing_style if validation else "bits",
        features=list(validation.features) if validation else [],
        id=unique_id(slugify(name), (d.id for d in config.devices)),
        options=dict(options or {}),
    )
    config.devices.append(device)
    save_config(path, config)
    return device, True


def remove_device(path: Path, device_id: str) -> Config:
    config = load_config(path)
    if config.get(device_id) is None:
        raise LookupError(f"unknown device {device_id!r}")
    config.devices = [d for d in config.devices if d.id != device_id]
    if config.default == device_id:
        config.default = None
    save_config(path, config)
    return config


def rename_device(path: Path, device_id: str, name: str) -> DeviceConfig:
    if not name.strip():
        raise ValueError("the name cannot be empty")
    config = load_config(path)
    device = config.get(device_id)
    if device is None:
        raise LookupError(f"unknown device {device_id!r}")
    device.name = name.strip()
    save_config(path, config)
    return device


def write_config(path: Path, text: str) -> Path | None:
    """Write the config atomically and privately (it may hold keys); returns the backup path.

    A symlinked config stays a symlink, and only the last few backups are kept.
    """
    target = path.resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    backup = None
    if target.exists():
        backup = target.with_name(f"{target.name}.bak.{int(time.time())}")
        shutil.copy2(target, backup)
        backup.chmod(0o600)
        old = sorted(target.parent.glob(f"{target.name}.bak.*"))[:-KEEP_BACKUPS]
        for stale in old:
            stale.unlink(missing_ok=True)
    tmp = target.with_suffix(".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as fh:
        fh.write(text)
    os.chmod(tmp, 0o600)
    os.replace(tmp, target)
    return backup


def restart_daemon() -> bool:
    try:
        result = subprocess.run(["systemctl", "--user", "restart", "aircond.service"],
                                capture_output=True, timeout=15, check=False)
    except (OSError, subprocess.SubprocessError):
        return False
    return result.returncode == 0
