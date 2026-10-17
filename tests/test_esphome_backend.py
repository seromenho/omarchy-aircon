import asyncio
import socket

import pytest
from fake_esphome import FakeEsphome

from aircon.backends import create_backend
from aircon.backends.esphome.backend import EsphomeBackend
from aircon.config import DeviceConfig
from aircon.model import Command


def device(port: int) -> DeviceConfig:
    return DeviceConfig(name="Test", backend="esphome", host="127.0.0.1", port=port)


async def make_backend(port: int) -> EsphomeBackend:
    backend = EsphomeBackend(device(port), timeout=0.5, quiet_timeout=0.5, settle=1.0,
                             reconnect_max=0.2)
    await backend.start()
    return backend


async def until(predicate, timeout: float = 3.0) -> None:
    async with asyncio.timeout(timeout):
        while not predicate():
            await asyncio.sleep(0.01)


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


@pytest.fixture
async def board():
    fake = await FakeEsphome().start()
    yield fake
    await fake.stop()


async def test_reads_state_and_capabilities_from_the_stream(board):
    backend = await make_backend(board.port)
    await until(lambda: backend.connected)
    state = backend.state
    assert (state.power, state.mode, state.target_temp, state.room_temp) == (True, "cool",
                                                                             24, 27)
    assert backend.capabilities.fan_speeds == ["low", "mid", "high", "auto"]
    await backend.close()


async def test_follows_changes_made_elsewhere(board):
    backend = await make_backend(board.port)
    await until(lambda: backend.connected)
    seen = []
    backend.on_state(seen.append)
    board.update(mode="HEAT", target_temperature="26")
    await until(lambda: seen and seen[-1].mode == "heat")
    assert seen[-1].target_temp == 26
    await backend.close()


async def test_apply_sends_only_the_change_and_waits_for_it(board):
    board.apply_delay = 0.2
    backend = await make_backend(board.port)
    await until(lambda: backend.connected)
    state = await backend.apply(Command(target_temp=21))
    assert board.set_calls == [{"target_temperature": "21"}]
    assert state.target_temp == 21
    await backend.close()


async def test_power_off_then_on_restores_the_mode(board):
    backend = await make_backend(board.port)
    await until(lambda: backend.connected)
    state = await backend.apply(Command(power=False))
    assert state.power is False and state.mode == "cool"
    state = await backend.apply(Command(power=True))
    assert board.set_calls[-1] == {"mode": "COOL"}
    assert state.power is True
    await backend.close()


async def test_refresh_reads_the_entity(board):
    backend = await make_backend(board.port)
    await until(lambda: backend.connected)
    board.entity["current_temperature"] = "25.4"
    state = await backend.refresh()
    assert state.room_temp == 25
    await backend.close()


async def test_refused_change_raises(board):
    backend = await make_backend(board.port)
    await until(lambda: backend.connected)
    board.refuse_sets = True
    with pytest.raises(ConnectionError, match="refused"):
        await backend.apply(Command(fan="high"))
    await backend.close()


async def test_a_change_the_unit_never_shows_raises(board):
    board.apply_delay = 30  # the board accepts it, the unit never reports it
    backend = await make_backend(board.port)
    await until(lambda: backend.connected)
    with pytest.raises(TimeoutError, match="did not take"):
        await backend.apply(Command(target_temp=19))
    await backend.close()


async def test_a_malformed_chunk_is_a_connection_error():
    async def serve(reader, writer):
        await reader.readuntil(b"\r\n\r\n")
        writer.write(b"HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\n\r\n-1\r\nxx\r\n")
        await writer.drain()
        writer.close()

    server = await asyncio.start_server(serve, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    backend = EsphomeBackend(device(port), timeout=0.5)
    backend._entity = "Air conditioner"
    with pytest.raises(ConnectionError, match="unexpected answer"):
        await backend.refresh()
    server.close()
    await server.wait_closed()


async def test_reconnects_after_the_stream_drops(board):
    backend = await make_backend(board.port)
    await until(lambda: backend.connected)
    events: list[bool] = []
    backend.on_connection(events.append)
    board.drop_streams()
    await until(lambda: events[-2:] == [False, True])
    assert board.streams == 2
    await backend.close()


async def test_a_silent_board_is_reconnected(board):
    backend = await make_backend(board.port)
    await until(lambda: backend.connected)
    events: list[bool] = []
    backend.on_connection(events.append)
    board.silent = True
    await until(lambda: False in events)
    board.silent = False
    await until(lambda: bool(events) and events[-1] is True)
    await backend.close()


async def test_a_board_without_climate_never_counts_as_connected():
    fake = await FakeEsphome(greet=False).start()
    backend = await make_backend(fake.port)
    await asyncio.sleep(0.3)
    assert backend.connected is False
    with pytest.raises(ConnectionError):
        await backend.refresh()
    await backend.close()
    await fake.stop()


async def test_unreachable_board_does_not_raise():
    backend = await make_backend(free_port())
    await asyncio.sleep(0.3)
    assert backend.connected is False
    assert "cannot reach" in (backend.last_error or "")
    await backend.close()


def test_registry_and_default_port():
    backend = create_backend(DeviceConfig(name="x", backend="esphome", host="h"))
    assert isinstance(backend, EsphomeBackend)
    assert backend.port == 80
