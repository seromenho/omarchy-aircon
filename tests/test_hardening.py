"""Regression tests for the fixes from the security and code review."""

import asyncio
import json
import re
import time
from datetime import datetime, timedelta

import pytest
from conftest import MAC
from fake_dongle import FakeDongle
from test_daemon import device, env, fast_backend, ready, rpc, until  # noqa: F401 (fixture)
from test_scheduler import FakeClock

from aircon import menu, setup
from aircon.backends.haier_smartair2.backend import HaierSmartAir2Backend
from aircon.cli import main, parse_clock
from aircon.config import Config, DeviceConfig, dump_config, load_config
from aircon.daemon import Daemon, build_daemon
from aircon.model import Command, State
from aircon.scheduler import Scheduler
from aircon.store import Store


def unit(name: str, mac: str = MAC) -> DeviceConfig:
    return DeviceConfig(name=name, backend="haier_smartair2", host="10.0.0.2", mac=mac)


def jsonc_to_json(text: str) -> dict:
    text = "\n".join(line for line in text.splitlines() if not line.strip().startswith("//"))
    return json.loads(re.sub(r",(\s*})", r"\1", text))


# -- scheduler ------------------------------------------------------------------------------


@pytest.fixture
def sched(tmp_path):
    clock = FakeClock()
    applied: list[Command] = []
    failures = {"left": 0}

    async def apply(cmd: Command) -> None:
        if failures["left"]:
            failures["left"] -= 1
            raise TimeoutError("no reply")
        applied.append(cmd)

    return Scheduler(Store(tmp_path / "s.json"), apply, clock=clock), clock, applied, failures


async def test_off_timer_ends_the_sleep_curve(sched):
    scheduler, clock, applied, _ = sched
    scheduler.start_sleep_curve(State(power=True, mode="cool", target_temp=24))
    scheduler.set_timer("off", clock.now + timedelta(minutes=30))
    clock.advance(minutes=30)
    await scheduler.tick(power=True)
    clock.advance(minutes=60)
    await scheduler.tick(power=False)
    assert applied == [Command(power=False)]  # never switched back on
    assert scheduler.sleep_curve() is None


async def test_unit_switched_off_elsewhere_ends_the_curve(sched):
    scheduler, clock, applied, _ = sched
    scheduler.start_sleep_curve(State(power=True, mode="cool", target_temp=24))
    clock.advance(minutes=61)
    await scheduler.tick(power=False)
    assert applied == []
    assert scheduler.sleep_curve() is None


async def test_timer_retries_until_the_unit_answers(sched):
    scheduler, clock, applied, failures = sched
    scheduler.set_timer("off", clock.now)
    failures["left"] = 1
    await scheduler.tick()
    assert "off" in scheduler.timers()
    await scheduler.tick()
    assert applied == [Command(power=False)]
    assert scheduler.timers() == {}


async def test_timer_gives_up_after_the_grace_period(sched):
    scheduler, clock, applied, failures = sched
    scheduler.set_timer("on", clock.now)
    failures["left"] = 100
    clock.advance(minutes=16)
    await scheduler.tick()
    assert scheduler.timers() == {}
    assert applied == []


async def test_curve_step_retries_on_failure(sched):
    scheduler, clock, applied, failures = sched
    scheduler.start_sleep_curve(State(power=True, mode="cool", target_temp=24))
    clock.advance(minutes=61)
    failures["left"] = 1
    await scheduler.tick()
    await scheduler.tick()
    assert applied == [Command(target_temp=25)]


async def test_curve_end_clears_the_sleep_bit(sched):
    scheduler, clock, applied, _ = sched
    scheduler.start_sleep_curve(State(power=True, mode="cool", target_temp=24), hours=2)
    clock.advance(hours=2)
    await scheduler.tick()
    assert applied[-1] == Command(sleep=False)
    assert scheduler.sleep_curve() is None


def test_malformed_curve_is_ignored(tmp_path):
    path = tmp_path / "s.json"
    path.write_text(json.dumps({"devices": {"x": {"sleep_curve": {"started": "nope"}}}}))

    async def apply(cmd):
        pass

    assert Scheduler(Store(path, "x"), apply).sleep_curve() is None


# -- config ---------------------------------------------------------------------------------

GOOD = 'name = "A"\nbackend = "haier_smartair2"\nhost = "10.0.0.2"\nmac = "04FA83000002"\n'


