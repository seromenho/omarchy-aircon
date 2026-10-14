import asyncio
import builtins
import json
import stat
import sys
from types import SimpleNamespace

import pytest
from fake_midea import FakeMidea

from aircon import cli, setup, setup_cli
from aircon.backends import create_backend
from aircon.backends.midea_lan import protocol as p
from aircon.backends.midea_lan.backend import MideaLANBackend
from aircon.config import DeviceConfig, load_config
from aircon.model import Command


def device(**kwargs):
    return DeviceConfig(name="Midea test", backend="midea_lan", host="192.168.1.50",
                        options=kwargs.pop("options", {"device_id": "123456789"}), **kwargs)


async def until(predicate):
    async with asyncio.timeout(2):
        while not predicate():
            await asyncio.sleep(0.005)


@pytest.fixture
async def backend(monkeypatch):
    fake = FakeMidea()
    monkeypatch.setattr(p, "create_client", lambda *_: fake)
    backend = MideaLANBackend(device(), poll_interval=0.02, timeout=0.05, reconnect_max=0.01)
    await backend.start()
    await until(lambda: backend.connected)
    yield backend, fake
    await backend.close()
    assert fake.closed >= 1


def test_settings_and_registry():
    backend = create_backend(device())
    assert isinstance(backend, MideaLANBackend)
    assert backend.settings.port == 6444
    assert p.settings(device(port=6500)).port == 6500
    config = p.settings(device(options={"device_id": 123, "token": "01" * 64, "key": "02" * 32}))
    assert config.version == 3


@pytest.mark.parametrize("options", [
    {}, {"device_id": True}, {"device_id": "-1"}, {"device_id": 1.5},
    {"device_id": 2**48}, {"device_id": 0}, {"device_id": 1, "protocol_version": True},
    {"device_id": 1, "protocol_version": 1}, {"device_id": 1, "protocol_version": 3},
    {"device_id": 1, "token": "bad", "key": "02" * 32},
    {"device_id": 1, "token": "01" * 64, "key": "02"},
    {"device_id": 1, "protocol_version": 2, "token": "01"},
    {"device_id": 1, "password": "never-store-cloud-passwords"},
])
def test_invalid_options(options):
    with pytest.raises(ValueError):
        create_backend(device(options=options))


async def test_initial_state_and_capabilities(backend):
    b, fake = backend
    assert (b.state.mode, b.state.fan, b.state.target_temp, b.state.room_temp) == (
        "cool", "low", 24.5, 26.5)
    assert b.capabilities.features == []
    assert b.capabilities.swing == ["off", "vertical"]
    fake.supported_operation_modes.remove(fake.OperationalMode.HEAT)
    fake.supported_swing_modes = [fake.SwingMode.OFF, fake.SwingMode.VERTICAL]
    fake.max_target_temperature = 28.5
    caps = p.capabilities(fake, ["sleep", "health"])
    assert "heat" not in caps.modes
    assert caps.swing == ["off", "vertical"]
    assert caps.temp_max == 28
    assert set(caps.features) == {"sleep", "health"}
    fake.supports_purifier = False
    assert p.capabilities(fake, ["health"]).features == []


async def test_command_roundtrip_and_preserves_extra_settings(backend):
    b, fake = backend
    b.capabilities = p.capabilities(fake, ["swing_horizontal"])
    fake.saved["eco"] = False  # changed with a remote since the last poll
    for mode in p.MODES:
        state = await b.apply(Command(mode=mode))
        assert state.mode == mode
    for fan in p.FANS:
        assert (await b.apply(Command(fan=fan))).fan == fan
    for swing in p.SWINGS:
        assert (await b.apply(Command(swing=swing))).swing == swing
    assert fake.saved["eco"] is False
    assert fake.saved["fahrenheit"] is True
    assert fake.saved["target_temperature"] == 24.5
    assert (await b.apply(Command(target_temp=22))).target_temp == 22
    assert (await b.apply(Command(power=False))).power is False
    assert (await b.apply(Command(mode="heat"))).power is True
    assert (await b.apply(Command(power=False, target_temp=23))).power is False
    assert (await b.apply(Command(power=True))).power is True


async def test_optional_sleep_and_purifier_roundtrip(backend):
    b, fake = backend
    b.capabilities = p.capabilities(fake, ["sleep", "health"])
    state = await b.apply(Command(sleep=True, health=True))
    assert state.sleep and state.health
    state = await b.apply(Command(sleep=False, health=False))
    assert not state.sleep and not state.health


async def test_invalid_optional_feature_does_not_disconnect(backend):
    b, fake = backend
    before = len(fake.requests)
    with pytest.raises(ValueError, match="not supported"):
        await b.apply(Command(health=True))
    assert len(fake.requests) == before and b.connected


async def test_timeout_and_reconnect(backend):
    b, fake = backend
    events = []
    b.on_connection(events.append)
    fake.delay = 0.1
    with pytest.raises(ConnectionError):
        await b.refresh()
    assert not b.connected
    fake.delay = 0
    await until(lambda: events[-2:] == [False, True])
    assert b.last_error is None


