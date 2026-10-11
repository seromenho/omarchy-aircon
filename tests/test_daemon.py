import asyncio
import json
import socket

import pytest
from conftest import MAC
from fake_dongle import FakeDongle

from aircon.backends.haier_smartair2.backend import HaierSmartAir2Backend
from aircon.config import Config, DeviceConfig
from aircon.daemon import Daemon

FEATURES = ["health", "sleep", "swing_horizontal"]


async def until(predicate, timeout: float = 2.0) -> None:
    async with asyncio.timeout(timeout):
        while not predicate():
            await asyncio.sleep(0.01)


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def device(name: str, port: int, device_id: str | None = None) -> DeviceConfig:
    return DeviceConfig(name=name, backend="haier_smartair2", host="127.0.0.1", mac=MAC,
                        port=port, features=FEATURES, id=device_id or "")


def fast_backend(dev: DeviceConfig) -> HaierSmartAir2Backend:
    return HaierSmartAir2Backend(dev, poll_interval=60, timeout=0.3, gap=0, reconnect_max=0.2)


async def start_daemon(tmp_path, *ports: int, default: str | None = None) -> Daemon:
    names = ["Living room", "Bedroom", "Office"]
    devices = [device(names[i], port) for i, port in enumerate(ports)]
    daemon = Daemon(
        Config(devices=devices, default=default), tmp_path / "a.sock", tmp_path / "state.json",
        backends={d.id: fast_backend(d) for d in devices}, tick_interval=0.05,
    )
    await daemon.start()
    return daemon


def ready(daemon: Daemon) -> bool:
    return all(rt.backend.connected and rt.backend.last_frame is not None
               for rt in daemon.devices.values())


def dev(reply: dict, device_id: str = "living-room") -> dict:
    return next(d for d in reply["state"]["devices"] if d["id"] == device_id)


async def rpc(path, request) -> dict:
    reader, writer = await asyncio.open_unix_connection(str(path))
    payload = request if isinstance(request, bytes) else json.dumps(request).encode()
    writer.write(payload + b"\n")
    await writer.drain()
    line = await reader.readline()
    writer.close()
    return json.loads(line)


@pytest.fixture
async def env(tmp_path):
    dongle = await FakeDongle().start()
    daemon = await start_daemon(tmp_path, dongle.port)
    await until(lambda: ready(daemon))
    yield daemon, dongle, tmp_path / "a.sock"
    await daemon.stop()
    await dongle.stop()


@pytest.fixture
async def pair(tmp_path):
    living, bedroom = await FakeDongle().start(), await FakeDongle().start()
    daemon = await start_daemon(tmp_path, living.port, bedroom.port)
    await until(lambda: ready(daemon))
    yield daemon, living, bedroom, tmp_path / "a.sock"
    await daemon.stop()
    await living.stop()
    await bedroom.stop()


async def test_get_returns_snapshot(env):
    _, _, sock = env
    reply = await rpc(sock, {"op": "get"})
    assert reply["ok"] is True
    assert reply["state"]["configured"] is True
    assert reply["state"]["default"] == "living-room"
    state = dev(reply)
    assert state["connected"] is True
    assert state["name"] == "Living room"
    assert state["backend"] == "haier_smartair2"
    assert state["room_temp"] == 26
    assert state["capabilities"]["temp_max"] == 30
    assert state["filter"]["threshold"] == 300
    assert state["timers"] == {}
    assert state["sleep_curve"] is None


async def test_set_temperature(env):
    _, dongle, sock = env
    reply = await rpc(sock, {"op": "set", "target_temp": 22})
    assert reply["ok"] is True
    assert dev(reply)["target_temp"] == 22
    assert dongle.state().target_temp == 22


async def test_set_clamps_temperature(env):
    _, _, sock = env
    reply = await rpc(sock, {"op": "set", "target_temp": 40})
    assert dev(reply)["target_temp"] == 30


async def test_set_rejects_invalid_values(env):
    _, _, sock = env
    reply = await rpc(sock, {"op": "set", "mode": "turbo"})
    assert reply["ok"] is False
    assert "mode" in reply["error"]
    reply = await rpc(sock, {"op": "set"})
    assert reply["ok"] is False


async def test_malformed_json_keeps_connection_usable(env):
    _, _, sock = env
    reader, writer = await asyncio.open_unix_connection(str(sock))
    writer.write(b"{nope\n")
    writer.write(b'{"op": "get"}\n')
    await writer.drain()
    first = json.loads(await reader.readline())
    second = json.loads(await reader.readline())
    writer.close()
    assert first["ok"] is False
    assert second["ok"] is True


async def test_unknown_op(env):
    _, _, sock = env
    reply = await rpc(sock, {"op": "dance"})
    assert reply["ok"] is False
    assert "dance" in reply["error"]


async def test_watch_receives_pushed_changes(env):
    _, _, sock = env
    reader, writer = await asyncio.open_unix_connection(str(sock))
    writer.write(b'{"op": "watch"}\n')
    await writer.drain()
    first = json.loads(await reader.readline())
    assert first["devices"][0]["target_temp"] == 29
    await rpc(sock, {"op": "set", "target_temp": 23})
    async with asyncio.timeout(2):
        while True:
            update = json.loads(await reader.readline())
            if update["devices"][0]["target_temp"] == 23:
                break
    writer.close()