@pytest.mark.parametrize(("key", "line", "message"), [
    ("backend", 'backend = "nope"', "unknown backend"),
    ("swing_style", 'swing_style = "bit"', "swing_style"),
    ("mac", 'mac = "zz"', "invalid mac"),
    ("name", "name = 3", "name"),
])
def test_bad_device_values_are_rejected(tmp_path, key, line, message):
    body = [entry for entry in GOOD.splitlines() if not entry.startswith(f"{key} ")]
    path = tmp_path / "c.toml"
    path.write_text("[[device]]\n" + "\n".join(body + [line]) + "\n")
    with pytest.raises(ValueError, match=message):
        load_config(path)


def test_filter_must_be_a_table(tmp_path):
    path = tmp_path / "c.toml"
    path.write_text("filter = 3\n[[device]]\n" + GOOD)
    with pytest.raises(ValueError, match="filter"):
        load_config(path)


def test_mac_is_normalised(tmp_path):
    path = tmp_path / "c.toml"
    path.write_text("[[device]]\n" + GOOD.replace("04FA83000002", "04:fa:83:00:00:02"))
    assert load_config(path).device.mac == "04FA83000002"


def test_odd_characters_survive_a_round_trip(tmp_path):
    name = "Living \x7f «room»"
    path = tmp_path / "c.toml"
    path.write_text(dump_config(Config(devices=[unit(name)])))
    assert load_config(path).device.name == name


# -- menu -----------------------------------------------------------------------------------


def test_menu_adds_the_missing_comma(tmp_path):
    path = tmp_path / "m.jsonc"
    path.write_text('{\n  "personal": {"label": "P"}\n  // a comment\n}\n')
    menu.sync(path, Config(devices=[unit("A")]))
    parsed = jsonc_to_json(path.read_text())
    assert "personal" in parsed and "aircon.toggle" in parsed


def test_device_name_cannot_fake_the_end_marker(tmp_path):
    path = tmp_path / "m.jsonc"
    config = Config(devices=[unit("x // <<< aircon"), unit("B", "04FA83000003")])
    menu.sync(path, config)
    menu.sync(path, config)
    text = path.read_text()
    assert text.count(menu.BEGIN) == 1
    assert text.count('"aircon.b.toggle"') == 1


def test_symlinked_menu_stays_a_symlink(tmp_path):
    real = tmp_path / "dotfiles" / "menu.jsonc"
    real.parent.mkdir()
    real.write_text("{\n}\n")
    link = tmp_path / "menu.jsonc"
    link.symlink_to(real)
    menu.sync(link, None)
    assert link.is_symlink()
    assert menu.BEGIN in real.read_text()


# -- daemon ---------------------------------------------------------------------------------


async def test_invalid_config_runs_unconfigured(tmp_path):
    bad = Config(devices=[DeviceConfig(name="A", backend="haier_smartair2", host="h")])
    daemon = build_daemon(bad, tmp_path / "a.sock", tmp_path / "s.json")
    await daemon.start()
    try:
        reply = await rpc(tmp_path / "a.sock", {"op": "get"})
        assert reply["state"]["configured"] is False
        assert "MAC" in reply["state"]["last_error"]
    finally:
        await daemon.stop()


async def test_failed_request_resends_the_state(env):  # noqa: F811
    _, _, sock = env
    reader, writer = await asyncio.open_unix_connection(str(sock))
    writer.write(b'{"op": "watch"}\n')
    await writer.drain()
    await reader.readline()
    reply = await rpc(sock, {"op": "set", "target_temp": "warm"})
    assert reply["ok"] is False
    async with asyncio.timeout(2):
        resent = json.loads(await reader.readline())
    assert resent["devices"][0]["target_temp"] == 29
    writer.close()


@pytest.mark.parametrize("request_line", [
    b'{"op": "set", "target_temp": 1e999}',
    b'{"op": "timer", "action": "off", "in_minutes": 1e30}',
    b"[" * 5000 + b"]" * 5000,
])
async def test_bad_requests_get_an_error_reply(env, request_line):  # noqa: F811
    _, _, sock = env
    reply = await rpc(sock, request_line)
    assert reply["ok"] is False
    assert (await rpc(sock, {"op": "get"}))["ok"] is True


async def test_overlong_request(env):  # noqa: F811
    _, _, sock = env
    reader, writer = await asyncio.open_unix_connection(str(sock))
    writer.write(b"x" * 70000 + b"\n")
    await writer.drain()
    assert json.loads(await reader.readline()) == {"ok": False, "error": "request too long"}
    writer.close()


async def test_second_daemon_does_not_steal_the_socket(env, tmp_path):  # noqa: F811
    _, _, sock = env
    other = Daemon(Config(devices=[]), sock, tmp_path / "other.json")
    with pytest.raises(RuntimeError, match="already"):
        await other.start()
    assert (await rpc(sock, {"op": "get"}))["ok"] is True


