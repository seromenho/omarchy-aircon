import pytest

from aircon.model import Capabilities, Command, State, apply_command

CAPS = Capabilities(
    modes=["auto", "cool", "heat", "fan", "dry"],
    fan_speeds=["low", "mid", "high", "auto"],
    swing=["off", "vertical"],
    temp_min=16,
    temp_max=30,
    features=["sleep"],
)


def test_validate_clamps_temperature():
    assert Command(target_temp=40).validate(CAPS).target_temp == 30
    assert Command(target_temp=5).validate(CAPS).target_temp == 16


def test_validate_accepts_numeric_strings_and_floats():
    assert Command.from_dict({"target_temp": "22"}).validate(CAPS).target_temp == 22
    assert Command(target_temp=22.6).validate(CAPS).target_temp == 23


def test_validate_rejects_unsupported_mode():
    with pytest.raises(ValueError, match="mode"):
        Command(mode="turbo").validate(CAPS)


def test_validate_rejects_unsupported_swing():
    with pytest.raises(ValueError, match="swing"):
        Command(swing="horizontal").validate(CAPS)


def test_validate_rejects_unsupported_feature():
    with pytest.raises(ValueError, match="health"):
        Command(health=True).validate(CAPS)
    assert Command(sleep=True).validate(CAPS).sleep is True


def test_validate_rejects_non_boolean_power():
    with pytest.raises(ValueError, match="power"):
        Command(power="yes").validate(CAPS)


def test_validate_rejects_non_numeric_temperature():
    with pytest.raises(ValueError, match="target_temp"):
        Command(target_temp="warm").validate(CAPS)


def test_from_dict_rejects_unknown_fields():
    with pytest.raises(ValueError, match="foo"):
        Command.from_dict({"foo": 1})


def test_is_empty_and_changes():
    assert Command().is_empty()
    cmd = Command(mode="heat", target_temp=22)
    assert not cmd.is_empty()
    assert cmd.changes() == {"mode": "heat", "target_temp": 22}


def test_apply_command_merges_only_given_fields():
    state = State(power=True, mode="cool", fan="low", target_temp=29, room_temp=26)
    new = apply_command(state, Command(target_temp=24))
    assert new.target_temp == 24
    assert new.mode == "cool"
    assert new.room_temp == 26
    assert state.target_temp == 29


def test_to_dict_has_all_fields():
    assert set(State().to_dict()) == {
        "power", "mode", "fan", "target_temp", "room_temp", "room_humidity",
        "swing", "health", "sleep", "alarm",
    }
    assert CAPS.to_dict()["temp_max"] == 30
