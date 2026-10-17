"""`aircon setup`: find an air conditioner on the local network and add it to the config."""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path

from aircon import menu, setup
from aircon.config import default_config_path

DEFAULT_NAME = setup.DEFAULT_NAME

NOT_FOUND_HELP = """No air conditioner was found on the local network.

Check that:
  - a Haier Wi-Fi module was paired once with the HaierSmartAir2 app (not hOn), or an
    ESPHome board runs a climate component with `web_server:` enabled;
  - the air conditioner and this computer are on the same network;
  - the unit is plugged in.

If you know its address, run:
  aircon setup apply --host <ip> --mac <MAC printed on the Wi-Fi module>
  aircon setup apply --backend esphome --host <ip> --mac <board MAC>"""

FEATURE_WARNING = (
    "Test optional features (left/right swing, health, sleep)?\n"
    "  This changes the air conditioner for about a minute, then restores it. [y/N] "
)


def add_parser(sub: argparse._SubParsersAction) -> None:
    # Options shared by every setup command. SUPPRESS keeps a subcommand's defaults from
    # overwriting values given before the subcommand name.
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--config", type=Path, default=argparse.SUPPRESS,
                        help="config file (default: ~/.config/aircon/config.toml)")
    common.add_argument("--no-restart", action="store_true", default=argparse.SUPPRESS,
                        help="do not restart aircond afterwards")
    common.add_argument("--port", type=int, default=argparse.SUPPRESS,
                        help=f"device port (default: {setup.DEFAULT_PORT} for Haier, "
                             f"{setup.ESPHOME_PORT} for ESPHome)")
    common.add_argument("--hosts", nargs="+", default=argparse.SUPPRESS,
                        help="only probe these IP addresses")
    common.add_argument("--settle", type=float, default=argparse.SUPPRESS,
                        help=argparse.SUPPRESS)
    common.add_argument("--esphome-port", type=int, default=argparse.SUPPRESS,
                        help=argparse.SUPPRESS)

    parser = sub.add_parser(
        "setup", parents=[common], help="find an air conditioner and add it",
        description="Without a subcommand, runs an interactive setup.",
    )
    setup_sub = parser.add_subparsers(dest="setup_command")

    discover = setup_sub.add_parser("discover", parents=[common],
                                    help="list air conditioners found on the network")
    discover.add_argument("--json", action="store_true")

    apply = setup_sub.add_parser("apply", parents=[common],
                                 help="add (or update) a known device")
    apply.add_argument("--host", required=True)
    apply.add_argument("--backend", choices=[setup.DEFAULT_BACKEND, setup.ESPHOME_BACKEND],
                       default=setup.DEFAULT_BACKEND,
                       help=f"how to talk to it (default: {setup.DEFAULT_BACKEND})")
    apply.add_argument("--mac", required=True)
    apply.add_argument("--name", default=None,
                       help=f"name to show (default: keep the current one, or {DEFAULT_NAME!r})")
    apply.add_argument("--validate", action="store_true",
                       help="test optional features on the device (changes it briefly)")
    apply.add_argument("--json", action="store_true")


def run(args: argparse.Namespace) -> int:
    command = getattr(args, "setup_command", None)
    if command == "discover":
        return _discover(args)
    if command == "apply":
        return _apply(args)
    return _wizard(args)


# -- helpers --------------------------------------------------------------------------------


def _opt(args: argparse.Namespace, name: str, default):
    return getattr(args, name, default)


def _port(args: argparse.Namespace, backend: str = setup.DEFAULT_BACKEND) -> int:
    default = setup.ESPHOME_PORT if backend == setup.ESPHOME_BACKEND else setup.DEFAULT_PORT
    return _opt(args, "port", default)


def config_path(args: argparse.Namespace) -> Path:
    return _opt(args, "config", None) or default_config_path()


def apply_changes(args: argparse.Namespace) -> bool:
    """Refresh the menu entries (only if the user added them) and restart the daemon."""
    try:
        path = menu.default_menu_path()
        if menu.is_installed(path):
            menu.sync(path, setup.load_or_empty(config_path(args)))
    except (OSError, ValueError) as exc:  # a broken menu file must not block the restart
        print(f"aircon: could not update the Omarchy menu: {exc}")
    if _opt(args, "no_restart", False):
        return False
    return setup.restart_daemon()


def describe(candidate: setup.Candidate) -> str:
    vendor = "unknown vendor" if candidate.vendor == "Unknown" else candidate.vendor
    mac = setup.compact_mac(candidate.mac) if candidate.mac else "MAC unknown"
    state = candidate.state
    if state is None:
        status = "does not answer status queries"
    else:
        status = f"on · {state.mode} · {state.target_temp}°C" if state.power else "off"
        if state.room_temp is not None:
            status += f" (room {state.room_temp}°C)"
    added = "  [already added]" if candidate.configured else ""
    return f"{candidate.host}  {mac}  {vendor} · {status}{added}"


def _print_check(label: str, ok: bool) -> None:
    print(f"  {'ok' if ok else 'no':<3} {label}", flush=True)


def _ask(prompt: str, default: str = "") -> str:
    try:
        answer = input(prompt).strip()
    except EOFError:
        answer = ""
    return answer or default


