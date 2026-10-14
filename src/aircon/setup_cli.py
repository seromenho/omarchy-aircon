"""`aircon setup`: find an air conditioner on the local network and add it to the config."""

from __future__ import annotations

import argparse
import asyncio
import getpass
import json
import sys
import tomllib
from pathlib import Path

from aircon import menu, setup
from aircon.config import DeviceConfig, default_config_path

DEFAULT_NAME = setup.DEFAULT_NAME

NOT_FOUND_HELP = """No air conditioner answered for Haier SmartAir2 on TCP port {port}.

Check that:
  - the unit's Wi-Fi module was paired once with the HaierSmartAir2 app (not hOn);
  - the air conditioner and this computer are on the same network;
  - the unit is plugged in.

If you know its address, run:
  aircon setup apply --backend haier_smartair2 --host <ip> --mac <module MAC>

For Midea / SmartHome, choose Midea in setup or run:
  aircon setup --backend midea_lan"""

MIDEA_NOT_FOUND_HELP = """No Midea air conditioner was found on the local network.

Pair the unit with SmartHome (formerly MSmartHome), and check that it is plugged in
and connected to the same network as this computer.

If you know its address, run:
  aircon setup discover --backend midea_lan --hosts <ip>

V3 setup can sign in to SmartHome once to retrieve its local token/key.
See docs/midea-setup.md for pairing and account instructions."""

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
                        help="device port (default: Midea 6444, Haier 56800)")
    common.add_argument("--hosts", nargs="+", default=argparse.SUPPRESS,
                        help="only probe these IP addresses")
    common.add_argument("--backend", choices=("haier_smartair2", "midea_lan"),
                        default=argparse.SUPPRESS,
                        help="device backend (interactive setup asks which brand)")
    common.add_argument("--settle", type=float, default=argparse.SUPPRESS,
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
    apply.add_argument("--mac", help="Haier Wi-Fi module MAC (required for Haier)")
    apply.add_argument("--device-id", help="Midea numeric device ID")
    apply.add_argument("--protocol-version", type=int, choices=(2, 3))
    auth = apply.add_mutually_exclusive_group()
    auth.add_argument("--options-file", type=Path,
                      help="Midea options in a private TOML file (device_id, token, key, etc.)")
    auth.add_argument("--smarthome", action="store_true",
                      help="sign in to SmartHome once, verify local credentials and add the AC")
    apply.add_argument("--credentials-stdin", action="store_true",
                       help="read SmartHome email/password as one JSON line from stdin")
    apply.add_argument("--name", default=None,
                       help=f"name to show (default: keep the current one, or {DEFAULT_NAME!r})")
    apply.add_argument("--validate", action="store_true",
                       help="test optional features on the device (changes it briefly)")
    apply.add_argument("--json", action="store_true")


def run(args: argparse.Namespace) -> int:
    command = getattr(args, "setup_command", None)
    if command != "apply":
        preferences = setup.discovery_preferences(config_path(args))
        if preferences:
            if not hasattr(args, "backend"):
                args.backend = preferences["backend"]
            if args.backend == preferences["backend"] and not hasattr(args, "hosts"):
                if args.backend == "midea_lan":
                    args.discovery_hints = preferences.get("hosts") or []
                else:
                    args.hosts = preferences.get("hosts")
    if command == "discover":
        return _discover(args)
    if command == "apply":
        return _apply(args)
    if not hasattr(args, "backend"):
        if not sys.stdin.isatty():
            raise ValueError("choose a brand: --backend midea_lan (SmartHome / Midea) "
                             "or --backend haier_smartair2 (HaierSmartAir2)")
        print("Choose your air conditioner:\n"
              "  1) Midea — SmartHome / Midea Air\n"
              "  2) Haier — HaierSmartAir2")
        choice = _ask("Brand [1]: ", "1")
        if choice not in ("1", "2"):
            raise ValueError("choose 1 for Midea or 2 for Haier")
        args.backend = "midea_lan" if choice == "1" else "haier_smartair2"
    if args.backend == "midea_lan":
        return _wizard_midea(args)
    return _wizard(args)


# -- helpers --------------------------------------------------------------------------------


def _opt(args: argparse.Namespace, name: str, default):
    return getattr(args, name, default)


def _port(args: argparse.Namespace) -> int:
    default = 6444 if _opt(args, "backend", setup.DEFAULT_BACKEND) == "midea_lan" else 56800
    return _opt(args, "port", default)


def _not_found_help(args: argparse.Namespace) -> str:
    if _opt(args, "backend", setup.DEFAULT_BACKEND) == "midea_lan":
        return MIDEA_NOT_FOUND_HELP
    return NOT_FOUND_HELP.format(port=_port(args))


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
    if candidate.backend == "midea_lan":
        return (f"{candidate.host}:{candidate.port}  Midea · "
                f"device ID {candidate.options['device_id']} · "
                f"V{candidate.options['protocol_version']}"
                + (" · already added" if candidate.configured else ""))
    vendor = "Haier" if candidate.is_haier else "unknown vendor"
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
          validation: setup.ValidationResult | None) -> dict:
    path = config_path(args)
    device, created = setup.add_device(path, name=name, host=host, mac=mac, port=_port(args),
                                       validation=validation)
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
    if _opt(args, "backend", setup.DEFAULT_BACKEND) == "midea_lan":
        devices = [d for d in setup.load_or_empty(config_path(args)).devices
                   if d.backend == "midea_lan"]
        ids = [str(d.options.get("device_id")) for d in devices]
        hosts = _opt(args, "hosts", None)
        hints = (list(_opt(args, "discovery_hints", [])) + [d.host for d in devices]
                 if hosts is None else [])
        try:
            return asyncio.run(setup.discover_midea(hosts, ids, known_hosts=hints))
        except RuntimeError as exc:
            raise ValueError(str(exc)) from None
    return asyncio.run(setup.discover(
        hosts=_opt(args, "hosts", None), port=_port(args),
        configured_macs=setup.configured_macs(config_path(args)),
    ))