async def test_timer_and_cancel(env):
    _, _, sock = env
    reply = await rpc(sock, {"op": "timer", "action": "off", "in_minutes": 90})
    assert "off" in dev(reply)["timers"]
    reply = await rpc(sock, {"op": "timer_cancel", "action": "off"})
    assert dev(reply)["timers"] == {}
    reply = await rpc(sock, {"op": "timer", "action": "off"})
    assert reply["ok"] is False


async def test_timer_fires(env):
    _, dongle, sock = env
    await rpc(sock, {"op": "timer", "action": "off", "at": "2000-01-01T00:00:00+00:00"})
    await until(lambda: dongle.state().power is False)


async def test_sleep_curve_sets_sleep_bit(env):
    _, dongle, sock = env
    reply = await rpc(sock, {"op": "sleep_curve", "hours": 8})
    assert dev(reply)["sleep_curve"]["hours"] == 8
    assert dongle.state().sleep is True
    reply = await rpc(sock, {"op": "sleep_curve_cancel"})
    assert dev(reply)["sleep_curve"] is None
    assert dongle.state().sleep is False


async def test_manual_temperature_cancels_sleep_curve(env):
    _, _, sock = env
    await rpc(sock, {"op": "sleep_curve"})
    reply = await rpc(sock, {"op": "set", "target_temp": 20})
    assert dev(reply)["sleep_curve"] is None


async def test_filter_reset(env):
    _, _, sock = env
    reply = await rpc(sock, {"op": "filter_reset"})
    assert dev(reply)["filter"]["hours"] == 0.0


async def test_unreachable_device_still_serves(tmp_path):
    daemon = await start_daemon(tmp_path, free_port())
    try:
        reply = await rpc(tmp_path / "a.sock", {"op": "get"})
        assert reply["ok"] is True
        assert dev(reply)["connected"] is False
        reply = await rpc(tmp_path / "a.sock", {"op": "set", "target_temp": 22})
        assert reply["ok"] is False
    finally:
        await daemon.stop()


async def test_empty_config_is_not_configured(tmp_path):
    daemon = Daemon(Config(devices=[]), tmp_path / "a.sock", tmp_path / "state.json")
    await daemon.start()
    try:
        reply = await rpc(tmp_path / "a.sock", {"op": "get"})
        assert reply["state"]["configured"] is False
        assert reply["state"]["devices"] == []
    finally:
        await daemon.stop()


# -- several devices -------------------------------------------------------------------


async def test_routes_by_device_and_defaults_to_first(pair):
    _, living, bedroom, sock = pair
    await rpc(sock, {"op": "set", "device": "bedroom", "target_temp": 21})
    await rpc(sock, {"op": "set", "target_temp": 25})
    assert bedroom.state().target_temp == 21
    assert living.state().target_temp == 25


async def test_unknown_device(pair):
    *_, sock = pair
    reply = await rpc(sock, {"op": "set", "device": "garage", "power": True})
    assert reply["ok"] is False
    assert "garage" in reply["error"] and "bedroom" in reply["error"]


async def test_all_off_and_its_limits(pair):
    _, living, bedroom, sock = pair
    reply = await rpc(sock, {"op": "set", "device": "all", "power": False})
    assert reply["ok"] is True
    assert living.state().power is False and bedroom.state().power is False
    reply = await rpc(sock, {"op": "set", "device": "all", "target_temp": 20})
    assert reply["ok"] is False


async def test_all_off_reports_unreachable_but_switches_the_rest(tmp_path):
    living = await FakeDongle().start()
    daemon = await start_daemon(tmp_path, living.port, free_port())
    try:
        await until(lambda: daemon.devices["living-room"].backend.last_frame is not None)
        reply = await rpc(tmp_path / "a.sock", {"op": "set", "device": "all", "power": False})
        assert reply["ok"] is False
        assert "Bedroom" in reply["error"]
        assert living.state().power is False
    finally:
        await daemon.stop()
        await living.stop()


async def test_timers_are_per_device(pair):
    *_, sock = pair
    reply = await rpc(sock, {"op": "timer", "device": "bedroom", "action": "off",
                             "in_minutes": 30})
    assert "off" in dev(reply, "bedroom")["timers"]
    assert dev(reply, "living-room")["timers"] == {}


async def test_configured_default_device(tmp_path):
    living, bedroom = await FakeDongle().start(), await FakeDongle().start()
    daemon = await start_daemon(tmp_path, living.port, bedroom.port, default="bedroom")
    try:
        await until(lambda: ready(daemon))
        reply = await rpc(tmp_path / "a.sock", {"op": "set", "target_temp": 18})
        assert reply["state"]["default"] == "bedroom"
        assert bedroom.state().target_temp == 18
    finally:
        await daemon.stop()
        await living.stop()
        await bedroom.stop()
