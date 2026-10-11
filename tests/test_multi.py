import json

import pytest

from aircon.config import Config, DeviceConfig, dump_config, load_config, slugify, unique_id
from aircon.store import Store

LEGACY_CONFIG = """
[device]
name = "Living room"
backend = "haier_smartair2"
host = "192.168.1.50"
mac = "04FA83A1B2C3"
features = ["health"]

[filter]
threshold_hours = 200
"""

LIST_CONFIG = """
default = "bedroom"

[[device]]
name = "Living room"
backend = "haier_smartair2"
host = "192.168.1.50"
mac = "04FA83A1B2C3"

[[device]]
id = "bedroom"
name = "Quarto"
backend = "haier_smartair2"
host = "192.168.1.51"
mac = "04FA83000001"
"""


def write(tmp_path, text):
    path = tmp_path / "config.toml"
    path.write_text(text)
    return path


@pytest.mark.parametrize(("name", "slug"), [
    ("Living room", "living-room"), ("Sala de estar", "sala-de-estar"),
    ("Escritório", "escritorio"), ("  !!  ", "ac"), ("Quarto 2", "quarto-2"),
])
def test_slugify(name, slug):
    assert slugify(name) == slug


def test_unique_id():
    assert unique_id("sala", []) == "sala"
    assert unique_id("sala", ["sala", "sala-2"]) == "sala-3"


def test_legacy_single_table(tmp_path):
    config = load_config(write(tmp_path, LEGACY_CONFIG))
    assert [d.id for d in config.devices] == ["living-room"]
    assert config.default_id == "living-room"
    assert config.device.features == ["health"]
    assert config.filter_threshold_hours == 200


def test_device_list_and_default(tmp_path):
    config = load_config(write(tmp_path, LIST_CONFIG))
    assert [d.id for d in config.devices] == ["living-room", "bedroom"]
    assert config.default_id == "bedroom"
    assert config.get("bedroom").name == "Quarto"
    assert config.get("nope") is None


def test_duplicate_ids_rejected(tmp_path):
    text = LIST_CONFIG.replace('id = "bedroom"', 'id = "living-room"').replace(
        'default = "bedroom"', "")
    with pytest.raises(ValueError, match="duplicate"):
        load_config(write(tmp_path, text))


def test_unknown_default_rejected(tmp_path):
    with pytest.raises(ValueError, match="default"):
        load_config(write(tmp_path, LIST_CONFIG.replace('"bedroom"\n\n[[', '"garage"\n\n[[')))


def test_invalid_id_rejected(tmp_path):
    with pytest.raises(ValueError, match="invalid device id"):
        load_config(write(tmp_path, LIST_CONFIG.replace('id = "bedroom"', 'id = "Bed Room"')))


def test_no_devices_is_an_empty_config(tmp_path):
    config = load_config(write(tmp_path, "[filter]\nthreshold_hours = 300\n"))
    assert config.devices == []
    assert config.default_id is None


def test_dump_round_trip(tmp_path):
    config = Config(devices=[
        DeviceConfig(name="Sala de estar", backend="haier_smartair2", host="10.0.0.2",
                     mac="04FA83000002", features=["sleep"]),
        DeviceConfig(name="Quarto", backend="haier_smartair2", host="10.0.0.3", id="quarto",
                     options={"local_key": "abc \"1\"", "version": 3, "secure": True}),
    ], default="quarto", filter_threshold_hours=250)
    path = write(tmp_path, dump_config(config, "# written by a test"))
    loaded = load_config(path)
    assert loaded == config


def test_store_sections_are_independent(tmp_path):
    path = tmp_path / "state.json"
    Store(path, "a").save({"filter_hours": 1})
    Store(path, "b").save({"filter_hours": 2})
    assert Store(path, "a").load() == {"filter_hours": 1}
    assert Store(path, "b").load() == {"filter_hours": 2}


def test_store_adopts_legacy_state(tmp_path):
    path = tmp_path / "state.json"
    path.write_text(json.dumps({"timers": {"off": "2026-10-11T23:00:00+01:00"},
                                "sleep_curve": None, "filter_hours": 12.5}))
    first = Store(path, "living-room", adopt_legacy=True)
    assert first.load()["filter_hours"] == 12.5
    assert Store(path, "bedroom").load() == {}
    first.save(first.load())
    data = json.loads(path.read_text())
    assert "timers" not in data
    assert data["devices"]["living-room"]["filter_hours"] == 12.5


def test_options_must_be_plain_values(tmp_path):
    text = LIST_CONFIG + '\n[device.options]\nnested = { a = 1 }\n'
    with pytest.raises(ValueError, match="options"):
        load_config(write(tmp_path, text))
