"""Non-blocking Midea backend with serialized requests and background reconnects."""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import Callable

from aircon.config import DeviceConfig
from aircon.model import Capabilities, Command, State

from . import protocol as p

log = logging.getLogger(__name__)


class MideaLANBackend:
    name = "midea_lan"

    def __init__(self, device: DeviceConfig, *, poll_interval: float = 10,
                 timeout: float = 30, reconnect_max: float = 30) -> None:
        self.device = device
        self.settings = p.settings(device)
        self.poll_interval = poll_interval
        self.timeout = timeout
        self.reconnect_max = reconnect_max
        self.capabilities = Capabilities([], [], ["off"], 16, 30)
        self.state = State()
        self.connected = False
        self.last_error: str | None = None
        self._client = None
        self._lock = asyncio.Lock()
        self._task: asyncio.Task | None = None
        self._closing = False
        self._state_callbacks: list[Callable[[State], None]] = []
        self._connection_callbacks: list[Callable[[bool], None]] = []
        # msmart-ng logs its negotiated session key at INFO, including under --verbose.
        logging.getLogger("msmart.lan").setLevel(logging.WARNING)

    def on_state(self, callback: Callable[[State], None]) -> None:
        self._state_callbacks.append(callback)

    def on_connection(self, callback: Callable[[bool], None]) -> None:
        self._connection_callbacks.append(callback)

    async def start(self) -> None:
        if self._task is None:
            self._closing = False
            self._task = asyncio.create_task(self._run(), name="midea-lan")

    async def close(self) -> None:
        self._closing = True
        if self._task:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
            self._task = None
        async with self._lock:
            self._disconnect()

    async def refresh(self) -> State:
        return await self._request()

    async def apply(self, cmd: Command) -> State:
        return await self._request(cmd)

    async def _request(self, cmd: Command | None = None) -> State:
        async with self._lock:
            if not self.connected or self._client is None:
                raise ConnectionError(self.last_error or "not connected to the air conditioner")
            if cmd is not None:
                cmd = cmd.validate(self.capabilities)
                if cmd.is_empty():
                    return self.state
            try:
                async with asyncio.timeout(self.timeout):
                    # Read before applying: Midea sends a full state, including settings the
                    # generic model does not expose. Preserve changes made by a remote/app.
                    await self._refresh()
                    if cmd is not None:
                        p.encode_command(self._client, cmd)
                        await self._client.apply()
                        self._check_reply()
                        await self._refresh()
                    self._publish()
            except Exception as exc:
                self._fail(exc)
                raise ConnectionError(self.last_error) from None
            return self.state

    async def _run(self) -> None:
        backoff = min(1.0, self.reconnect_max)
        while not self._closing:
            try:
                async with self._lock:
                    async with asyncio.timeout(self.timeout):
                        if self._client is None:
                            self._client = p.create_client(self.device, self.settings)
                            if self.settings.version == 3:
                                await self._client.authenticate(
                                    self.settings.token, self.settings.key)
                                self._check_closing()
                            await self._client.get_capabilities()
                            self._check_closing()
                            self.capabilities = p.capabilities(self._client, self.device.features)
                        await self._refresh()
                        self._publish()
                backoff = min(1.0, self.reconnect_max)
                await asyncio.sleep(self.poll_interval)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                if self._closing:
                    break
                # Both background and public operations release the same client under lock.
                async with self._lock:
                    self._fail(exc)
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, self.reconnect_max)

    async def _refresh(self) -> None:
        await self._client.refresh()
        self._check_closing()
        self._check_reply()

    def _check_closing(self) -> None:
        # msmart-ng converts CancelledError into a transport timeout. Do not reconnect
        # or publish a state if shutdown was requested while the SDK was awaiting I/O.
        if self._closing:
            raise asyncio.CancelledError

    def _check_reply(self) -> None:
        # msmart-ng can swallow transport errors. No reply must not become a stale success.
        if (not self._client.online or not self._client.supported
                or self._client.power_state is None or self._client.target_temperature is None):
            raise ConnectionError("the air conditioner did not reply")

    def _publish(self) -> None:
        state = p.decode_state(self._client)
        changed = state != self.state
        self.state = state
        self.last_error = None
        self._set_connected(True)
        if changed:
            self._notify(self._state_callbacks, state)

    def _fail(self, exc: Exception) -> None:
        # Do not expose third-party exception text: authentication errors can contain keys.
        self.last_error = (
            p.DEPENDENCY_HELP if isinstance(exc, RuntimeError) and str(exc) == p.DEPENDENCY_HELP
            else "Midea connection failed; check the address, device ID and V3 token/key"
        )
        self._disconnect()

    def _disconnect(self) -> None:
        if self._client is not None:
            with contextlib.suppress(Exception):
                p.close_client(self._client)
            self._client = None
        self._set_connected(False)

    def _set_connected(self, value: bool) -> None:
        if value != self.connected:
            self.connected = value
            self._notify(self._connection_callbacks, value)

    @staticmethod
    def _notify(callbacks: list[Callable], value: object) -> None:
        for callback in callbacks:
            try:
                callback(value)
            except Exception:
                log.exception("Midea state listener failed")
