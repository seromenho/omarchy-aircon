"""asyncio client for an air conditioner driven by an ESPHome board (web_server component).

Live state comes from the board's `/events` stream (Server-Sent Events); `refresh()` reads
the climate entity directly and `apply()` posts to its `/set` endpoint. The stream is
reopened with exponential backoff whenever it drops or goes quiet.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
from collections.abc import Callable
from urllib.parse import quote, urlencode

from aircon.config import DeviceConfig
from aircon.model import FAN_SPEEDS, MODES, SWING_POSITIONS, Capabilities, Command, State

from . import http
from . import protocol as p

log = logging.getLogger(__name__)

DEFAULT_PORT = 80


def _safe_call(callback: Callable, value: object) -> None:
    """A failing listener must not take the connection down with it."""
    try:
        callback(value)
    except Exception:
        log.exception("state listener failed")


class EsphomeBackend:
    name = "esphome"

    def __init__(
        self,
        device: DeviceConfig,
        *,
        timeout: float = 5.0,
        quiet_timeout: float = 60.0,
        settle: float = 4.0,
        reconnect_max: float = 30.0,
    ) -> None:
        self.device = device
        self.port = device.port or DEFAULT_PORT
        self.timeout = timeout
        self.quiet_timeout = quiet_timeout  # the board sends a ping event every few seconds
        self.settle = settle
        self.reconnect_max = reconnect_max

        self.capabilities = Capabilities(
            modes=list(MODES),
            fan_speeds=list(FAN_SPEEDS),
            swing=list(SWING_POSITIONS),
            temp_min=16,
            temp_max=30,
        )
        self.state = State()
        self.connected = False
        self.last_error: str | None = None
        self.quiet = False  # keep errors in last_error only (used while probing the LAN)

        entity = device.options.get("entity")
        self._entity: str | None = str(entity) if entity else None
        self._raw_modes: list[str] = []
        self._seen_state = False
        self._task: asyncio.Task | None = None
        self._closing = False
        self._state_callbacks: list[Callable[[State], None]] = []
        self._connection_callbacks: list[Callable[[bool], None]] = []

    # -- public API ---------------------------------------------------------------

    def on_state(self, callback: Callable[[State], None]) -> None:
        self._state_callbacks.append(callback)

    def on_connection(self, callback: Callable[[bool], None]) -> None:
        self._connection_callbacks.append(callback)

    async def start(self) -> None:
        if self._task is None:
            self._task = asyncio.create_task(self._run(), name="esphome")

    async def close(self) -> None:
        self._closing = True
        if self._task:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
            self._task = None

    async def refresh(self) -> State:
        status, body = await self._call("GET", self._entity_path())
        if status != 200:
            raise ConnectionError(f"the board answered HTTP {status} for the air conditioner")
        try:
            entity = json.loads(body)
        except ValueError:
            raise ConnectionError("the board sent an unreadable state") from None
        if not isinstance(entity, dict) or not p.is_climate(entity):
            raise ConnectionError("the board did not describe an air conditioner")
        self._handle_entity(entity)
        return self.state

    async def apply(self, cmd: Command) -> State:
        if not self._seen_state:
            await self.refresh()
        params = p.encode_command(self.state, cmd, self._raw_modes)
        if not params:
            return self.state
        status, _ = await self._call("POST", f"{self._entity_path()}/set?{urlencode(params)}")
        if status != 200:
            raise ConnectionError(f"the board refused the change (HTTP {status})")
        # The board answers at once and tells the unit afterwards: wait until the change shows.
        loop = asyncio.get_running_loop()
        deadline = loop.time() + self.settle
        while True:
            with contextlib.suppress(ConnectionError, TimeoutError):  # the change was accepted
                await self.refresh()
            if p.reached(self.state, params):
                return self.state
            if loop.time() >= deadline:
                raise TimeoutError("the air conditioner did not take the change")
            await asyncio.sleep(0.25)

    # -- connection handling --------------------------------------------------------

    async def _run(self) -> None:
        backoff = 1.0
        while not self._closing:
            try:
                await self._follow_events()
            except (OSError, ConnectionError, TimeoutError) as exc:
                self._set_error(self._describe(exc))
            except Exception as exc:  # keep reconnecting whatever happened
                log.exception("unexpected error on the event stream")
                self._set_error(f"connection error ({exc})")
            if self.connected:
                backoff = 1.0
            self._set_connected(False)
            if self._closing:
                break
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, self.reconnect_max)

    async def _follow_events(self) -> None:
        buffer = ""
        async for chunk in http.stream(self.device.host, self.port, "/events", self.timeout,
                                       self.quiet_timeout):
            buffer += chunk.decode("utf-8", "replace")
            events, buffer = p.parse_events(buffer)
            for event, data in events:
                if event != "state":
                    continue
                try:
                    entity = json.loads(data)
                except ValueError:
                    continue
                if isinstance(entity, dict) and p.is_climate(entity):
                    self._handle_entity(entity)
                    if not self.connected:
                        # A climate entity on the stream proves we reached the right board.
                        self.last_error = None
                        self._set_connected(True)
            if len(buffer) > http.MAX_BODY:
                buffer = ""  # an event that never ends: drop it

    def _handle_entity(self, entity: dict) -> None:
        name = p.entity_name(entity)
        if self._entity is None:
            self._entity = name
        elif name != self._entity:
            return  # another climate on the same board
        if "modes" in entity:
            self._raw_modes = [m for m in entity["modes"] if isinstance(m, str)]
            self.capabilities = p.decode_capabilities(entity, self.capabilities)
        self._seen_state = True
        new_state = p.decode_state(entity, self.state)
        if new_state != self.state:
            self.state = new_state
            for callback in self._state_callbacks:
                _safe_call(callback, new_state)

    # -- requests -------------------------------------------------------------------

    def _entity_path(self) -> str:
        if self._entity is None:
            raise ConnectionError(self.last_error or "not connected to the ESPHome board yet")
        return "/climate/" + quote(self._entity, safe="")

    async def _call(self, method: str, path: str) -> tuple[int, bytes]:
        try:
            return await http.request(self.device.host, self.port, method, path, self.timeout)
        except TimeoutError:
            raise TimeoutError("the ESPHome board did not reply") from None
        except http.HttpError as exc:
            raise ConnectionError(f"unexpected answer from the ESPHome board ({exc})") from None
        except OSError as exc:
            raise ConnectionError(self._describe(exc)) from None

    def _describe(self, exc: BaseException) -> str:
        where = f"{self.device.host}:{self.port}"
        if isinstance(exc, TimeoutError):
            return f"no news from the ESPHome board at {where}"
        return f"cannot reach the ESPHome board at {where} ({exc})"

    def _set_connected(self, connected: bool) -> None:
        if connected == self.connected:
            return
        self.connected = connected
        for callback in self._connection_callbacks:
            _safe_call(callback, connected)

    def _set_error(self, message: str) -> None:
        if message != self.last_error and not self.quiet:
            log.warning("%s", message)
        self.last_error = message
