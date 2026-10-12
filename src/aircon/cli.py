"""aircon: command-line client for the aircond daemon."""

from __future__ import annotations

import argparse
import json
import re
import socket
import sys
from datetime import datetime, timedelta
from pathlib import Path

from aircon import menu, setup, setup_cli
from aircon.config import default_config_path, default_socket_path

REQUEST_TIMEOUT = 15.0


class CliError(Exception):
    pass


# -- socket client ------------------------------------------------------------------


def _connect(path: Path) -> socket.socket:
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    sock.settimeout(REQUEST_TIMEOUT)
    try:
        sock.connect(str(path))
    except (FileNotFoundError, ConnectionRefusedError) as exc:
        sock.close()
        raise CliError(f"aircond is not running (no socket at {path})") from exc
    return sock


def request(payload: dict, device: str | None = None) -> dict:
    if device:
        payload = {**payload, "device": device}
    path = default_socket_path()
    with _connect(path) as sock:
        sock.sendall((json.dumps(payload) + "\n").encode())
        line = sock.makefile("r", encoding="utf-8").readline()
    if not line:
        raise CliError("aircond closed the connection")
    reply = json.loads(line)
    if not reply.get("ok"):
        raise CliError(reply.get("error") or "request failed")
    return reply["state"]


def watch() -> int:
    path = default_socket_path()
    try:
        with _connect(path) as sock:
            sock.settimeout(None)
            sock.sendall(b'{"op": "watch"}\n')
            for line in sock.makefile("r", encoding="utf-8"):
                sys.stdout.write(line)
                sys.stdout.flush()
    except CliError as exc:
        print(f"aircon: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 0
    print("aircon: aircond closed the connection", file=sys.stderr)
    return 1


def pick(snapshot: dict, device: str | None) -> dict:
    """The device a command targets: -d ID, or the daemon's default."""
    devices = snapshot.get("devices") or []
    if not snapshot.get("configured") or not devices:
        raise CliError("not configured; run: aircon setup")
    wanted = device or snapshot.get("default") or devices[0]["id"]
    for entry in devices:
        if entry["id"] == wanted:
            return entry
    known = ", ".join(d["id"] for d in devices)
    raise CliError(f"unknown device {wanted!r} (known: {known})")


# -- parsing helpers ----------------------------------------------------------------

_DURATION = re.compile(r"^\s*(?:(\d+)\s*h)?\s*(?:(\d+)\s*m?)?\s*$")


def parse_duration(text: str) -> int:
    """'90m', '2h', '1h30m' or plain minutes -> minutes."""
    match = _DURATION.match(text.lower())
    if not match or not any(match.groups()):
        raise ValueError(f"invalid duration {text!r} (use e.g. 90m, 2h, 1h30m)")
    hours, minutes = (int(g) if g else 0 for g in match.groups())
    total = hours * 60 + minutes
    if total <= 0:
        raise ValueError("duration must be positive")
    return total


def parse_clock(text: str, now: datetime | None = None) -> datetime:
    """'HH:MM' -> the next local time with that clock reading."""
    now = now or datetime.now().astimezone()
    try:
        hour, minute = (int(part) for part in text.split(":"))
        at = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    except ValueError:
        raise ValueError(f"invalid time {text!r} (use HH:MM)") from None
    return at if at > now else at + timedelta(days=1)


def _on_off(value: str) -> bool:
    if value in ("on", "true", "1", "yes"):
        return True
    if value in ("off", "false", "0", "no"):
        return False
    raise argparse.ArgumentTypeError("use on or off")


# -- output -------------------------------------------------------------------------


def format_device(state: dict) -> str:
    name = state.get("name") or state.get("id") or "Air conditioner"
    if not state.get("connected"):
        return f"{name}: unreachable ({state.get('last_error') or 'not connected'})"
    room = []
    if state.get("room_temp") is not None:
        room.append(f"{state['room_temp']}°C")
    if state.get("room_humidity") is not None:
        room.append(f"{state['room_humidity']}%")
    room_text = f" (room {', '.join(room)})" if room else ""
    if state.get("power"):
        line = (f"{name}: on · {state['mode']} · {state['target_temp']}°C{room_text}"
                f" · fan {state['fan']} · swing {state['swing']}")
        extras = [key for key in ("health", "sleep") if state.get(key)]
        if extras:
            line += " · " + ", ".join(extras)
    else:
        line = f"{name}: off{room_text}"
    details = []
    for action, when in (state.get("timers") or {}).items():
        details.append(f"  timer: power {action} at {when[11:16]}")
    curve = state.get("sleep_curve")
    if curve:
        details.append(f"  sleep curve: {curve['target']}°C until {curve['ends_at'][11:16]}")
    filt = state.get("filter") or {}
    if filt.get("due"):
        details.append(f"  filter: clean it ({filt['hours']} h of use)")
    return "\n".join([line, *details])


def format_status(snapshot: dict, device: str | None = None) -> str:
    if not snapshot.get("configured"):
        return f"Not configured: {snapshot.get('last_error') or 'no config'}\nRun: aircon setup"
    if device:
        return format_device(pick(snapshot, device))
    return "\n".join(format_device(d) for d in snapshot.get("devices") or [])


# -- commands -----------------------------------------------------------------------


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="aircon", description="Control air conditioners.")
    parser.add_argument("-d", "--device", help="device id (default: the configured default)")
    sub = parser.add_subparsers(dest="command", required=True)

    status = sub.add_parser("status", help="show the current state")
    status.add_argument("--json", action="store_true", help="print the state as JSON")
    sub.add_parser("on", help="power on")
    sub.add_parser("off", help="power off")
    sub.add_parser("toggle", help="toggle power")
    sub.add_parser("is-on", help="exit 0 if on, 1 otherwise")
    sub.add_parser("watch", help="stream state changes as JSON lines")

    all_parser = sub.add_parser("all", help="power every air conditioner on or off")
    all_parser.add_argument("action", choices=["on", "off"])

    set_parser = sub.add_parser("set", help="change one or more settings")
    set_parser.add_argument("--mode")
    set_parser.add_argument("--temp", type=float, dest="target_temp")
    set_parser.add_argument("--fan")
    set_parser.add_argument("--swing")
    set_parser.add_argument("--health", type=_on_off)
    set_parser.add_argument("--sleep", type=_on_off)

    temp = sub.add_parser("temp", help="set the target temperature: 24, +1 or -1")
    temp.add_argument("value")

    timer = sub.add_parser("timer", help="power on/off later, or cancel a timer")
    timer.add_argument("action", choices=["on", "off", "cancel"])
    timer.add_argument("cancel_action", nargs="?", choices=["on", "off"])
    when = timer.add_mutually_exclusive_group()
    when.add_argument("--at", help="HH:MM")
    when.add_argument("--in", dest="in_", help="duration, e.g. 90m or 2h")

    curve = sub.add_parser("sleep-curve", help="start or cancel the sleep curve")
    curve.add_argument("action", choices=["start", "cancel"])
    curve.add_argument("--hours", type=int, default=8)
    curve.add_argument("--off-at-end", action="store_true")

    filt = sub.add_parser("filter", help="filter maintenance")
    filt.add_argument("action", choices=["reset"])

    devices = sub.add_parser("devices", help="list configured air conditioners")
    devices.add_argument("--json", action="store_true")

    for name, help_text in (("rename", "rename an air conditioner"),
                            ("remove", "remove an air conditioner")):
        cmd = sub.add_parser(name, help=help_text)
        cmd.add_argument("id")
        if name == "rename":
            cmd.add_argument("name")
        cmd.add_argument("--config", type=Path, default=None, help=argparse.SUPPRESS)
        cmd.add_argument("--no-restart", action="store_true", help=argparse.SUPPRESS)

    menu_parser = sub.add_parser("menu", help="Omarchy menu entries")
    menu_parser.add_argument("action", choices=["sync", "remove"])

    setup_cli.add_parser(sub)
    return parser