async def test_scheduler_skips_features_the_unit_lacks(tmp_path):
    dongle = await FakeDongle().start()
    plain = DeviceConfig(name="Living room", backend="haier_smartair2", host="127.0.0.1",
                         mac=MAC, port=dongle.port)
    daemon = Daemon(Config(devices=[plain]), tmp_path / "a.sock", tmp_path / "s.json",
                    backends={plain.id: fast_backend(plain)}, tick_interval=60)
    await daemon.start()
    try:
        await until(lambda: ready(daemon))
        apply = daemon._scheduler_apply("living-room")
        await apply(Command(sleep=False))
        await apply(Command(target_temp=22, sleep=False))
        assert dongle.state().target_temp == 22
    finally:
        await daemon.stop()
        await dongle.stop()


# -- backend --------------------------------------------------------------------------------


async def test_connected_only_after_a_valid_status_frame():
    dongle = await FakeDongle(greet=False).start()
    dongle.ignore_next(1000)
    backend = HaierSmartAir2Backend(device("x", dongle.port), poll_interval=0.05, timeout=0.1,
                                    gap=0)
    await backend.start()
    try:
        await asyncio.sleep(0.3)
        assert backend.connected is False
        dongle.ignore_next(0)
        await until(lambda: backend.connected)
    finally:
        await backend.close()
        await dongle.stop()


def test_haier_backend_requires_a_mac():
    with pytest.raises(ValueError, match="MAC"):
        HaierSmartAir2Backend(DeviceConfig(name="A", backend="haier_smartair2", host="h"))


# -- setup ----------------------------------------------------------------------------------


def test_config_is_private_and_backups_are_pruned(tmp_path, monkeypatch):
    path = tmp_path / "c.toml"
    for i in range(5):
        monkeypatch.setattr(setup.time, "time", lambda i=i: 1000 + i)
        setup.add_device(path, name=f"Unit {i}", host="h", mac=f"04FA8300000{i}")
    assert path.stat().st_mode & 0o777 == 0o600
    backups = sorted(tmp_path.glob("c.toml.bak.*"))
    assert len(backups) == 3
    assert all(b.stat().st_mode & 0o777 == 0o600 for b in backups)


def test_symlinked_config_stays_a_symlink(tmp_path):
    real = tmp_path / "dotfiles" / "c.toml"
    real.parent.mkdir()
    real.write_text("")
    link = tmp_path / "c.toml"
    link.symlink_to(real)
    setup.add_device(link, name="A", host="h", mac=MAC)
    assert link.is_symlink()
    assert MAC in real.read_text()


def test_apply_without_a_name_keeps_the_current_one(tmp_path):
    path = tmp_path / "c.toml"
    setup.add_device(path, name="Bedroom", host="h", mac=MAC)
    device_, created = setup.add_device(path, name=None, host="h2", mac=MAC)
    assert (created, device_.name, device_.host) == (False, "Bedroom", "h2")
    new, _ = setup.add_device(path, name=None, host="h3", mac="04FA83000009")
    assert new.name == "Air conditioner"
    with pytest.raises(ValueError, match="MAC"):
        setup.add_device(path, name="x", host="h", mac="nope")


async def test_validation_restores_a_unit_that_was_off():
    dongle = await FakeDongle().start()
    dongle.set_power(False)
    before = dongle.state()
    try:
        result = await setup.validate_features("127.0.0.1", MAC, dongle.port, settle=0)
        assert result.restored is True
        assert dongle.state() == before
    finally:
        await dongle.stop()


# -- cli ------------------------------------------------------------------------------------


def test_rename_accepts_a_name_starting_with_a_dash(tmp_path, monkeypatch):
    path = tmp_path / "c.toml"
    setup.add_device(path, name="Bedroom", host="h", mac=MAC)
    monkeypatch.setenv("AIRCON_CONFIG", str(path))
    assert main(["rename", "--no-restart", "--", "bedroom", "-Kids"]) == 0
    assert load_config(path).get("bedroom").name == "-Kids"


def test_parse_clock_across_a_daylight_saving_change(monkeypatch):
    monkeypatch.setenv("TZ", "Europe/Lisbon")
    time.tzset()
    try:
        now = datetime(2026, 10, 24, 23, 0).astimezone()  # summer time, +01:00
        at = parse_clock("07:30", now)
        assert (at.day, at.hour, at.minute) == (25, 7, 30)
        assert at.utcoffset() == timedelta(0)  # winter time after the change
    finally:
        monkeypatch.delenv("TZ")
        time.tzset()
