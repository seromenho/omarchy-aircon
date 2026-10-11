"""Frame codec for the Haier SmartAir2 local protocol (TCP 56800, unencrypted).

Every message is an 80-byte header followed by a Haier frame:

    header[0:4]   00 00 27 14 (request) / 00 00 27 15 (response)
    header[40:52] module MAC address as 12 ASCII hex characters
    header[75]    sequence number, echoed back in the reply
    header[79]    length of the Haier frame that follows

    frame         FF FF <len> ... <checksum>, checksum = sum(frame[2:-1]) & 0xFF

Status frame offsets follow ESPHome's smartair2_packet.h, shifted by two bytes
for the leading FF FF.
"""

from __future__ import annotations

from aircon.model import State

HEADER_LEN = 80
REQ_MAGIC = b"\x00\x00\x27\x14"
RESP_MAGIC = b"\x00\x00\x27\x15"

QUERY = bytes.fromhex("ff ff 0a 00 00 00 00 00 00 01 4d 01 59")
POWER_ON = bytes.fromhex("ff ff 0a 00 00 00 00 00 00 01 4d 02 5a")
POWER_OFF = bytes.fromhex("ff ff 0a 00 00 00 00 00 00 01 4d 03 5b")
CONTROL_PREFIX = bytes.fromhex("ff ff 22 00 00 00 00 00 00 01 4d 5f")

FRAME_STATUS = 0x22
FRAME_INFO = 0x10
STATUS_LEN = 37

OFF_TYPE = 2
OFF_ROOM = 13
OFF_HUMIDITY = 15
OFF_CNTRL = 17
OFF_MODE = 23
OFF_FAN = 25
OFF_SWING = 27
OFF_LOCKS = 28  # bit3 Fahrenheit, bit7 remote lock
OFF_POWER = 29  # bit0 power, bit3 health
OFF_FLAGS = 31  # bit0 use swing bits, bit1 turbo, bit2 quiet/sleep, bit3 horizontal,
#                 bit4 vertical, bit5 display off
OFF_SETPOINT = 35

POWER_BIT = 0x01
HEALTH_BIT = 0x08
SWING_BITS_FLAG = 0x01
TURBO_BIT = 0x02
SLEEP_BIT = 0x04
HORIZONTAL_BIT = 0x08
VERTICAL_BIT = 0x10
DISPLAY_OFF_BIT = 0x20

TEMP_OFFSET = 16

HAIER_MODES = {"auto": 0, "cool": 1, "heat": 2, "fan": 3, "dry": 4}
HAIER_FANS = {"high": 0, "mid": 1, "low": 2, "auto": 3}
SWING_BYTE_STYLE = {"off": 0, "vertical": 1, "horizontal": 2, "both": 3}
SWING_STYLES = ("bits", "byte")

_MODES_BY_CODE = {v: k for k, v in HAIER_MODES.items()}
_FANS_BY_CODE = {v: k for k, v in HAIER_FANS.items()}
_SWING_BY_CODE = {v: k for k, v in SWING_BYTE_STYLE.items()}


def checksum(frame_without_checksum: bytes) -> int:
    return sum(frame_without_checksum[2:]) & 0xFF


def build_request(mac: str, seq: int, payload: bytes) -> bytes:
    mac_ascii = mac.replace(":", "").replace("-", "").upper().encode("ascii")
    if len(mac_ascii) != 12:
        raise ValueError(f"invalid MAC address: {mac!r}")
    header = bytearray(HEADER_LEN)
    header[0:4] = REQ_MAGIC
    header[40:52] = mac_ascii
    header[75] = seq & 0xFF
    header[79] = len(payload)
    return bytes(header) + payload


def is_valid_frame(frame: bytes) -> bool:
    return (
        len(frame) >= 4
        and frame[0:2] == b"\xff\xff"
        and frame[OFF_TYPE] == len(frame) - 3
        and checksum(frame[:-1]) == frame[-1]
    )


