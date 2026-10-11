import asyncio
import socket

import pytest
from conftest import MAC
from fake_dongle import FakeDongle

from aircon.backends import create_backend
from aircon.backends.haier_smartair2 import protocol as p
from aircon.backends.haier_smartair2.backend import HaierSmartAir2Backend
from aircon.config import DeviceConfig
from aircon.model import Command


def device(port: int, **kwargs) -> DeviceConfig:
    return DeviceConfig(
        name="Test", backend="haier_smartair2", host="127.0.0.1", mac=MAC, port=port, **kwargs
    )


async def make_backend(port: int, **kwargs) -> HaierSmartAir2Backend:
    backend = HaierSmartAir2Backend(
        device(port, **kwargs), poll_interval=60, timeout=0.3, gap=0, reconnect_max=0.2
    )
    await backend.start()
    return backend


async def until(predicate, timeout: float = 2.0) -> None:
    async with asyncio.timeout(timeout):
        while not predicate():
            await asyncio.sleep(0.01)


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


@pytest.fixture
async def dongle():
    fake = await FakeDongle().start()
    yield fake
    await fake.stop()


async def test_reads_initial_state(dongle):
    backend = await make_backend(dongle.port)
    await until(lambda: backend.connected and backend.state.room_temp == 26)
    state = await backend.refresh()
    assert (state.power, state.mode, state.fan, state.target_temp) == (True, "cool", "low", 29)
    assert state.room_humidity == 51
    await backend.close()


async def test_apply_temperature(dongle):
    backend = await make_backend(dongle.port)
    await until(lambda: backend.connected)
    state = await backend.apply(Command(target_temp=22))
    assert dongle.state().target_temp == 22
    assert state.target_temp == 22
    await backend.close()


async def test_change_while_off_powers_on_first(dongle):
    dongle.set_power(False)
    backend = await make_backend(dongle.port)
    await until(lambda: backend.connected and backend.last_frame is not None)
    state = await backend.apply(Command(mode="heat"))
    commands = [r[10:12] for r in dongle.requests]
    assert commands.index(b"\x4d\x02") < commands.index(b"\x4d\x5f")
    assert (state.power, state.mode) == (True, "heat")
    await backend.close()


async def test_fan_mode_never_sends_auto_speed(dongle):
    backend = await make_backend(dongle.port)
    await until(lambda: backend.connected)
    await backend.apply(Command(fan="auto"))
    state = await backend.apply(Command(mode="fan"))
    assert (state.mode, state.fan) == ("fan", "mid")


async def test_power_off_uses_power_off_command(dongle):
    backend = await make_backend(dongle.port)
    await until(lambda: backend.connected)
    state = await backend.apply(Command(power=False))
    assert dongle.requests[-1] == p.POWER_OFF
    assert state.power is False
    await backend.close()


async def test_retries_once_on_missing_reply(dongle):
    backend = await make_backend(dongle.port)
    await until(lambda: backend.connected)
    before = len(dongle.requests)
    dongle.ignore_next(1)
    await backend.refresh()
    assert len(dongle.requests) == before + 2
    await backend.close()


async def test_times_out_after_retry(dongle):
    backend = await make_backend(dongle.port)
    await until(lambda: backend.connected)
    dongle.ignore_next(2)
    with pytest.raises(TimeoutError):
        await backend.refresh()
    await backend.close()


async def test_reconnects_after_connection_drop(dongle):
    backend = await make_backend(dongle.port)
    await until(lambda: backend.connected)
    events: list[bool] = []
    backend.on_connection(events.append)
    dongle.drop_connections()
    await until(lambda: events[-2:] == [False, True])
    assert dongle.connections == 2
    await backend.close()


async def test_unreachable_device_does_not_raise():
    backend = await make_backend(free_port())
    await asyncio.sleep(0.3)
    assert backend.connected is False
    assert "cannot reach" in (backend.last_error or "")
    with pytest.raises(ConnectionError):
        await backend.refresh()
    await backend.close()


def test_capabilities_follow_configured_features():
    plain = HaierSmartAir2Backend(device(1))
    assert plain.capabilities.swing == ["off", "vertical"]
    assert plain.capabilities.features == ["room_humidity"]
    rich = HaierSmartAir2Backend(device(1, features=["health", "sleep", "swing_horizontal"]))
    assert rich.capabilities.swing == ["off", "vertical", "horizontal", "both"]
    assert set(rich.capabilities.features) == {"room_humidity", "health", "sleep"}


def test_registry_creates_backend_and_rejects_unknown():
    assert isinstance(create_backend(device(1)), HaierSmartAir2Backend)
    with pytest.raises(ValueError, match="unknown backend"):
        create_backend(DeviceConfig(name="x", backend="nope", host="h"))
