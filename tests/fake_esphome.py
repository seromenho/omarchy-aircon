"""An in-process stand-in for an ESPHome board running the Haier climate component.

Speaks the parts of ESPHome's web_server API the backend uses: `GET /events` (Server-Sent
Events, chunked), `GET /climate/<name>` and `POST /climate/<name>/set?...`. The JSON shapes
were captured from a real board (ESPHome 2026.9, Haier smartAir2 protocol).
"""

from __future__ import annotations

import asyncio
import json
from urllib.parse import parse_qsl, unquote, urlsplit

NAME = "Air conditioner"


def climate_entity(**overrides) -> dict:
    entity = {
        "id": f"climate/{NAME}", "domain": "climate", "name": NAME, "entity_category": 0,
        "modes": ["OFF", "HEAT_COOL", "COOL", "HEAT", "FAN_ONLY", "DRY"],
        "fan_modes": ["AUTO", "LOW", "MEDIUM", "HIGH"],
        "swing_modes": ["OFF", "BOTH", "VERTICAL", "HORIZONTAL"],
        "presets": ["NONE", "BOOST", "COMFORT"],
        "max_temp": "30", "min_temp": "16", "step": 1,
        "mode": "COOL", "fan_mode": "LOW", "preset": "NONE", "swing_mode": "OFF",
        "current_temperature": "27.0", "target_temperature": "24", "state": "24",
    }
    entity.update(overrides)
    return entity


class FakeEsphome:
    def __init__(self, *, greet: bool = True, ping_every: float = 0.05,
                 apply_delay: float = 0.0) -> None:
        self.entity = climate_entity()
        self.greet = greet  # send the climate state when a stream opens
        self.ping_every = ping_every
        self.apply_delay = apply_delay  # how long the "unit" takes to take a change
        self.requests: list[tuple[str, str]] = []
        self.set_calls: list[dict[str, str]] = []
        self.refuse_sets = False
        self.streams = 0
        self.silent = False  # stop sending pings (a stuck board)
        self.port = 0
        self._writers: set[asyncio.StreamWriter] = set()
        self._streams: set[asyncio.StreamWriter] = set()
        self._server: asyncio.Server | None = None

    async def start(self) -> FakeEsphome:
        self._server = await asyncio.start_server(self._serve, "127.0.0.1", 0)
        self.port = self._server.sockets[0].getsockname()[1]
        return self

    async def stop(self) -> None:
        self.drop_streams()
        for writer in list(self._writers):
            writer.close()
        if self._server:
            self._server.close()
            await self._server.wait_closed()

    def drop_streams(self) -> None:
        for writer in list(self._streams):
            writer.close()

    def update(self, **fields) -> None:
        """Change the unit as if someone used the remote, and tell open streams."""
        self.entity.update(fields)
        for writer in list(self._streams):
            self._send_event(writer, "state", dict(self.entity))

    @staticmethod
    def _chunk(writer: asyncio.StreamWriter, text: str) -> None:
        data = text.encode()
        writer.write(f"{len(data):x}\r\n".encode() + data + b"\r\n")

    def _send_event(self, writer: asyncio.StreamWriter, event: str, payload: dict) -> None:
        self._chunk(writer, f"event: {event}\r\ndata: {json.dumps(payload)}\r\n\r\n")

    async def _serve(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        self._writers.add(writer)
        try:
            head = await reader.readuntil(b"\r\n\r\n")
            method, target, _ = head.decode().split("\r\n", 1)[0].split(" ", 2)
            self.requests.append((method, target))
            url = urlsplit(target)
            path = unquote(url.path)
            if method == "GET" and path == "/events":
                await self._events(writer)
            elif path == f"/climate/{NAME}" and method == "GET":
                # The direct read leaves out the lists of supported values, like the board.
                listed = ("modes", "fan_modes", "swing_modes", "presets")
                self._reply(writer, 200, json.dumps({k: v for k, v in self.entity.items()
                                                     if k not in listed}))
            elif path == f"/climate/{NAME}/set" and method == "POST":
                params = dict(parse_qsl(url.query))
                self.set_calls.append(params)
                if self.refuse_sets:
                    self._reply(writer, 400, "")
                else:
                    self._reply(writer, 200, "")
                    asyncio.get_running_loop().call_later(self.apply_delay, self._take, params)
            else:
                self._reply(writer, 404, "Not Found")
            await writer.drain()
        except (ConnectionError, asyncio.IncompleteReadError):
            pass
        finally:
            self._writers.discard(writer)
            writer.close()

    def _take(self, params: dict[str, str]) -> None:
        keys = ("mode", "fan_mode", "swing_mode", "target_temperature")
        self.update(**{k: params[k] for k in keys if k in params})

    @staticmethod
    def _reply(writer: asyncio.StreamWriter, status: int, body: str) -> None:
        data = body.encode()
        writer.write(f"HTTP/1.1 {status} X\r\nContent-Type: application/json\r\n"
                     f"Content-Length: {len(data)}\r\n\r\n".encode() + data)

    async def _events(self, writer: asyncio.StreamWriter) -> None:
        self.streams += 1
        self._streams.add(writer)
        writer.write(b"HTTP/1.1 200 OK\r\nContent-Type: text/event-stream\r\n"
                     b"Transfer-Encoding: chunked\r\nCache-Control: no-cache\r\n\r\n")
        self._chunk(writer, "retry: 30000\r\nid: 1\r\nevent: ping\r\n"
                            'data: {"title":"ac","uptime":1}\r\n\r\n')
        self._send_event(writer, "state", {"id": "sensor/Wi-Fi signal", "domain": "sensor",
                                           "value": -60, "state": "-60 dBm"})
        if self.greet:
            self._send_event(writer, "state", dict(self.entity))
        try:
            while not writer.is_closing():
                await writer.drain()
                await asyncio.sleep(self.ping_every)
                if not self.silent:
                    self._chunk(writer, 'event: ping\r\ndata: {"uptime":2}\r\n\r\n')
        finally:
            self._streams.discard(writer)
