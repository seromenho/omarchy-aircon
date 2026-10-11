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
    config = Config(devices=[device("Living room"), device("Quarto", "bedroom")])
    rows = dict(menu.entries(config))
    assert rows["aircon"]["label"] == "Air Conditioners"
    assert rows["aircon.bedroom"]["label"] == "Quarto"
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
    menu.sync(path, Config(devices=[device("Living room"), device("Quarto", "bedroom")]))
    text = path.read_text()
    assert menu.LEGACY_HEADER not in text
    assert '"action": "a"' not in text
    assert text.count('"aircon":') == 1
    assert '"personal"' in text


def test_sync_updates_when_devices_change(tmp_path):
    path = tmp_path / "omarchy-menu.jsonc"
    menu.sync(path, Config(devices=[device("Living room")]))
    menu.sync(path, Config(devices=[device("Living room"), device("Quarto", "bedroom")]))
    text = path.read_text()
    assert text.count(menu.BEGIN) == 1
    assert '"aircon.bedroom.toggle"' in text
    assert '"aircon.toggle"' not in text


def test_sync_creates_missing_file(tmp_path):
    path = tmp_path / "ext" / "omarchy-menu.jsonc"
    menu.sync(path, None)
    assert '"aircon.setup"' in path.read_text()