def _save(args: argparse.Namespace, name: str | None, host: str, mac: str,
          validation: setup.ValidationResult | None,
          backend: str = setup.DEFAULT_BACKEND, port: int | None = None) -> dict:
    path = config_path(args)
    device, created = setup.add_device(path, name=name, host=host, mac=mac,
                                       port=_port(args, backend) if port is None else port,
                                       backend=backend, validation=validation)
    restarted = apply_changes(args)
    return {
        "ok": True,
        "id": device.id,
        "name": device.name,
        "created": created,
        "config": str(path),
        "restarted": restarted,
        "swing_style": device.swing_style,
        "features": device.features,
        "checks": validation.checks if validation else {},
        "restored": validation.restored if validation else True,
    }


def _report_saved(args: argparse.Namespace, result: dict) -> None:
    verb = "Added" if result["created"] else "Updated"
    print(f"{verb} {result['name']} (id: {result['id']}) in {result['config']}")
    if not result["restored"]:
        print("Warning: the air conditioner did not answer while restoring its settings; "
              "check it with the remote.")
    if result["restarted"]:
        print("Restarted aircond.")
    elif not _opt(args, "no_restart", False):
        print("Could not restart aircond; run: systemctl --user restart aircond")


def _discover_candidates(args: argparse.Namespace) -> list[setup.Candidate]:
    return asyncio.run(setup.discover(
        hosts=_opt(args, "hosts", None), port=_port(args),
        configured_macs=setup.configured_macs(config_path(args)),
        esphome_port=_opt(args, "esphome_port", setup.ESPHOME_PORT),
    ))


# -- commands -------------------------------------------------------------------------------


def _discover(args: argparse.Namespace) -> int:
    candidates = _discover_candidates(args)
    if args.json:
        print(json.dumps([c.to_dict() for c in candidates]))
        return 0
    if not candidates:
        print(NOT_FOUND_HELP)
        return 1
    for candidate in candidates:
        print(describe(candidate))
    return 0


def _apply(args: argparse.Namespace) -> int:
    backend = args.backend
    port = _port(args, backend)
    if backend == setup.ESPHOME_BACKEND:
        state = asyncio.run(setup.read_esphome_state(args.host, port))
        error = (f"no ESPHome air conditioner at {args.host}:{port}; check the address and "
                 "that the board has `web_server:` enabled")
    else:
        state = asyncio.run(setup.read_state(args.host, args.mac, port))
        error = f"no answer from {args.host}:{port}; check the address and the MAC"
    if state is None:
        print(json.dumps({"ok": False, "error": error}) if args.json else f"aircon: {error}")
        return 1
    validation = None
    if args.validate and backend != setup.DEFAULT_BACKEND:
        error = "--validate only applies to Haier SmartAir2 modules"
        print(json.dumps({"ok": False, "error": error}) if args.json else f"aircon: {error}")
        return 2
    if args.validate:
        report = (lambda label, ok: None) if args.json else _print_check
        validation = asyncio.run(setup.validate_features(
            args.host, args.mac, port, _opt(args, "settle", 2.5), report))
    result = _save(args, args.name, args.host, args.mac, validation, backend)
    if args.json:
        print(json.dumps(result))
    else:
        _report_saved(args, result)
    return 0


def _wizard(args: argparse.Namespace) -> int:
    print("Looking for air conditioners on the local network...", flush=True)
    candidates = _discover_candidates(args)
    usable = [c for c in candidates if c.state is not None and c.mac and not c.configured]
    if not usable:
        if any(c.configured for c in candidates):
            print("Every air conditioner found is already added:")
            for candidate in candidates:
                print(f"  {describe(candidate)}")
        else:
            print(NOT_FOUND_HELP)
        return 1

    if len(usable) == 1:
        choice = usable[0]
        print(f"Found {describe(choice)}")
        if _ask("Add it? [Y/n] ", "y").lower() not in ("y", "yes"):
            print("Nothing changed.")
            return 1
    else:
        for number, candidate in enumerate(usable, start=1):
            print(f"  {number}) {describe(candidate)}")
        answer = _ask("Which one? [1] ", "1")
        if not answer.isdigit() or not 1 <= int(answer) <= len(usable):
            print("Nothing changed.")
            return 1
        choice = usable[int(answer) - 1]

    name = _ask(f"Name [{DEFAULT_NAME}]: ", DEFAULT_NAME)
    validation = None
    # Only Haier modules need the feature test; an ESPHome board reports what the unit supports.
    if choice.backend == setup.DEFAULT_BACKEND:
        if _ask(FEATURE_WARNING, "n").lower() in ("y", "yes"):
            print("Testing...", flush=True)
            validation = asyncio.run(setup.validate_features(
                choice.host, choice.mac or "", choice.port, _opt(args, "settle", 2.5),
                _print_check))
        else:
            print("Skipped; only the basic controls will be offered. "
                  "Re-run aircon setup to test.")

    result = _save(args, name, choice.host, choice.mac or "", validation, choice.backend,
                   choice.port)
    _report_saved(args, result)
    print("Done. Open the panel from the bar, or run: aircon status")
    return 0