# -- commands -------------------------------------------------------------------------------


def _discover(args: argparse.Namespace) -> int:
    candidates = _discover_candidates(args)
    if args.json:
        print(json.dumps([c.to_dict() for c in candidates]))
        return 0
    if not candidates:
        print(_not_found_help(args))
        return 1
    for candidate in candidates:
        print(describe(candidate))
    return 0


def _apply(args: argparse.Namespace) -> int:
    if _opt(args, "credentials_stdin", False) and not _opt(args, "smarthome", False):
        raise ValueError("--credentials-stdin requires --smarthome")
    if _opt(args, "smarthome", False) and _opt(args, "backend", "") != "midea_lan":
        raise ValueError("--smarthome requires --backend midea_lan")
    if _opt(args, "backend", setup.DEFAULT_BACKEND) == "midea_lan":
        return _apply_midea(args)
    if not args.mac:
        raise ValueError("Haier setup requires --mac")
    port = _port(args)
    state = asyncio.run(setup.read_state(args.host, args.mac, port))
    if state is None:
        error = f"no answer from {args.host}:{port}; check the address and the MAC"
        print(json.dumps({"ok": False, "error": error}) if args.json else f"aircon: {error}")
        return 1
    validation = None
    if args.validate:
        report = (lambda label, ok: None) if args.json else _print_check
        validation = asyncio.run(setup.validate_features(
            args.host, args.mac, port, _opt(args, "settle", 2.5), report))
    result = _save(args, args.name, args.host, args.mac, validation)
    if args.json:
        print(json.dumps(result))
    else:
        _report_saved(args, result)
    return 0


def _apply_midea(args: argparse.Namespace) -> int:
    if args.validate:
        raise ValueError("Midea --validate is not supported; optional features stay disabled")
    options = {}
    if args.options_file:
        with args.options_file.expanduser().open("rb") as fh:
            options = tomllib.load(fh)
    if args.device_id is not None:
        options["device_id"] = args.device_id
    if args.protocol_version is not None:
        options["protocol_version"] = args.protocol_version
    if _opt(args, "smarthome", False):
        from aircon.backends.midea_lan.credentials import fetch_credentials
        from aircon.backends.midea_lan.protocol import settings

        if args.protocol_version not in (None, 3):
            raise ValueError("SmartHome sign-in is only needed for Midea V3 units")
        # Validate the selected device before asking for or reading account credentials.
        settings(DeviceConfig(name="Midea setup", backend="midea_lan", host=args.host,
                              port=_port(args), options={"device_id": args.device_id or ""}))
        email, password = _smarthome_login(_opt(args, "credentials_stdin", False))
        options = asyncio.run(fetch_credentials(args.host, args.device_id, email, password,
                                                port=_port(args)))
        del email, password
    device, created = setup.add_device(
        config_path(args), name=args.name, host=args.host,
        port=_opt(args, "port", 6444), backend="midea_lan", options=options,
    )
    result = {
        "ok": True, "id": device.id, "name": device.name, "created": created,
        "config": str(config_path(args)), "restarted": apply_changes(args), "restored": True,
    }
    if args.json:
        print(json.dumps(result))
    else:
        _report_saved(args, result)
        print("Settings saved; aircond will connect in the background. Run: aircon status")
    return 0


