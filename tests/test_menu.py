from aircon import menu
from aircon.config import Config, DeviceConfig

USER_FILE = """{
  // Extend the Quickshell Omarchy menu with JSONC.
  "personal": {"icon":"","label":"Personal"},
  "personal.notes": {"icon":"󰎞","label":"Notes","action":"omarchy-launch-editor ~/notes"},
}
"""

LEGACY_FILE = """{
  // comments
  "personal": {"icon":"","label":"Personal"},

  // Air conditioner (added by aircon/install.sh)
  "aircon": {"icon": "x", "label": "Air Conditioner"},
  "aircon.toggle": {"icon": "x", "label": "Power on/off", "action": "a"},
  "aircon.panel": {"icon": "x", "label": "Open panel", "action": "b"},
}
"""


def device(name: str, device_id: str = "") -> DeviceConfig:
    return DeviceConfig(name=name, backend="haier_smartair2", host="10.0.0.2", id=device_id)


def ids(config) -> list[str]:
    return [key for key, _ in menu.entries(config)]


def test_entries_without_devices():
    assert ids(Config()) == ["aircon", "aircon.setup"]
    assert ids(None) == ["aircon", "aircon.setup"]


def test_entries_single_device_are_flat():
    rows = dict(menu.entries(Config(devices=[device("Living room")])))
    assert "aircon.toggle" in rows and "aircon.panel" in rows
    assert rows["aircon.toggle"]["action"] == "$HOME/.local/bin/aircon toggle"
    assert rows["aircon"]["label"] == "Air Conditioner"


def test_entries_several_devices_get_submenus():
    config = Config(devices=[device("Living room"), device("Bedroom", "bedroom")])
    rows = dict(menu.entries(config))
    assert rows["aircon"]["label"] == "Air Conditioners"
    assert rows["aircon.bedroom"]["label"] == "Bedroom"
    assert rows["aircon.bedroom.heat"]["action"].endswith("-d bedroom set --mode heat --temp 22")
    assert rows["aircon.all-off"]["action"].endswith("aircon all off")
    assert "aircon.toggle" not in rows


def test_sync_keeps_user_entries_and_is_idempotent(tmp_path):
    path = tmp_path / "omarchy-menu.jsonc"
    path.write_text(USER_FILE)
    config = Config(devices=[device("Living room")])
    assert menu.sync(path, config) is True
    text = path.read_text()
    assert text.startswith(USER_FILE.rstrip()[:-1].rstrip())
    assert '"personal.notes"' in text
    assert text.count(menu.BEGIN) == 1
    assert text.rstrip().endswith("}")
    assert menu.sync(path, config) is False
    assert (tmp_path / "omarchy-menu.jsonc.aircon.bak").read_text() == USER_FILE


def test_sync_replaces_legacy_block(tmp_path):
    path = tmp_path / "omarchy-menu.jsonc"
    path.write_text(LEGACY_FILE)
    menu.sync(path, Config(devices=[device("Living room"), device("Bedroom", "bedroom")]))
    text = path.read_text()
    assert menu.LEGACY_HEADER not in text
    assert '"action": "a"' not in text
    assert text.count('"aircon":') == 1
    assert '"personal"' in text


def test_sync_updates_when_devices_change(tmp_path):
    path = tmp_path / "omarchy-menu.jsonc"
    menu.sync(path, Config(devices=[device("Living room")]))
    menu.sync(path, Config(devices=[device("Living room"), device("Bedroom", "bedroom")]))
    text = path.read_text()
    assert text.count(menu.BEGIN) == 1
    assert '"aircon.bedroom.toggle"' in text
    assert '"aircon.toggle"' not in text


def test_sync_creates_missing_file(tmp_path):
    path = tmp_path / "ext" / "omarchy-menu.jsonc"
    menu.sync(path, None)
    assert '"aircon.setup"' in path.read_text()


def test_remove_drops_only_our_block(tmp_path):
    path = tmp_path / "omarchy-menu.jsonc"
    path.write_text(USER_FILE)
    menu.sync(path, Config(devices=[device("Living room")]))
    assert menu.remove(path) is True
    text = path.read_text()
    assert menu.BEGIN not in text
    assert '"personal.notes"' in text
    assert text.rstrip().endswith("}")
    assert menu.remove(path) is False
    assert menu.remove(tmp_path / "missing.jsonc") is False


def test_is_installed(tmp_path):
    path = tmp_path / "omarchy-menu.jsonc"
    assert menu.is_installed(path) is False
    path.write_text(USER_FILE)
    assert menu.is_installed(path) is False
    menu.sync(path, None)
    assert menu.is_installed(path) is True
    menu.remove(path)
    assert menu.is_installed(path) is False
    path.write_text(LEGACY_FILE)
    assert menu.is_installed(path) is True


def test_menu_status_command(capsys):
    from aircon.cli import main

    assert main(["menu", "status"]) == 1
    assert not menu.default_menu_path().exists()
    assert main(["menu", "sync"]) == 0
    assert main(["menu", "status"]) == 0
    assert "has aircon entries" in capsys.readouterr().out


def test_open_panel_uses_the_plugin_id():
    import json
    from pathlib import Path

    manifest = json.loads((Path(__file__).resolve().parents[1] / "manifest.json").read_text())
    assert menu.OPEN_PANEL.split()[-1] == manifest["id"]
