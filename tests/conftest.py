import pytest

# Status frame captured from the real AC on 2026-10-11:
# room 26 °C, humidity 51 %, cool, fan low, swing off, power on, target 29 °C.
REAL_STATUS_FRAME = bytes.fromhex(
    "ff ff 22 00 00 00 00 00 01 06 6d 01 00 1a 00 33 00 7f 00 00 00 00 00 01"
    " 00 02 00 00 00 01 00 00 00 00 00 0d 74"
)
INFO_FRAME = bytes.fromhex("ff ff 10 00 00 00 00 00 01 04 0f 5a 00 00 00 00 00 00 7e")
MAC = "04FA83A1B2C3"


@pytest.fixture
def real_frame() -> bytes:
    return REAL_STATUS_FRAME


@pytest.fixture(autouse=True)
def isolated_user_files(tmp_path, monkeypatch):
    """Never let a test touch the real config, menu file or daemon socket."""
    monkeypatch.setenv("AIRCON_CONFIG", str(tmp_path / "user-config.toml"))
    monkeypatch.setenv("AIRCON_MENU_FILE", str(tmp_path / "omarchy-menu.jsonc"))
    monkeypatch.setenv("AIRCON_SOCKET", str(tmp_path / "no-daemon.sock"))
