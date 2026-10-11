"""On-device validation for the Haier SmartAir2 backend.

Talks to the real air conditioner directly (no daemon). Every subcommand that changes
the device restores the state it found at the start.

    uv run python tools/haier_probe.py --host 192.168.1.50 --mac 04FA83A1B2C3 status
    uv run python tools/haier_probe.py --host ... --mac ... set mode=heat target_temp=22
    uv run python tools/haier_probe.py --host ... --mac ... validate
"""

from __future__ import annotations

import argparse
import asyncio
import json
import socket
from dataclasses import replace

from aircon.backends.haier_smartair2 import protocol as p
from aircon.backends.haier_smartair2.backend import HaierSmartAir2Backend
from aircon.config import DeviceConfig
from aircon.model import Command, State

SETTLE_SECONDS = 2.5
FEATURES = ["health", "sleep", "swing_horizontal"]


def raw_summary(frame: bytes | None) -> str:
    if frame is None:
        return "-"
    return (
        f"swing={frame[p.OFF_SWING]:#04x} power={frame[p.OFF_POWER]:#04x} "
        f"flags={frame[p.OFF_FLAGS]:#04x} locks={frame[p.OFF_LOCKS]:#04x}"
    )


async def connect(host: str, mac: str, swing_style: str) -> HaierSmartAir2Backend:
    device = DeviceConfig(
        name="probe", backend="haier_smartair2", host=host, mac=mac,
        swing_style=swing_style, features=FEATURES,
    )
    backend = HaierSmartAir2Backend(device, poll_interval=3600)
    await backend.start()
    async with asyncio.timeout(10):
        while not backend.connected:
            await asyncio.sleep(0.05)
    await backend.refresh()
    return backend


async def settle(backend: HaierSmartAir2Backend) -> State:
    await asyncio.sleep(SETTLE_SECONDS)
    return await backend.refresh()


async def check(backend, label: str, cmd: Command, field: str, expected) -> bool:
    try:
        await backend.apply(cmd)
        state = await settle(backend)
    except (TimeoutError, ConnectionError) as exc:
        print(f"  FAIL {label}: {exc}")
        return False
    ok = getattr(state, field) == expected
    print(f"  {'ok  ' if ok else 'FAIL'} {label}: {field}={getattr(state, field)!r} "
          f"[{raw_summary(backend.last_frame)}]")
    return ok


async def restore(backend: HaierSmartAir2Backend, original: State) -> None:
    print(f"restoring {original}")
    if original.power:
        await backend.apply(Command(
            mode=original.mode, fan=original.fan, target_temp=original.target_temp,
            swing=original.swing, health=original.health, sleep=original.sleep,
        ))
    else:
        await backend.apply(Command(power=False))
    state = await settle(backend)
    print(f"restored  {state}")


def second_connection(host: str, mac: str) -> str:
    try:
        with socket.create_connection((host, 56800), timeout=3) as sock:
            sock.sendall(p.build_request(mac, 200, p.QUERY))
            sock.settimeout(3)
            data = sock.recv(4096)
            frames, _ = p.parse_stream(data)
            return f"accepted, {len(frames)} frame(s) received"
    except OSError as exc:
        return f"refused/failed: {exc}"


async def validate(host: str, mac: str) -> None:
    backend = await connect(host, mac, "bits")
    original = replace(backend.state)
    print(f"start     {original} [{raw_summary(backend.last_frame)}]")
    results: dict[str, bool] = {}
    try:
        results["target_temp"] = await check(
            backend, "temp 28", Command(target_temp=28), "target_temp", 28)
        results["fan"] = await check(backend, "fan auto", Command(fan="auto"), "fan", "auto")
        results["mode"] = await check(backend, "mode fan", Command(mode="fan"), "mode", "fan")
        await backend.apply(Command(mode="cool"))
        for position in ("vertical", "horizontal", "both", "off"):
            results[f"swing_bits_{position}"] = await check(
                backend, f"swing {position} (bits)", Command(swing=position), "swing", position)
        results["health_on"] = await check(
            backend, "health on", Command(health=True), "health", True)
        results["health_off"] = await check(
            backend, "health off", Command(health=False), "health", False)
        results["sleep_on"] = await check(backend, "sleep on", Command(sleep=True), "sleep", True)
        results["sleep_off"] = await check(
            backend, "sleep off", Command(sleep=False), "sleep", False)
        results["power_off"] = await check(
            backend, "power off", Command(power=False), "power", False)
        results["power_on"] = await check(backend, "power on", Command(power=True), "power", True)
        print(f"second connection while connected: {second_connection(host, mac)}")
        print(f"backend still connected: {backend.connected}")
    finally:
        await restore(backend, original)
        await backend.close()

    if not all(results.get(f"swing_bits_{s}") for s in ("vertical", "horizontal", "both")):
        print("retrying swing with the 'byte' style")
        backend = await connect(host, mac, "byte")
        original = replace(backend.state)
        try:
            for position in ("vertical", "horizontal", "both", "off"):
                results[f"swing_byte_{position}"] = await check(
                    backend, f"swing {position} (byte)", Command(swing=position), "swing",
                    position)
        finally:
            await restore(backend, original)
            await backend.close()

    print(json.dumps(results, indent=2))


async def show_status(host: str, mac: str, style: str) -> None:
    backend = await connect(host, mac, style)
    print(json.dumps(backend.state.to_dict(), indent=2))
    print(raw_summary(backend.last_frame))
    print("frame:", backend.last_frame.hex(" ") if backend.last_frame else "-")
    await backend.close()


async def set_values(host: str, mac: str, style: str, pairs: list[str]) -> None:
    values: dict = {}
    for pair in pairs:
        key, _, raw = pair.partition("=")
        values[key] = json.loads(raw) if raw in ("true", "false") or raw.isdigit() else raw
    backend = await connect(host, mac, style)
    await backend.apply(Command.from_dict(values).validate(backend.capabilities))
    print(json.dumps((await settle(backend)).to_dict(), indent=2))
    print(raw_summary(backend.last_frame))
    await backend.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--host", required=True)
    parser.add_argument("--mac", required=True)
    parser.add_argument("--swing-style", default="bits", choices=p.SWING_STYLES)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("status")
    sub.add_parser("validate")
    set_parser = sub.add_parser("set")
    set_parser.add_argument("pairs", nargs="+", metavar="key=value")
    args = parser.parse_args()

    if args.command == "status":
        asyncio.run(show_status(args.host, args.mac, args.swing_style))
    elif args.command == "validate":
        asyncio.run(validate(args.host, args.mac))
    else:
        asyncio.run(set_values(args.host, args.mac, args.swing_style, args.pairs))


if __name__ == "__main__":
    main()
