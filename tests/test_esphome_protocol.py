from fake_esphome import climate_entity

from aircon.backends.esphome import protocol as p
from aircon.model import Capabilities, Command, State

FALLBACK = Capabilities(modes=["cool"], fan_speeds=["auto"], swing=["off"], temp_min=17,
                        temp_max=31, temp_step=1)
RAW_MODES = ["OFF", "HEAT_COOL", "COOL", "HEAT", "FAN_ONLY", "DRY"]


def test_decodes_a_real_entity():
    state = p.decode_state(climate_entity(), State())
    assert (state.power, state.mode, state.fan, state.swing) == (True, "cool", "low", "off")
    assert (state.target_temp, state.room_temp) == (24, 27)


def test_off_keeps_the_last_mode_and_reads_unknown_values_as_none():
    before = State(power=True, mode="heat", fan="high")
    state = p.decode_state(climate_entity(mode="OFF", current_temperature="NA",
                                          target_temperature="nan"), before)
    assert (state.power, state.mode) == (False, "heat")
    assert (state.room_temp, state.target_temp) == (None, 24)


def test_capabilities_follow_the_entity():
    caps = p.decode_capabilities(climate_entity(), FALLBACK)
    assert caps.modes == ["auto", "cool", "heat", "fan", "dry"]
    assert caps.fan_speeds == ["low", "mid", "high", "auto"]
    assert caps.swing == ["off", "vertical", "horizontal", "both"]
    assert (caps.temp_min, caps.temp_max, caps.temp_step) == (16, 30, 1)


def test_capabilities_without_lists_keep_the_fallback():
    assert p.decode_capabilities({"id": "climate/x"}, FALLBACK) == FALLBACK


def test_capabilities_ignore_values_that_are_not_names():
    caps = p.decode_capabilities({"modes": [[1], {"a": 2}, "COOL"], "fan_modes": "AUTO"},
                                 FALLBACK)
    assert caps.modes == ["cool"]
    assert caps.fan_speeds == FALLBACK.fan_speeds


def test_power_off_and_power_on():
    on = State(power=True, mode="dry")
    assert p.encode_command(on, Command(power=False), RAW_MODES) == {"mode": "OFF"}
    off = State(power=False, mode="heat")
    assert p.encode_command(off, Command(power=True), RAW_MODES) == {"mode": "HEAT"}


def test_a_change_while_off_switches_on():
    off = State(power=False, mode="cool")
    params = p.encode_command(off, Command(target_temp=22), RAW_MODES)
    assert params == {"mode": "COOL", "target_temperature": "22"}


def test_only_changed_fields_are_sent():
    on = State(power=True, mode="cool", fan="low")
    params = p.encode_command(on, Command(fan="high", swing="both"), RAW_MODES)
    assert params == {"fan_mode": "HIGH", "swing_mode": "BOTH"}


def test_auto_mode_and_fan_only_with_auto_speed():
    on = State(power=True, mode="cool", fan="auto")
    assert p.encode_command(on, Command(mode="auto"), RAW_MODES) == {"mode": "HEAT_COOL"}
    assert p.encode_command(on, Command(mode="auto"), ["OFF", "AUTO", "COOL"]) == {
        "mode": "AUTO"}
    assert p.encode_command(on, Command(mode="fan"), RAW_MODES) == {"mode": "FAN_ONLY",
                                                                    "fan_mode": "MEDIUM"}


def test_reached_compares_the_requested_fields():
    state = State(power=True, mode="cool", fan="low", target_temp=22)
    assert p.reached(state, {"mode": "COOL", "target_temperature": "22"})
    assert not p.reached(state, {"fan_mode": "HIGH"})
    assert not p.reached(state, {"mode": "OFF"})
    assert p.reached(State(power=False), {"mode": "OFF"})


def test_parse_events_handles_partial_blocks_and_crlf():
    events, rest = p.parse_events('event: ping\r\ndata: {"a":1}\r\n\r\nevent: state\r\ndata: {')
    assert events == [("ping", '{"a":1}')]
    events, rest = p.parse_events(rest + '"b":2}\r\n\r\n: comment\n\n')
    assert events == [("state", '{"b":2}')]
    assert rest == ""
