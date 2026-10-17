"""A minimal asyncio HTTP/1.1 client: enough for ESPHome's web_server, stdlib only."""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import AsyncIterator

MAX_BODY = 64 * 1024
MAX_HEAD = 16 * 1024


class HttpError(ConnectionError):
    """The board answered, but not with what we asked for."""


async def _read_head(reader: asyncio.StreamReader) -> tuple[int, dict[str, str]]:
    try:
        head = await reader.readuntil(b"\r\n\r\n")
    except asyncio.LimitOverrunError:
        raise HttpError("response headers too long") from None
    except asyncio.IncompleteReadError:
        raise ConnectionError("closed by the board") from None
    if len(head) > MAX_HEAD:
        raise HttpError("response headers too long")
    lines = head.decode("latin-1").split("\r\n")
    parts = lines[0].split(" ", 2)
    if len(parts) < 2 or not parts[0].startswith("HTTP/") or not parts[1].isdigit():
        raise HttpError(f"not an HTTP response: {lines[0][:60]!r}")
    headers = {}
    for line in lines[1:]:
        name, sep, value = line.partition(":")
        if sep:
            headers[name.strip().lower()] = value.strip()
    return int(parts[1]), headers


async def _chunks(reader: asyncio.StreamReader, headers: dict[str, str]) -> AsyncIterator[bytes]:
    """The body as it arrives: chunked, sized, or until the board closes the connection."""
    if "chunked" in headers.get("transfer-encoding", "").lower():
        while True:
            try:
                size_line = await reader.readline()
            except ValueError:  # a line longer than the stream limit
                raise HttpError("bad chunk size") from None
            if not size_line:
                raise ConnectionError("closed by the board")
            digits = size_line.split(b";")[0].strip()
            try:
                size = int(digits, 16)
            except ValueError:
                raise HttpError("bad chunk size") from None
            if size < 0 or not digits.isalnum():
                raise HttpError("bad chunk size")
            if size == 0:
                return
            if size > MAX_BODY:
                raise HttpError("chunk too large")
            data = await reader.readexactly(size)
            await reader.readexactly(2)
            yield data
    elif "content-length" in headers:
        try:
            length = int(headers["content-length"])
        except ValueError:
            raise HttpError("bad content length") from None
        if not 0 <= length <= MAX_BODY:
            raise HttpError("response too large")
        if length:
            yield await reader.readexactly(length)
    else:
        while data := await reader.read(4096):
            yield data


def _request_bytes(method: str, host: str, path: str, connection: str = "close") -> bytes:
    return (f"{method} {path} HTTP/1.1\r\nHost: {host}\r\nAccept: */*\r\n"
            f"Connection: {connection}\r\nContent-Length: 0\r\n\r\n").encode()


async def request(host: str, port: int, method: str, path: str,
                  timeout: float) -> tuple[int, bytes]:
    """Send one request and return (status, body). Raises ConnectionError or TimeoutError."""

    async def exchange() -> tuple[int, bytes]:
        reader, writer = await asyncio.open_connection(host, port)
        try:
            writer.write(_request_bytes(method, host, path))
            await writer.drain()
            status, headers = await _read_head(reader)
            body = b""
            async for chunk in _chunks(reader, headers):
                body += chunk
                if len(body) > MAX_BODY:
                    raise HttpError("response too large")
            return status, body
        except asyncio.IncompleteReadError:
            raise ConnectionError("closed by the board") from None
        finally:
            writer.close()
            with contextlib.suppress(OSError):
                await writer.wait_closed()

    return await asyncio.wait_for(exchange(), timeout)


async def stream(host: str, port: int, path: str, connect_timeout: float,
                 read_timeout: float) -> AsyncIterator[bytes]:
    """GET `path` and yield the body as it arrives (for Server-Sent Events).

    Raises TimeoutError when nothing arrives for `read_timeout` seconds.
    """
    reader, writer = await asyncio.wait_for(asyncio.open_connection(host, port),
                                            connect_timeout)
    try:
        writer.write(_request_bytes("GET", host, path, connection="keep-alive"))
        await writer.drain()
        status, headers = await asyncio.wait_for(_read_head(reader), connect_timeout)
        if status != 200:
            raise HttpError(f"{path} answered HTTP {status}")
        chunks = _chunks(reader, headers).__aiter__()
        while True:
            try:
                yield await asyncio.wait_for(chunks.__anext__(), read_timeout)
            except StopAsyncIteration:
                raise ConnectionError("closed by the board") from None
            except asyncio.IncompleteReadError:
                raise ConnectionError("closed by the board") from None
    finally:
        writer.close()
        with contextlib.suppress(OSError):
            await writer.wait_closed()
