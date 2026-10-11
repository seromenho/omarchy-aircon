import pytest
from conftest import INFO_FRAME, MAC, REAL_STATUS_FRAME

from aircon.backends.haier_smartair2 import protocol as p
from aircon.model import State


def response(seq: int, frame: bytes) -> bytes:
    header = bytearray(p.HEADER_LEN)
    header[0:4] = p.RESP_MAGIC
    header[40:52] = MAC.encode()
    header[75] = seq
    header[79] = len(frame)
    return bytes(header) + frame


def test_checksum_matches_real_frame():
    assert p.checksum(REAL_STATUS_FRAME[:-1]) == REAL_STATUS_FRAME[-1]
    assert p.checksum(p.QUERY[:-1]) == p.QUERY[-1]
    assert p.checksum(p.POWER_ON[:-1]) == p.POWER_ON[-1]
    assert p.checksum(p.POWER_OFF[:-1]) == p.POWER_OFF[-1]


def test_decode_real_status_frame():
    state = p.decode_state(REAL_STATUS_FRAME)
    assert state == State(
        power=True, mode="cool", fan="low", target_temp=29, room_temp=26,
        room_humidity=51, swing="off", health=False, sleep=False,
    )


def test_decode_rejects_non_status_frames():
    with pytest.raises(ValueError):
        p.decode_state(INFO_FRAME)


def test_build_request_layout():
    req = p.build_request("04:fa:83:a1:b2:c3", 7, p.QUERY)
    assert len(req) == 80 + len(p.QUERY)
    assert req[0:4] == p.REQ_MAGIC
    assert req[40:52] == MAC.encode()
    assert req[75] == 7
    assert req[79] == len(p.QUERY)
    assert req[80:] == p.QUERY


def test_build_request_rejects_bad_mac():
    with pytest.raises(ValueError):
        p.build_request("1234", 1, p.QUERY)


def test_parse_stream_reassembles_split_frames():
    data = response(0, INFO_FRAME) + response(3, REAL_STATUS_FRAME)
    frames, rest = p.parse_stream(data[:100])
    assert frames == [(0, INFO_FRAME)]
    frames, rest = p.parse_stream(rest + data[100:])
    assert frames == [(3, REAL_STATUS_FRAME)]
    assert rest == b""


def test_parse_stream_skips_garbage_and_bad_checksums():
    bad = bytearray(REAL_STATUS_FRAME)
    bad[-1] ^= 0xFF
    data = b"xyz" + response(1, bytes(bad)) + response(2, REAL_STATUS_FRAME)
    frames, rest = p.parse_stream(data)
    assert frames == [(2, REAL_STATUS_FRAME)]
    assert rest == b""


def test_encode_control_sets_fields_and_checksum():
    target = State(power=True, mode="heat", fan="auto", target_temp=22, swing="off")
    frame = p.encode_control(REAL_STATUS_FRAME, target, "bits")
    assert len(frame) == len(REAL_STATUS_FRAME)
    assert frame[:12] == bytes.fromhex("ff ff 22 00 00 00 00 00 00 01 4d 5f")
    assert frame[p.OFF_CNTRL] == 0
    assert frame[p.OFF_MODE] == 2
    assert frame[p.OFF_FAN] == 3
    assert frame[p.OFF_SETPOINT] == 6
    assert frame[p.OFF_POWER] & 0x01 == 1
    assert p.checksum(frame[:-1]) == frame[-1]
    decoded = p.decode_state(frame)
    summary = (decoded.mode, decoded.fan, decoded.target_temp, decoded.power)
    assert summary == ("heat", "auto", 22, True)


def test_encode_control_health_and_sleep_bits():
    target = State(power=True, health=True, sleep=True)
    frame = p.encode_control(REAL_STATUS_FRAME, target, "bits")
    decoded = p.decode_state(frame)
    assert decoded.health is True
    assert decoded.sleep is True


def test_encode_control_keeps_display_bit():
    status = bytearray(REAL_STATUS_FRAME)
    status[p.OFF_FLAGS] |= 0x20  # display off
    status[-1] = p.checksum(bytes(status[:-1]))
    frame = p.encode_control(bytes(status), State(power=True), "bits")
    assert frame[p.OFF_FLAGS] & 0x20


@pytest.mark.parametrize("style", ["bits", "byte"])
@pytest.mark.parametrize("swing", ["off", "vertical", "horizontal", "both"])
def test_swing_round_trips(style, swing):
    frame = p.encode_control(REAL_STATUS_FRAME, State(power=True, swing=swing), style)
    assert p.decode_state(frame, style).swing == swing


def test_swing_byte_style_encoding():
    frame = p.encode_control(REAL_STATUS_FRAME, State(power=True, swing="horizontal"), "byte")
    assert frame[p.OFF_SWING] == 2