def _manage(args: argparse.Namespace) -> int:
    path = args.config or default_config_path()
    try:
        if args.command == "rename":
            device = setup.rename_device(path, args.id, args.name)
            message = f"Renamed {device.id} to {device.name}"
        else:
            setup.remove_device(path, args.id)
            message = f"Removed {args.id}"
    except (LookupError, FileNotFoundError) as exc:
        raise CliError(str(exc).strip("'\"")) from exc
    restarted = setup_cli.apply_changes(args)
    print(message + ("; restarted aircond" if restarted else ""))
    return 0


def _devices(args: argparse.Namespace) -> int:
    config = setup.load_or_empty(default_config_path())
    rows = [{"id": d.id, "name": d.name, "host": d.host, "backend": d.backend,
             "default": d.id == config.default_id} for d in config.devices]
    if args.json:
        print(json.dumps(rows))
    elif not rows:
        print("No air conditioners configured. Run: aircon setup")
    else:
        for row in rows:
            mark = " (default)" if row["default"] else ""
            print(f"{row['id']:<16} {row['name']:<20} {row['host']:<16} {row['backend']}{mark}")
    return 0


def _run(args: argparse.Namespace) -> int:
    command, device = args.command, args.device
    if command == "setup":
        return setup_cli.run(args)
    if command in ("rename", "remove"):
        return _manage(args)
    if command == "devices":
        return _devices(args)
    if command == "menu":
        path = menu.default_menu_path()
        if args.action == "remove":
            menu.remove(path)
        else:
            menu.sync(path, setup.load_or_empty(default_config_path()))
        print(f"Updated {path}")
        return 0
    if command == "watch":
        return watch()
    if command == "status":
        snapshot = request({"op": "get"})
        if args.json:
            print(json.dumps(pick(snapshot, device) if device else snapshot))
        else:
            print(format_status(snapshot, device))
        return 0
    if command == "is-on":
        try:
            state = pick(request({"op": "get"}), device)
        except CliError:
            return 1
        return 0 if state.get("connected") and state.get("power") else 1
    if command == "all":
        snapshot = request({"op": "set", "power": args.action == "on"}, "all")
        print(format_status(snapshot))
        return 0

    if command in ("on", "off"):
        snapshot = request({"op": "set", "power": command == "on"}, device)
    elif command == "toggle":
        current = pick(request({"op": "get"}), device)
        snapshot = request({"op": "set", "power": not current.get("power")}, current["id"])
    elif command == "set":
        changes = {key: getattr(args, key)
                   for key in ("mode", "target_temp", "fan", "swing", "health", "sleep")
                   if getattr(args, key) is not None}
        if not changes:
            raise CliError("nothing to change; see aircon set --help")
        snapshot = request({"op": "set", **changes}, device)
    elif command == "temp":
        value = args.value.strip()
        current = pick(request({"op": "get"}), device)
        target = current["target_temp"] + int(value) if value[:1] in "+-" else float(value)
        snapshot = request({"op": "set", "target_temp": target}, current["id"])
        device = current["id"]
    elif command == "timer":
        if args.action == "cancel":
            if not args.cancel_action:
                raise CliError("which timer? use: aircon timer cancel on|off")
            snapshot = request({"op": "timer_cancel", "action": args.cancel_action}, device)
        else:
            if args.at:
                at = parse_clock(args.at)
            elif args.in_:
                at = datetime.now().astimezone() + timedelta(minutes=parse_duration(args.in_))
            else:
                raise CliError("use --at HH:MM or --in DURATION")
            snapshot = request({"op": "timer", "action": args.action, "at": at.isoformat()},
                               device)
    elif command == "sleep-curve":
        if args.action == "start":
            snapshot = request({"op": "sleep_curve", "hours": args.hours,
                                "off_at_end": args.off_at_end}, device)
        else:
            snapshot = request({"op": "sleep_curve_cancel"}, device)
    elif command == "filter":
        snapshot = request({"op": "filter_reset"}, device)
    else:  # pragma: no cover - argparse guards this
        raise CliError(f"unknown command {command}")
    print(format_device(pick(snapshot, device)))
    return 0


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    # Let "aircon temp -1" through: argparse would read "-1" as an option.
    if "temp" in argv:
        index = argv.index("temp")
        if index + 1 < len(argv) and re.fullmatch(r"-\d+", argv[index + 1]):
            argv = argv[: index + 1] + ["--"] + argv[index + 1 :]
    args = _build_parser().parse_args(argv)
    try:
        return _run(args)
    except (CliError, ValueError) as exc:
        print(f"aircon: {exc}", file=sys.stderr)
        return 1
    except (TimeoutError, OSError) as exc:
        print(f"aircon: cannot talk to aircond: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
