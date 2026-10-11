import asyncio
import json
from datetime import datetime

import pytest
from fake_dongle import FakeDongle
from test_daemon import ready, start_daemon, until

from aircon.cli import main, parse_clock, parse_duration


@pytest.fixture
async def env(tmp_path, monkeypatch):
    dongle = await FakeDongle().start()
    daemon = await start_daemon(tmp_path, dongle.port)
    await until(lambda: ready(daemon))
    monkeypatch.setenv("AIRCON_SOCKET", str(tmp_path / "a.sock"))
    yield dongle
    await daemon.stop()
    await dongle.stop()


async def run(*argv: str) -> int:
    return await asyncio.to_thread(main, list(argv))


async def test_status_json(env, capsys):
    assert await run("status", "--json") == 0
    state = json.loads(capsys.readouterr().out)
    assert state["devices"][0]["room_temp"] == 26


async def test_status_human(env, capsys):
    assert await run("status") == 0
    out = capsys.readouterr().out
    assert out.startswith("Living room: on · cool · 29°C")
    assert "room 26°C, 51%" in out


async def test_set_mode_and_temperature(env):
    assert await run("set", "--mode", "heat", "--temp", "22") == 0
    state = env.state()
    assert (state.mode, state.target_temp) == ("heat", 22)


async def test_temp_relative_changes(env):
    assert await run("temp", "+1") == 0
    assert env.state().target_temp == 30
    assert await run("temp", "-2") == 0
    assert env.state().target_temp == 28
    assert await run("temp", "24") == 0
    assert env.state().target_temp == 24


async def test_power_and_is_on(env):
    assert await run("off") == 0
    assert await run("is-on") == 1
    assert await run("toggle") == 0
    assert await run("is-on") == 0


async def test_swing_and_features(env):
    assert await run("set", "--swing", "horizontal", "--health", "on", "--sleep", "on") == 0
    state = env.state()
    assert (state.swing, state.health, state.sleep) == ("horizontal", True, True)


async def test_timer_commands(env, capsys):
    assert await run("timer", "off", "--in", "90m") == 0
    assert await run("status", "--json") == 0
    out = capsys.readouterr().out.strip().splitlines()[-1]
    assert "off" in json.loads(out)["devices"][0]["timers"]
    assert await run("timer", "cancel", "off") == 0


async def test_sleep_curve_and_filter(env):
    assert await run("sleep-curve", "start", "--hours", "6") == 0
    assert env.state().sleep is True
    assert await run("sleep-curve", "cancel") == 0
    assert await run("filter", "reset") == 0


async def test_invalid_value_reports_error(env, capsys):
    assert await run("set", "--mode", "turbo") == 1
    assert "mode" in capsys.readouterr().err


def test_no_daemon(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("AIRCON_SOCKET", str(tmp_path / "missing.sock"))
    assert main(["status"]) == 1
    assert "aircond is not running" in capsys.readouterr().err
    assert main(["watch"]) == 1


@pytest.mark.parametrize(
    ("text", "minutes"),
    [("90m", 90), ("2h", 120), ("1h30m", 90), ("45", 45), ("1h 15m", 75)],
)
def test_parse_duration(text, minutes):
    assert parse_duration(text) == minutes


def test_parse_duration_rejects_garbage():
    with pytest.raises(ValueError):
        parse_duration("soon")


def test_parse_clock_returns_next_occurrence():
    now = datetime(2026, 10, 11, 23, 0).astimezone()
    at = parse_clock("07:30", now)
    assert (at.day, at.hour, at.minute) == (12, 7, 30)
    assert parse_clock("23:30", now).day == 11


# -- several devices ---------------------------------------------------------------


@pytest.fixture
async def pair(tmp_path, monkeypatch):
    living, bedroom = await FakeDongle().start(), await FakeDongle().start()
    daemon = await start_daemon(tmp_path, living.port, bedroom.port)
    await until(lambda: ready(daemon))
    monkeypatch.setenv("AIRCON_SOCKET", str(tmp_path / "a.sock"))
    yield living, bedroom
    await daemon.stop()
    await living.stop()
    await bedroom.stop()


async def test_device_option_targets_one_unit(pair, capsys):
    living, bedroom = pair
    assert await run("-d", "bedroom", "set", "--temp", "20") == 0
    assert await run("-d", "bedroom", "temp", "-1") == 0
    assert bedroom.state().target_temp == 19
    assert living.state().target_temp == 29
    assert await run("-d", "garage", "on") == 1
    assert "garage" in capsys.readouterr().err


async def test_status_lists_every_unit(pair, capsys):
    assert await run("status") == 0
    lines = capsys.readouterr().out.strip().splitlines()
    assert lines[0].startswith("Living room: on")
    assert lines[1].startswith("Bedroom: on")
    assert await run("-d", "bedroom", "status", "--json") == 0
    assert json.loads(capsys.readouterr().out)["id"] == "bedroom"


async def test_all_off(pair):
    living, bedroom = pair
    assert await run("all", "off") == 0
    assert living.state().power is False and bedroom.state().power is False
    assert await run("-d", "bedroom", "is-on") == 1
