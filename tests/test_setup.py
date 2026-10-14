import asyncio
import json
import socket

import pytest
from conftest import MAC
from fake_dongle import FakeDongle

from aircon import menu, setup
from aircon.cli import main
from aircon.config import load_config
from aircon.daemon import Daemon

ARP_SAMPLE = """IP address       HW type     Flags       HW address            Mask     Device
192.168.1.50    0x1         0x2         04:fa:83:a1:b2:c3     *        enp195s0
192.168.1.99     0x1         0x0         00:00:00:00:00:00     *        enp195s0
"""


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


@pytest.fixture
async def dongle():
    fake = await FakeDongle().start()
    yield fake
    await fake.stop()


def test_read_arp(tmp_path):
    path = tmp_path / "arp"
    path.write_text(ARP_SAMPLE)
    assert setup.read_arp(path) == {"192.168.1.50": "04:fa:83:a1:b2:c3"}


def test_candidate_vendor_and_dict():
    candidate = setup.Candidate(host="192.168.1.50", mac="04:fa:83:a1:b2:c3", state=None)
    assert candidate.is_haier
    assert candidate.to_dict()["mac"] == "04FA83A1B2C3"
    assert not setup.Candidate(host="10.0.0.2", mac="aa:bb:cc:00:00:01", state=None).is_haier


async def test_discover_finds_the_fake_dongle(dongle):
    found = await setup.discover(hosts=["127.0.0.1"], port=dongle.port, mac_lookup=lambda _: MAC)
    assert len(found) == 1
    assert found[0].state is not None
    assert found[0].state.room_temp == 26


async def test_discover_without_mac_reports_unknown_state(dongle):
    found = await setup.discover(hosts=["127.0.0.1"], port=dongle.port, mac_lookup=lambda _: None)
    assert found[0].mac is None
    assert found[0].state is None


async def test_discover_nothing_listening():
    assert await setup.discover(hosts=["127.0.0.1"], port=free_port()) == []


async def test_read_state_unreachable():
    assert await setup.read_state("127.0.0.1", MAC, free_port(), timeout=1) is None


async def test_validate_features_and_restore(dongle):
    before = dongle.state()
    result = await setup.validate_features("127.0.0.1", MAC, dongle.port, settle=0)
    assert result.swing_style == "bits"
    assert set(result.features) == {"health", "sleep", "swing_horizontal"}
    assert dongle.state() == before


def test_add_rename_remove_devices(tmp_path):
    path = tmp_path / "config.toml"
    first, created = setup.add_device(path, name="Dining room", host="10.0.0.2",
                                      mac="04:fa:83:00:00:02")
    assert created and first.id == "dining-room"
    second, _ = setup.add_device(path, name="Dining room", host="10.0.0.3", mac="04FA83000003")
    assert second.id == "dining-room-2"
    same, created = setup.add_device(path, name="Dining", host="10.0.0.9", mac="04FA83000002")
    assert not created and same.id == "dining-room" and same.host == "10.0.0.9"
    assert [d.id for d in load_config(path).devices] == ["dining-room", "dining-room-2"]

    setup.rename_device(path, "dining-room-2", "Bedroom")
    assert load_config(path).get("dining-room-2").name == "Bedroom"
    config = load_config(path)
    config.default = "dining-room"
    setup.save_config(path, config)
    remaining = setup.remove_device(path, "dining-room")
    assert [d.id for d in remaining.devices] == ["dining-room-2"]
    assert load_config(path).default_id == "dining-room-2"
    with pytest.raises(LookupError):
        setup.remove_device(path, "nope")


def test_add_device_keeps_validation(tmp_path):
    path = tmp_path / "config.toml"
    validation = setup.ValidationResult(swing_style="byte", features=["sleep"])
    device, _ = setup.add_device(path, name="Bedroom", host="10.0.0.4", mac="04FA83000004",
                                 validation=validation)
    loaded = load_config(path).get(device.id)
    assert (loaded.swing_style, loaded.features) == ("byte", ["sleep"])


async def test_cli_apply_writes_config(dongle, tmp_path, capsys):
    path = tmp_path / "config.toml"
    code = await asyncio.to_thread(main, [
        "setup", "apply", "--host", "127.0.0.1", "--port", str(dongle.port), "--mac", MAC,
        "--name", "Living room", "--validate", "--settle", "0", "--no-restart",
        "--config", str(path), "--json",
    ])
    assert code == 0
    result = json.loads(capsys.readouterr().out)
    assert result["ok"] is True
    assert result["restarted"] is False
    assert result["id"] == "living-room" and result["created"] is True
    config = load_config(path)
    assert config.device.host == "127.0.0.1"
    assert config.device.port == dongle.port
    assert "swing_horizontal" in config.device.features


async def test_cli_apply_reports_unreachable(tmp_path, capsys):
    code = await asyncio.to_thread(main, [
        "setup", "apply", "--host", "127.0.0.1", "--port", str(free_port()), "--mac", MAC,
        "--no-restart", "--config", str(tmp_path / "c.toml"), "--json",
    ])
    assert code == 1
    assert json.loads(capsys.readouterr().out)["ok"] is False
    assert not (tmp_path / "c.toml").exists()