def _smarthome_login(from_stdin: bool) -> tuple[str, str]:
    if from_stdin:
        if sys.stdin.isatty():
            raise ValueError("--credentials-stdin requires a JSON line from a pipe")
        line = sys.stdin.readline(16385)
        try:
            payload = json.loads(line) if len(line) <= 16384 else None
        except (ValueError, TypeError):
            payload = None
        if not isinstance(payload, dict) or set(payload) != {"email", "password"}:
            raise ValueError("Expected a JSON object with SmartHome email and password on stdin")
        email, password = payload["email"], payload["password"]
    else:
        if not sys.stdin.isatty():
            raise ValueError("Use --credentials-stdin for non-interactive SmartHome setup")
        print("Sign in to SmartHome once to retrieve your AC's local token/key.\n"
              "Your account password is not saved. The AC must already be added in SmartHome.")
        email = _ask("SmartHome email: ")
        password = getpass.getpass("SmartHome password (hidden): ")
    if (not isinstance(email, str) or not email.strip() or len(email) > 4096
            or not isinstance(password, str) or not password or len(password) > 4096):
        raise ValueError("Enter your SmartHome email and password")
    return email.strip(), password


def _wizard(args: argparse.Namespace) -> int:
    print("Looking for Haier SmartAir2 air conditioners on the local network...", flush=True)
    candidates = _discover_candidates(args)
    usable = [c for c in candidates if c.state is not None and c.mac and not c.configured]
    if not usable:
        if any(c.configured for c in candidates):
            print("Every air conditioner found is already added:")
            for candidate in candidates:
                print(f"  {describe(candidate)}")
        else:
            print(_not_found_help(args))
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
    if _ask(FEATURE_WARNING, "n").lower() in ("y", "yes"):
        print("Testing...", flush=True)
        validation = asyncio.run(setup.validate_features(
            choice.host, choice.mac or "", choice.port, _opt(args, "settle", 2.5),
            _print_check))
    else:
        print("Skipped; only the basic controls will be offered. Re-run aircon setup to test.")

    result = _save(args, name, choice.host, choice.mac or "", validation)
    _report_saved(args, result)
    print("Done. Open the panel from the bar, or run: aircon status")
    return 0


def _wizard_midea(args: argparse.Namespace) -> int:
    hosts = _opt(args, "hosts", None)
    location = ", ".join(hosts) if hosts else "the local network"
    print(f"Looking for Midea air conditioners on {location}...", flush=True)
    candidates = _discover_candidates(args)
    usable = [c for c in candidates if not c.configured]
    if not usable:
        if candidates:
            print("Every Midea air conditioner found is already added:")
            for candidate in candidates:
                print(f"  {describe(candidate)}")
        else:
            print(_not_found_help(args))
        return 1
    for number, candidate in enumerate(usable, start=1):
        print(f"  {number}) {describe(candidate)}")
    answer = _ask("Which one? [1] ", "1")
    if not answer.isdigit() or not 1 <= int(answer) <= len(usable):
        print("Nothing changed.")
        return 1
    choice = usable[int(answer) - 1]
    name = _ask(f"Name [{DEFAULT_NAME}]: ", DEFAULT_NAME)
    options_file = None
    smarthome = False
    if choice.options["protocol_version"] == 3:
        print("This unit needs a local token/key.\n"
              "  1) Sign in to SmartHome (password is not saved)\n"
              "  2) Use a saved token/key TOML file\n"
              "See docs/midea-setup.md for pairing and account instructions.")
        method = _ask("Credentials [1]: ", "1")
        if method == "1":
            smarthome = True
        elif method == "2":
            path = _ask("Token/key file path (leave empty to cancel): ")
            if not path:
                print("Nothing changed.")
                return 1
            options_file = Path(path)
        else:
            print("Nothing changed.")
            return 1
    apply_args = argparse.Namespace(**{
        **vars(args), "host": choice.host, "port": choice.port, "name": name,
        "device_id": choice.options["device_id"],
        "protocol_version": choice.options["protocol_version"],
        "options_file": options_file, "smarthome": smarthome, "credentials_stdin": False,
        "validate": False, "json": False,
    })
    return _apply_midea(apply_args)
