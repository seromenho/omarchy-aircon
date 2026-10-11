"""asyncio client for the Haier SmartAir2 local protocol.

One TCP connection, a serialized command queue, replies matched by sequence number,
periodic status polling and reconnection with exponential backoff.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import Callable
from dataclasses import replace

from aircon.config import DeviceConfig
from aircon.model import FAN_SPEEDS, MODES, Capabilities, Command, State, apply_command

from . import protocol as p

log = logging.getLogger(__name__)

OPTIONAL_FEATURES = ("health", "sleep")
MAX_MISSED_POLLS = 3
FAN_ONLY_FALLBACK_SPEED = "mid"


class HaierSmartAir2Backend:
    name = "haier_smartair2"

    def __init__(
        self,
        device: DeviceConfig,
        *,
        poll_interval: float = 10.0,
        timeout: float = 3.0,
        gap: float = 0.15,
        reconnect_max: float = 30.0,
    ) -> None:
        if device.swing_style not in p.SWING_STYLES:
            raise ValueError(f"swing_style must be one of: {', '.join(p.SWING_STYLES)}")
        self.device = device
        self.poll_interval = poll_interval
        self.timeout = timeout
        self.gap = gap
        self.reconnect_max = reconnect_max

        swing = ["off", "vertical"]
        if "swing_horizontal" in device.features:
            swing += ["horizontal", "both"]
        features = ["room_humidity"] + [f for f in OPTIONAL_FEATURES if f in device.features]
        self.capabilities = Capabilities(
            modes=list(MODES),
            fan_speeds=list(FAN_SPEEDS),
            swing=swing,
            temp_min=16,
            temp_max=30,
            temp_step=1,
            features=features,
        )

        self.state = State()
        self.connected = False
        self.last_error: str | None = None
        self.last_frame: bytes | None = None
        self.unknown_frames: list[str] = []

        self._writer: asyncio.StreamWriter | None = None
        self._seq = 0
        self._pending: dict[int, asyncio.Future[bytes]] = {}
        self._lock = asyncio.Lock()
        self._last_send = 0.0
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
            self._task = asyncio.create_task(self._run(), name="haier-smartair2")

    async def close(self) -> None:
        self._closing = True
        if self._task:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
            self._task = None

    async def refresh(self) -> State:
        await self._request(p.QUERY)
        return self.state

    async def apply(self, cmd: Command) -> State:
        if cmd.power is False:
            await self._send_and_sync(p.POWER_OFF)
            return self.state

        if self.last_frame is None:
            await self.refresh()
        settings = {k: v for k, v in cmd.changes().items() if k != "power"}
        if not self.state.power and (cmd.power or settings):
            await self._send_and_sync(p.POWER_ON)
        if settings:
            target = replace(apply_command(self.state, cmd), power=True)
            if target.mode == "fan" and target.fan == "auto":
                # The unit silently rejects the whole command in fan-only mode with auto speed.
                target = replace(target, fan=FAN_ONLY_FALLBACK_SPEED)
            assert self.last_frame is not None
            control = p.encode_control(self.last_frame, target, self.device.swing_style)
            await self._send_and_sync(control)
        return self.state

    # -- connection handling --------------------------------------------------------

    async def _run(self) -> None:
        backoff = 1.0
        while not self._closing:
            host, port = self.device.host, self.device.port
            try:
                reader, writer = await asyncio.wait_for(
                    asyncio.open_connection(host, port), self.timeout
                )
            except (OSError, TimeoutError) as exc:
                self._set_error(f"cannot reach {host}:{port} ({exc or 'timeout'})")
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, self.reconnect_max)
                continue

            backoff = 1.0
            self._writer = writer
            self.last_error = None
            self._set_connected(True)
            poller = asyncio.create_task(self._poll_loop())
            try:
                await self._read_loop(reader)
            except (OSError, ConnectionError) as exc:
                self._set_error(f"connection lost ({exc})")
            finally:
                poller.cancel()
                self._writer = None
                writer.close()
                for future in self._pending.values():
                    if not future.done():
                        future.set_exception(ConnectionError("connection lost"))
                self._pending.clear()
                self._set_connected(False)
            if not self._closing:
                await asyncio.sleep(min(1.0, self.reconnect_max))

    async def _read_loop(self, reader: asyncio.StreamReader) -> None:
        buf = b""
        while True:
            data = await reader.read(4096)
            if not data:
                raise ConnectionError("closed by the device")
            frames, buf = p.parse_stream(buf + data)
            for seq, frame in frames:
                self._handle_frame(seq, frame)

    async def _poll_loop(self) -> None:
        missed = 0
        while True:
            try:
                await self.refresh()
                missed = 0
            except TimeoutError as exc:
                missed += 1
                self._set_error(str(exc))
                if missed >= MAX_MISSED_POLLS and self._writer:
                    # The socket looks alive but the module stopped answering: reconnect.
                    self._writer.close()
            except ConnectionError:
                pass
            await asyncio.sleep(self.poll_interval)

    def _handle_frame(self, seq: int, frame: bytes) -> None:
        frame_type = frame[p.OFF_TYPE]
        if frame_type == p.FRAME_INFO:
            return
        if frame_type == p.FRAME_STATUS and len(frame) >= p.STATUS_LEN:
            self.last_frame = frame
            new_state = replace(p.decode_state(frame, self.device.swing_style),
                                alarm=self.state.alarm)
            if new_state != self.state:
                self.state = new_state
                for callback in self._state_callbacks:
                    callback(new_state)
        else:
            self.unknown_frames = (self.unknown_frames + [frame.hex(" ")])[-5:]
            log.debug("unknown frame type 0x%02x: %s", frame_type, frame.hex(" "))
        future = self._pending.pop(seq, None)
        if future and not future.done():
            future.set_result(frame)

    # -- requests -------------------------------------------------------------------

    async def _send_and_sync(self, payload: bytes) -> None:
        reply = await self._request(payload)
        if reply[p.OFF_TYPE] != p.FRAME_STATUS:
            await self.refresh()

    async def _request(self, payload: bytes) -> bytes:
        async with self._lock:
            loop = asyncio.get_running_loop()
            for _attempt in range(2):
                writer = self._writer
                if writer is None:
                    raise ConnectionError(self.last_error or "not connected to the air conditioner")
                wait = self.gap - (loop.time() - self._last_send)
                if wait > 0:
                    await asyncio.sleep(wait)
                seq = self._next_seq()
                future: asyncio.Future[bytes] = loop.create_future()
                self._pending[seq] = future
                writer.write(p.build_request(self.device.mac, seq, payload))
                await writer.drain()
                self._last_send = loop.time()
                try:
                    return await asyncio.wait_for(future, self.timeout)
                except TimeoutError:
                    self._pending.pop(seq, None)
            raise TimeoutError("the air conditioner did not reply")

    def _next_seq(self) -> int:
        # Sequence 0 is used by the module for unsolicited frames, so skip it.
        self._seq = self._seq % 255 + 1
        return self._seq

    def _set_connected(self, connected: bool) -> None:
        if connected == self.connected:
            return
        self.connected = connected
        for callback in self._connection_callbacks:
            callback(connected)

    def _set_error(self, message: str) -> None:
        if message != self.last_error:
            log.warning("%s", message)
        self.last_error = message
