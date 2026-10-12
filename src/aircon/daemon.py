"""aircond: owns the device connections and serves state over a unix socket (JSON lines)."""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import json
import logging
import os
import signal
import sys
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

from aircon import __version__
from aircon.backends import create_backend
from aircon.backends.base import Backend
from aircon.config import (
    Config,
    DeviceConfig,
    default_config_path,
    default_socket_path,
    load_config,
)
from aircon.model import OPTIONAL_FEATURES, Command
from aircon.scheduler import Clock, Scheduler, local_now
from aircon.store import Store, default_state_path

log = logging.getLogger("aircond")

DEVICE_ERRORS = (TimeoutError, ConnectionError)
ALL_DEVICES = "all"
NOT_CONFIGURED = "not configured; run: aircon setup"


@dataclass
class DeviceRuntime:
    config: DeviceConfig
    backend: Backend
    scheduler: Scheduler


class Daemon:
    def __init__(
        self,
        config: Config | None,
        socket_path: Path,
        state_path: Path,
        backends: dict[str, Backend] | None = None,
        tick_interval: float = 30.0,
        clock: Clock = local_now,
        config_error: str | None = None,
    ) -> None:
        # Without devices the daemon still serves `get`/`watch`, so clients can point the
        # user at `aircon setup` instead of failing to connect.
        self.config = config if config and config.devices else None
        self.config_error = config_error
        if config is not None and not config.devices and not config_error:
            self.config_error = "no air conditioners configured"
        self.socket_path = socket_path
        self.tick_interval = tick_interval
        self._clock = clock
        self.devices: dict[str, DeviceRuntime] = {}
        if self.config:
            for index, device in enumerate(self.config.devices):
                backend = (backends or {}).get(device.id) or create_backend(device)
                scheduler = Scheduler(
                    Store(state_path, section=device.id, adopt_legacy=index == 0),
                    self._scheduler_apply(device.id),
                    self.config.filter_threshold_hours,
                    clock,
                )
                backend.on_state(lambda _state: self._publish())
                backend.on_connection(lambda _connected: self._publish())
                self.devices[device.id] = DeviceRuntime(device, backend, scheduler)
        self._server: asyncio.Server | None = None
        self._tick_task: asyncio.Task | None = None
        self._watchers: set[asyncio.StreamWriter] = set()
        self._last_published: str | None = None

    # -- lifecycle ------------------------------------------------------------------

    async def start(self) -> None:
        for runtime in self.devices.values():
            await runtime.backend.start()
        self.socket_path.parent.mkdir(parents=True, exist_ok=True)
        if await _socket_in_use(self.socket_path):
            raise RuntimeError(f"another aircond is already listening on {self.socket_path}")
        with contextlib.suppress(FileNotFoundError):
            self.socket_path.unlink()
        self._server = await asyncio.start_unix_server(
            self._serve_client, path=str(self.socket_path)
        )
        os.chmod(self.socket_path, 0o600)
        if self.devices:
            self._tick_task = asyncio.create_task(self._tick_loop(), name="aircond-tick")
            log.info("managing %s", ", ".join(self.devices))
        else:
            log.warning("%s (%s)", NOT_CONFIGURED, self.config_error or "no config")
        log.info("listening on %s", self.socket_path)

    async def stop(self) -> None:
        if self._tick_task:
            self._tick_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._tick_task
        for writer in list(self._watchers):
            writer.close()
        if self._server:
            self._server.close()
            with contextlib.suppress(Exception):
                await asyncio.wait_for(self._server.wait_closed(), 1)
        for runtime in self.devices.values():
            await runtime.backend.close()
        with contextlib.suppress(FileNotFoundError):
            self.socket_path.unlink()

    # -- state ----------------------------------------------------------------------

    def device_snapshot(self, runtime: DeviceRuntime) -> dict:
        backend = runtime.backend
        return {
            "id": runtime.config.id,
            "name": runtime.config.name,
            "backend": backend.name,
            "host": runtime.config.host,
            "connected": backend.connected,
            "capabilities": backend.capabilities.to_dict(),
            **backend.state.to_dict(),
            "timers": runtime.scheduler.timers(),
            "sleep_curve": runtime.scheduler.sleep_curve(),
            "filter": runtime.scheduler.filter_status(),
            "last_error": backend.last_error,
        }

    def snapshot(self) -> dict:
        updated_at = self._clock().isoformat(timespec="seconds")
        if not self.devices or self.config is None:
            return {
                "version": __version__,
                "configured": False,
                "default": None,
                "devices": [],
                "last_error": self.config_error or NOT_CONFIGURED,
                "updated_at": updated_at,
            }
        return {
            "version": __version__,
            "configured": True,
            "default": self.config.default_id,
            "devices": [self.device_snapshot(rt) for rt in self.devices.values()],
            "updated_at": updated_at,
        }

    def _publish(self) -> None:
        snapshot = self.snapshot()
        comparable = json.dumps({k: v for k, v in snapshot.items() if k != "updated_at"},
                                sort_keys=True)
        if comparable == self._last_published:
            return
        self._last_published = comparable
        line = (json.dumps(snapshot) + "\n").encode()
        for writer in list(self._watchers):
            try:
                writer.write(line)
            except (ConnectionError, RuntimeError):
                self._watchers.discard(writer)

    async def _tick_loop(self) -> None:
        while True:
            for device_id, runtime in self.devices.items():
                try:
                    backend = runtime.backend
                    on = backend.connected and backend.state.power
                    runtime.scheduler.filter_tick(on)
                    await runtime.scheduler.tick(backend.state.power)
                except Exception:
                    log.exception("%s: scheduler tick failed", device_id)
            with contextlib.suppress(Exception):
                self._publish()
            await asyncio.sleep(self.tick_interval)

    def _scheduler_apply(self, device_id: str):
        async def apply(cmd: Command) -> None:
            backend = self.devices[device_id].backend
            # The scheduler is brand-agnostic: drop features this unit doesn't have.
            changes = {k: v for k, v in cmd.changes().items()
                       if k not in OPTIONAL_FEATURES or k in backend.capabilities.features}
            if changes:
                await backend.apply(Command(**changes).validate(backend.capabilities))
            self._publish()

        return apply

    # -- socket ---------------------------------------------------------------------

    async def _serve_client(self, reader: asyncio.StreamReader,
                            writer: asyncio.StreamWriter) -> None:
        try:
            while True:
                try:
                    line = await reader.readline()
                except ValueError:  # longer than the stream limit (64 KiB)
                    writer.write(b'{"ok": false, "error": "request too long"}\n')
                    break
                if not line:
                    break
                try:
                    request = json.loads(line)
                    if not isinstance(request, dict):
                        raise ValueError("request must be a JSON object")
                    response = await self._handle(request, writer)
                except DEVICE_ERRORS as exc:
                    response = {"ok": False, "error": str(exc) or "air conditioner unavailable"}
                except (ValueError, TypeError, OverflowError, RecursionError) as exc:
                    response = {"ok": False, "error": str(exc) or "invalid request"}
                except Exception as exc:
                    log.exception("request failed")
                    response = {"ok": False, "error": f"internal error: {exc}"}
                if response is not None:
                    if not response.get("ok"):
                        # Clients may have shown the change optimistically: resend the truth.
                        self._last_published = None
                        self._publish()
                    writer.write((json.dumps(response) + "\n").encode())
                    await writer.drain()
        except (ConnectionError, asyncio.IncompleteReadError):
            pass
        finally:
            self._watchers.discard(writer)
            writer.close()

    async def _handle(self, request: dict, writer: asyncio.StreamWriter) -> dict | None:
        op = request.get("op")
        if op == "get":
            return {"ok": True, "state": self.snapshot()}
        if op == "watch":
            self._watchers.add(writer)
            writer.write((json.dumps(self.snapshot()) + "\n").encode())
            return None
        if not self.devices or self.config is None:
            raise ValueError(NOT_CONFIGURED)

        target = request.get("device") or self.config.default_id
        if target == ALL_DEVICES:
            return await self._handle_all(op, request)
        runtime = self.devices.get(target)
        if runtime is None:
            known = ", ".join(self.devices)
            raise ValueError(f"unknown device {target!r} (known: {known})")
        await self._handle_device(op, request, runtime)
        self._publish()
        return {"ok": True, "state": self.snapshot()}

    async def _handle_device(self, op: str | None, request: dict,
                             runtime: DeviceRuntime) -> None:
        backend, scheduler = runtime.backend, runtime.scheduler
        caps = backend.capabilities
        if op == "set":
            fields = {k: v for k, v in request.items() if k not in ("op", "device")}
            cmd = Command.from_dict(fields).validate(caps)
            if cmd.is_empty():
                raise ValueError("nothing to change")
            if cmd.target_temp is not None:
                scheduler.note_manual_temp(cmd.target_temp)
            if cmd.power is False:
                scheduler.cancel_sleep_curve()
            await backend.apply(cmd)
        elif op == "timer":
            scheduler.set_timer(request.get("action", ""), self._timer_time(request))
        elif op == "timer_cancel":
            action = request.get("action")
            if action not in ("on", "off"):
                raise ValueError("action must be 'on' or 'off'")
            scheduler.cancel_timer(action)
        elif op == "sleep_curve":
            hours = int(request.get("hours", 8))
            scheduler.start_sleep_curve(backend.state, hours,
                                        bool(request.get("off_at_end", False)))
            if "sleep" in caps.features:
                await backend.apply(Command(sleep=True))
        elif op == "sleep_curve_cancel":
            scheduler.cancel_sleep_curve()
            if "sleep" in caps.features and backend.state.sleep:
                await backend.apply(Command(sleep=False))
        elif op == "filter_reset":
            scheduler.filter_reset()
        else:
            raise ValueError(f"unknown op {op!r}")

    async def _handle_all(self, op: str | None, request: dict) -> dict:
        extra = set(request) - {"op", "device", "power"}
        if op != "set" or extra or not isinstance(request.get("power"), bool):
            raise ValueError("device 'all' only supports set with power true/false")
        power = request["power"]
        errors = []
        for runtime in self.devices.values():
            try:
                if not power:
                    runtime.scheduler.cancel_sleep_curve()
                await runtime.backend.apply(Command(power=power))
            except DEVICE_ERRORS as exc:
                errors.append(f"{runtime.config.name}: {exc or 'unavailable'}")
        self._publish()
        if errors:
            return {"ok": False, "error": "; ".join(errors), "state": self.snapshot()}
        return {"ok": True, "state": self.snapshot()}

    def _timer_time(self, request: dict) -> datetime:
        if "at" in request:
            at = datetime.fromisoformat(str(request["at"]))
            return at if at.tzinfo else at.astimezone()
        if "in_minutes" in request:
            minutes = int(request["in_minutes"])
            if minutes <= 0:
                raise ValueError("in_minutes must be positive")
            return self._clock() + timedelta(minutes=minutes)
        raise ValueError("timer needs 'at' or 'in_minutes'")


