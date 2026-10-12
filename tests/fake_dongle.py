"""An in-process stand-in for the Haier SmartAir2 Wi-Fi module."""

from __future__ import annotations

import asyncio

from conftest import INFO_FRAME, MAC, REAL_STATUS_FRAME

from aircon.backends.haier_smartair2 import protocol as p
from aircon.model import State

COPIED_OFFSETS = (p.OFF_MODE, p.OFF_FAN, p.OFF_SWING, p.OFF_POWER, p.OFF_FLAGS, p.OFF_SETPOINT)


class FakeDongle:
    def __init__(self, mac: str = MAC, frame: bytes = REAL_STATUS_FRAME,
                 greet: bool = True) -> None:
        self.mac = mac
        self.greet = greet
        self.frame = bytearray(frame)
        self.requests: list[bytes] = []
        self.connections = 0
        self.port = 0
        self._ignore = 0
        self._writers: set[asyncio.StreamWriter] = set()
        self._server: asyncio.Server | None = None

    async def start(self) -> FakeDongle:
        self._server = await asyncio.start_server(self._serve, "127.0.0.1", 0)
        self.port = self._server.sockets[0].getsockname()[1]
        return self

    async def stop(self) -> None:
        self.drop_connections()
        if self._server:
            self._server.close()
            await self._server.wait_closed()

    def ignore_next(self, count: int) -> None:
        self._ignore = count

    def drop_connections(self) -> None:
        for writer in list(self._writers):
            writer.close()

    def state(self, swing_style: str = "bits") -> State:
        return p.decode_state(self._status(), swing_style)

    def set_power(self, on: bool) -> None:
        if on:
            self.frame[p.OFF_POWER] |= p.POWER_BIT
        else:
            self.frame[p.OFF_POWER] &= ~p.POWER_BIT & 0xFF

    def _status(self) -> bytes:
        self.frame[-1] = p.checksum(bytes(self.frame[:-1]))
        return bytes(self.frame)

    def _response(self, seq: int, frame: bytes) -> bytes:
        header = bytearray(p.HEADER_LEN)
        header[0:4] = p.RESP_MAGIC
        header[40:52] = self.mac.encode()
        header[75] = seq
        header[79] = len(frame)
        return bytes(header) + frame

    async def _serve(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        self.connections += 1
        self._writers.add(writer)
        # The real module pushes an info frame and a status frame right after connecting.
        if self.greet:
            writer.write(self._response(0, INFO_FRAME) + self._response(0, self._status()))
        buf = b""
        try:
            while True:
                data = await reader.read(4096)
                if not data:
                    break
                buf += data
                while len(buf) >= p.HEADER_LEN and len(buf) >= p.HEADER_LEN + buf[79]:
                    size = p.HEADER_LEN + buf[79]
                    request, buf = buf[:size], buf[size:]
                    self._handle(writer, request[75], request[p.HEADER_LEN :])
                await writer.drain()
        except ConnectionError:
            pass
        finally:
            self._writers.discard(writer)
            writer.close()

    def _handle(self, writer: asyncio.StreamWriter, seq: int, payload: bytes) -> None:
        self.requests.append(payload)
        if self._ignore:
            self._ignore -= 1
            return
        command = payload[10:12]
        if payload == p.POWER_ON:
            self.set_power(True)
        elif payload == p.POWER_OFF:
            self.set_power(False)
        elif command == b"\x4d\x5f":
            for offset in COPIED_OFFSETS:
                self.frame[offset] = payload[offset]
        elif payload == p.QUERY:
            writer.write(self._response(seq, INFO_FRAME))
        writer.write(self._response(seq, self._status()))