async def test_cli_discover_json(dongle, capsys):
    code = await asyncio.to_thread(main, [
        "setup", "discover", "--hosts", "127.0.0.1", "--port", str(dongle.port), "--json",
    ])
    assert code == 0
    found = json.loads(capsys.readouterr().out)
    assert found[0]["host"] == "127.0.0.1"
    assert found[0]["configured"] is False


def test_wizard_with_nothing_found(monkeypatch, capsys):
    monkeypatch.setattr(setup, "default_subnets", lambda: [])
    assert main(["setup", "--backend", "haier_smartair2", "--no-restart"]) == 1
    assert "No air conditioner answered" in capsys.readouterr().out


async def test_daemon_without_config(tmp_path):
    daemon = Daemon(None, tmp_path / "a.sock", tmp_path / "state.json",
                    config_error="no config at /x")
    await daemon.start()
    try:
        reader, writer = await asyncio.open_unix_connection(str(tmp_path / "a.sock"))
        writer.write(b'{"op": "get"}\n{"op": "set", "power": true}\n')
        await writer.drain()
        got = json.loads(await reader.readline())
        refused = json.loads(await reader.readline())
        writer.close()
        assert got["state"]["configured"] is False
        assert got["state"]["devices"] == []
        assert refused["ok"] is False
        assert "aircon setup" in refused["error"]
    finally:
        await daemon.stop()


def test_status_when_not_configured(tmp_path, monkeypatch, capsys):
    async def scenario():
        daemon = Daemon(None, tmp_path / "a.sock", tmp_path / "state.json")
        await daemon.start()
        try:
            return await asyncio.to_thread(main, ["status"])
        finally:
            await daemon.stop()

    monkeypatch.setenv("AIRCON_SOCKET", str(tmp_path / "a.sock"))
    assert asyncio.run(scenario()) == 0
    assert "Run: aircon setup" in capsys.readouterr().out


async def test_setup_adds_second_device_and_syncs_menu(dongle, tmp_path, capsys):
    import os
    path = tmp_path / "config.toml"
    setup.add_device(path, name="Living room", host="10.0.0.2", mac="04FA83000002")
    menu.sync(menu.default_menu_path(), None)  # the user opted in to the menu entries
    code = await asyncio.to_thread(main, [
        "setup", "apply", "--host", "127.0.0.1", "--port", str(dongle.port), "--mac", MAC,
        "--name", "Bedroom", "--no-restart", "--config", str(path), "--json",
    ])
    assert code == 0
    assert json.loads(capsys.readouterr().out)["id"] == "bedroom"
    assert [d.id for d in load_config(path).devices] == ["living-room", "bedroom"]
    menu_text = open(os.environ["AIRCON_MENU_FILE"]).read()
    assert '"aircon.bedroom.toggle"' in menu_text


def test_wizard_skips_configured_units(monkeypatch, capsys, tmp_path):
    candidate = setup.Candidate(host="10.0.0.2", mac="04FA83000002",
                                state=None, configured=True)

    async def fake_discover(**kwargs):
        return [candidate]

    monkeypatch.setattr(setup, "discover", fake_discover)
    assert main(["setup", "--backend", "haier_smartair2", "--no-restart"]) == 1
    assert "already added" in capsys.readouterr().out


def test_cli_devices_rename_remove(tmp_path, capsys):
    import os
    path = tmp_path / "config.toml"
    setup.add_device(path, name="Living room", host="10.0.0.2", mac="04FA83000002")
    setup.add_device(path, name="Bedroom", host="10.0.0.3", mac="04FA83000003")
    os.environ["AIRCON_CONFIG"] = str(path)
    menu.sync(menu.default_menu_path(), None)  # the user opted in to the menu entries
    assert main(["devices", "--json"]) == 0
    rows = json.loads(capsys.readouterr().out)
    assert [r["id"] for r in rows] == ["living-room", "bedroom"] and rows[0]["default"]
    assert main(["rename", "bedroom", "Guest room", "--no-restart"]) == 0
    assert load_config(path).get("bedroom").name == "Guest room"
    assert main(["remove", "living-room", "--no-restart"]) == 0
    assert [d.id for d in load_config(path).devices] == ["bedroom"]
    assert main(["remove", "nope", "--no-restart"]) == 1
    menu_text = open(os.environ["AIRCON_MENU_FILE"]).read()
    assert '"aircon.toggle"' in menu_text  # back to a single, flat device menu


def test_setup_and_rename_leave_the_menu_alone_without_entries(tmp_path, monkeypatch):
    path = tmp_path / "config.toml"
    setup.add_device(path, name="Living room", host="10.0.0.2", mac="04FA83000002")
    monkeypatch.setenv("AIRCON_CONFIG", str(path))
    menu_path = menu.default_menu_path()
    assert main(["rename", "living-room", "Lounge", "--no-restart"]) == 0
    assert not menu_path.exists()
    menu_path.write_text("{\n}\n")
    assert main(["rename", "living-room", "Den", "--no-restart"]) == 0
    assert menu_path.read_text() == "{\n}\n"