def parse_stream(buf: bytes) -> tuple[list[tuple[int, bytes]], bytes]:
    """Split a TCP byte stream into (sequence, frame) pairs plus the unconsumed rest."""
    frames: list[tuple[int, bytes]] = []
    pos = 0
    while True:
        idx = buf.find(RESP_MAGIC, pos)
        if idx < 0:
            # Keep a possible partial magic at the very end.
            return frames, buf[max(pos, len(buf) - (len(RESP_MAGIC) - 1)) :]
        if len(buf) - idx < HEADER_LEN:
            return frames, buf[idx:]
        total = HEADER_LEN + buf[idx + HEADER_LEN - 1]
        if len(buf) - idx < total:
            return frames, buf[idx:]
        frame = buf[idx + HEADER_LEN : idx + total]
        if is_valid_frame(frame):
            frames.append((buf[idx + 75], frame))
        pos = idx + total


def decode_state(frame: bytes, swing_style: str = "bits") -> State:
    if len(frame) < STATUS_LEN or frame[OFF_TYPE] != FRAME_STATUS:
        raise ValueError("not a status frame")
    power = frame[OFF_POWER]
    flags = frame[OFF_FLAGS]
    humidity = frame[OFF_HUMIDITY]
    return State(
        power=bool(power & POWER_BIT),
        mode=_MODES_BY_CODE.get(frame[OFF_MODE], "auto"),
        fan=_FANS_BY_CODE.get(frame[OFF_FAN], "auto"),
        target_temp=frame[OFF_SETPOINT] + TEMP_OFFSET,
        room_temp=frame[OFF_ROOM],
        room_humidity=humidity or None,
        swing=_decode_swing(frame[OFF_SWING], flags, swing_style),
        health=bool(power & HEALTH_BIT),
        sleep=bool(flags & SLEEP_BIT),
    )


def _decode_swing(swing_byte: int, flags: int, style: str) -> str:
    if style == "byte":
        return _SWING_BY_CODE.get(swing_byte, "off")
    if swing_byte == 1:
        return "both"
    if flags & VERTICAL_BIT:
        return "vertical"
    if flags & HORIZONTAL_BIT:
        return "horizontal"
    return "off"


def encode_control(last_frame: bytes, state: State, swing_style: str) -> bytes:
    """Build a 4D 5F group command that sets every writable field from `state`.

    Unmodelled bytes start from zero, like the reference implementations do, except
    the lock/unit byte and the display bit, which are kept from the last status frame.
    """
    if swing_style not in SWING_STYLES:
        raise ValueError(f"swing_style must be one of: {', '.join(SWING_STYLES)}")
    frame = bytearray(STATUS_LEN)
    frame[: len(CONTROL_PREFIX)] = CONTROL_PREFIX
    frame[OFF_LOCKS] = last_frame[OFF_LOCKS]
    frame[OFF_MODE] = HAIER_MODES[state.mode]
    frame[OFF_FAN] = HAIER_FANS[state.fan]
    frame[OFF_POWER] = (POWER_BIT if state.power else 0) | (HEALTH_BIT if state.health else 0)

    flags = last_frame[OFF_FLAGS] & DISPLAY_OFF_BIT
    if state.sleep:
        flags |= SLEEP_BIT
    if swing_style == "byte":
        frame[OFF_SWING] = SWING_BYTE_STYLE[state.swing]
    elif state.swing == "both":
        frame[OFF_SWING] = 1
    elif state.swing == "vertical":
        flags |= SWING_BITS_FLAG | VERTICAL_BIT
    elif state.swing == "horizontal":
        flags |= SWING_BITS_FLAG | HORIZONTAL_BIT
    frame[OFF_FLAGS] = flags

    frame[OFF_SETPOINT] = max(0, state.target_temp - TEMP_OFFSET)
    frame[-1] = checksum(bytes(frame[:-1]))
    return bytes(frame)