async def _socket_in_use(path: Path) -> bool:
    try:
        _reader, writer = await asyncio.open_unix_connection(str(path))
    except OSError:
        return False
    writer.close()
    return True


def build_daemon(config: Config | None, socket_path: Path, state_path: Path,
                 config_error: str | None = None) -> Daemon:
    try:
        return Daemon(config, socket_path, state_path, config_error=config_error)
    except (ValueError, LookupError) as exc:
        # A config the backends reject: stay up and tell clients what is wrong.
        log.error("invalid configuration: %s", exc)
        return Daemon(None, socket_path, state_path, config_error=f"invalid config: {exc}")


async def _serve(config: Config | None, socket_path: Path, state_path: Path,
                 config_error: str | None) -> None:
    daemon = build_daemon(config, socket_path, state_path, config_error)
    await daemon.start()
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, stop.set)
    await stop.wait()
    log.info("stopping")
    await daemon.stop()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="aircond", description="aircon daemon")
    parser.add_argument("--config", type=Path, default=None,
                        help=f"config file (default: {default_config_path()})")
    parser.add_argument("--socket", type=Path, default=None,
                        help=f"unix socket (default: {default_socket_path()})")
    parser.add_argument("--state", type=Path, default=None,
                        help=f"state file (default: {default_state_path()})")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(levelname)s %(name)s: %(message)s",
        stream=sys.stderr,
    )
    config: Config | None = None
    config_error = None
    try:
        config = load_config(args.config)
    except (FileNotFoundError, ValueError) as exc:
        config_error = str(exc)
    asyncio.run(_serve(config, args.socket or default_socket_path(),
                       args.state or default_state_path(), config_error))
    return 0


if __name__ == "__main__":
    sys.exit(main())