async def test_dropped_reply_is_not_stale_success(backend):
    b, fake = backend
    fake.ignore_apply = True
    with pytest.raises(ConnectionError):
        await b.apply(Command(target_temp=20))
    assert b.state.target_temp == 24.5 and not b.connected
    fake.ignore_apply = False
    await until(lambda: b.connected)
    assert (await b.refresh()).target_temp == 24.5


async def test_unreachable_start_is_nonblocking(monkeypatch):
    fake = FakeMidea()
    fake.unreachable = True
    monkeypatch.setattr(p, "create_client", lambda *_: fake)
    b = MideaLANBackend(device(), poll_interval=0.01, reconnect_max=0.01)
    await asyncio.wait_for(b.start(), 0.05)
    await until(lambda: b.last_error is not None)
    assert not b.connected
    fake.unreachable = False
    await until(lambda: b.connected)
    await b.close()


async def test_v3_authentication_and_redacted_failure(monkeypatch):
    fake = FakeMidea()
    token, key = "01" * 64, "02" * 32
    monkeypatch.setattr(p, "create_client", lambda *_: fake)
    b = MideaLANBackend(device(options={"device_id": 1, "token": token, "key": key}),
                        poll_interval=0.01, reconnect_max=0.01)
    fake.auth_error = True
    await b.start()
    await until(lambda: b.last_error is not None)
    assert token not in b.last_error and key not in b.last_error
    fake.auth_error = False
    await until(lambda: b.connected)
    assert fake.authenticated[-1] == (token, key)
    await b.close()


async def test_failing_listener_and_repeated_start(backend):
    b, fake = backend
    b.on_state(lambda _: 1 / 0)
    task = b._task
    await b.start()
    assert task is b._task
    assert (await b.apply(Command(target_temp=21))).target_temp == 21
    assert b.connected


def test_percent_fan_unknown_room_and_error():
    fake = FakeMidea()
    fake.fan_speed = 55
    fake.indoor_temperature = None
    fake.error_code = 7
    state = p.decode_state(fake)
    assert state.fan == "mid" and state.room_temp is None and state.alarm == "Midea error 7"


async def test_missing_optional_dependency_keeps_backend_alive(monkeypatch):
    original = builtins.__import__

    def missing(name, *args, **kwargs):
        if name.startswith("msmart"):
            raise ImportError("not installed")
        return original(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", missing)
    b = MideaLANBackend(device(), reconnect_max=0.01)
    await b.start()
    await until(lambda: b.last_error is not None)
    assert b.last_error == p.DEPENDENCY_HELP and not b.connected
    await b.close()


async def test_discovery_never_authenticates_or_exposes_secrets(monkeypatch):
    calls = []

    async def discover(**kwargs):
        calls.append(kwargs)
        return [SimpleNamespace(type=0xAC, version=3, id=123, ip="192.168.1.50", port=6444),
                SimpleNamespace(type=0xA1, version=3), SimpleNamespace(type=0xAC, version=1)]

    monkeypatch.setitem(sys.modules, "msmart.discover",
                        SimpleNamespace(Discover=SimpleNamespace(discover=discover)))
    candidates = await setup.discover_midea(["192.168.1.50"], ["123"])
    assert len(candidates) == 1 and candidates[0].configured
    assert calls[0]["auto_connect"] is False
    item = candidates[0].to_dict()
    assert item["backend"] == "midea_lan" and item["options"]["protocol_version"] == 3
    assert not {"token", "key"} & set(item["options"])


def test_cli_add_update_and_private_options(tmp_path, capsys):
    options = tmp_path / "credentials.toml"
    token, key = "01" * 64, "02" * 32
    options.write_text(f'device_id = "123"\ntoken = "{token}"\nkey = "{key}"\n')
    config = tmp_path / "config.toml"
    args = ["setup", "apply", "--backend", "midea_lan", "--host", "192.168.1.50",
            "--options-file", str(options), "--config", str(config), "--no-restart", "--json"]
    assert cli.main(args) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["created"]
    assert token not in json.dumps(result) and key not in json.dumps(result)
    assert stat.S_IMODE(config.stat().st_mode) == 0o600
    assert load_config(config).device.options["token"] == token
    assert cli.main(args + ["--name", "New name"]) == 0
    assert not json.loads(capsys.readouterr().out)["created"]
    assert len(load_config(config).devices) == 1
    assert load_config(config).device.name == "New name"
    updated, created = setup.add_device(config, name=None, host="192.168.1.51",
                                       backend="midea_lan", options={"device_id": "123"})
    assert not created and updated.options["token"] == token


def test_actual_library_mapping_without_network():
    pytest.importorskip("msmart.device")
    client = p.create_client(device(), p.settings(device()))
    assert (client.id, client.port) == (123456789, 6444)
    p.encode_command(client, Command(mode="fan", fan="mid", swing="both", target_temp=22))
    state = p.decode_state(client)
    assert (state.mode, state.fan, state.swing, state.target_temp) == ("fan", "mid", "both", 22)
    assert set(p.capabilities(client, []).modes) == set(p.MODES)
    p.close_client(client)


async def test_empty_broadcast_falls_back_to_local_hosts(monkeypatch):
    import ipaddress

    calls = []

    async def empty(hosts):
        return []

    async def unicast(hosts):
        calls.extend(hosts)
        return [{"host": "192.168.1.50", "port": 6444,
                 "options": {"device_id": "123", "protocol_version": 3}}]

    monkeypatch.setattr(p, "discover", empty)
    monkeypatch.setattr(p, "discover_unicast", unicast)
    monkeypatch.setattr(setup, "read_arp", lambda: {})
    monkeypatch.setattr(setup, "default_subnets",
                        lambda: [ipaddress.ip_network("192.168.1.0/24")])
    candidates = await setup.discover_midea(configured_ids=["123"])
    assert len(candidates) == 1 and candidates[0].configured
    assert "192.168.1.50" in calls and len(calls) == 254


async def test_fallback_retries_neighbors_and_merges(monkeypatch):
    import ipaddress

    async def empty(hosts):
        return []

    calls = []

    async def unicast(hosts):
        calls.append(list(hosts))
        return [{"host": "192.168.1.50" if len(calls) == 1 else "192.168.1.51", "port": 6444,
                 "options": {"device_id": str(122 + len(calls)), "protocol_version": 3}}]

    monkeypatch.setattr(p, "discover", empty)
    monkeypatch.setattr(p, "discover_unicast", unicast)
    monkeypatch.setattr(setup, "default_subnets",
                        lambda: [ipaddress.ip_network("192.168.1.0/24")])
    monkeypatch.setattr(setup, "read_arp", lambda: {"192.168.1.51": "fake", "203.0.113.1": "fake"})
    candidates = await setup.discover_midea()
    assert {c.host for c in candidates} == {"192.168.1.50", "192.168.1.51"}
    assert calls[1] == ["192.168.1.51"]


def test_plain_setup_asks_for_brand_and_uses_midea(monkeypatch, capsys):
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr(setup_cli, "_ask", lambda *args: "1")
    seen = []

    async def empty(hosts, configured_ids):
        seen.append(hosts)
        return []

    monkeypatch.setattr(setup, "discover_midea", empty)
    assert cli.main(["setup", "--no-restart"]) == 1
    output = capsys.readouterr().out
    assert "Choose your air conditioner" in output and "No Midea air conditioner" in output
    assert "56800" not in output and len(seen) == 1


def test_plain_noninteractive_setup_requires_brand(capsys):
    assert cli.main(["setup", "--no-restart"]) == 1
    assert "choose a brand" in capsys.readouterr().err


@pytest.mark.parametrize("version", [2, 3])
def test_midea_wizard_adds_without_haier_prompts(tmp_path, monkeypatch, capsys, version):
    config = tmp_path / "config.toml"
    credentials = tmp_path / "options.toml"
    credentials.write_text(f'token = "{"01" * 64}"\nkey = "{"02" * 32}"\n')
    answers = iter(["1", "Bedroom", str(credentials)])
    monkeypatch.setattr(setup_cli, "_ask", lambda *args: next(answers))
    candidate = setup.Candidate(host="192.168.1.50", mac=None, state=None, port=6444,
                                backend="midea_lan",
                                options={"device_id": "123", "protocol_version": version})
    monkeypatch.setattr(setup_cli, "_discover_candidates", lambda args: [candidate])
    assert cli.main(["setup", "--backend", "midea_lan", "--config", str(config),
                     "--no-restart"]) == 0
    saved = load_config(config).device
    assert saved.backend == "midea_lan" and saved.options["device_id"] == "123"
    assert saved.port == 6444 and saved.name == "Bedroom"
    output = capsys.readouterr().out
    assert "Haier" not in output and "MAC" not in output


async def test_unicast_discovery_decodes_fake_udp_reply(monkeypatch):
    pytest.importorskip("msmart.discover")
    from msmart.const import DISCOVERY_MSG
    from msmart.discover import Discover

    requests = []

    class FakeDongle(asyncio.DatagramProtocol):
        def connection_made(self, transport):
            self.transport = transport

        def datagram_received(self, data, addr):
            requests.append(data)
            self.transport.sendto(b"fake-midea-info", addr)

    def version(data):
        assert data == b"fake-midea-info"
        return 3

    async def info(host, protocol_version, data):
        return {"device_type": 0xAC, "device_id": 123, "port": 6444}

    monkeypatch.setattr(Discover, "_get_device_version", version)
    monkeypatch.setattr(Discover, "_get_device_info", info)
    transport, _ = await asyncio.get_running_loop().create_datagram_endpoint(
        FakeDongle, local_addr=("127.0.0.1", 0))
    try:
        port = transport.get_extra_info("sockname")[1]
        found = await p.discover_unicast(["127.0.0.1"], timeout=0.05, ports=(port,))
        assert requests == [DISCOVERY_MSG]
        assert found == [{"host": "127.0.0.1", "port": 6444,
                          "options": {"device_id": "123", "protocol_version": 3}}]
    finally:
        transport.close()
