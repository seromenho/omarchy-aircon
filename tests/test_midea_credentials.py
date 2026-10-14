import asyncio
import builtins
import hashlib
import io
import json
import logging
import stat
import sys
from types import SimpleNamespace

import pytest
from fake_midea import FakeMidea

from aircon import cli, setup, setup_cli
from aircon.backends.midea_lan import credentials as c
from aircon.backends.midea_lan import protocol as p
from aircon.config import default_config_path, load_config

TOKEN, KEY = "01" * 64, "02" * 32
EMAIL, PASSWORD = "owner@example.invalid", " test-$#password' "
OPTIONS = {"device_id": "123456789", "protocol_version": 3, "token": TOKEN, "key": KEY}


class CloudError(Exception):
    pass


class ApiError(CloudError):
    def __init__(self, code):
        self.code = code
        super().__init__(f"private cloud data: {PASSWORD} {TOKEN} {KEY}")


@pytest.fixture
def cloud(monkeypatch):
    context = SimpleNamespace(events=[], replies=[], clients=[], login_error=None,
                              auth_error=False, delay=0)

    class SmartHomeCloud:
        def __init__(self, **kwargs):
            context.events.append(("account", kwargs))

        async def login(self):
            logging.getLogger("msmart.cloud").debug("sensitive %s", PASSWORD)
            context.events.append(("login",))
            await asyncio.sleep(context.delay)
            if context.login_error:
                raise context.login_error

        async def get_token(self, udpid, appliance_id=None):
            context.events.append(("token", udpid, appliance_id))
            if context.replies:
                reply = context.replies.pop(0)
                if isinstance(reply, Exception):
                    raise reply
                return reply
            return TOKEN, KEY

    def create_client(device, settings):
        fake = FakeMidea()
        fake.auth_error = context.auth_error
        context.clients.append((fake, device.host, settings))
        return fake

    monkeypatch.setitem(sys.modules, "msmart.cloud", SimpleNamespace(
        ApiError=ApiError, CloudError=CloudError, SmartHomeCloud=SmartHomeCloud))
    monkeypatch.setitem(sys.modules, "msmart.lan", SimpleNamespace(
        AuthenticationError=ConnectionError,
        Security=SimpleNamespace(udpid=lambda value: hashlib.sha256(value).digest()[:16])))
    monkeypatch.setattr(p, "create_client", create_client)
    return context


async def test_smarthome_uses_own_account_and_verifies_without_changing_settings(cloud, caplog):
    caplog.set_level(logging.DEBUG)
    assert await c.fetch_credentials("192.168.1.50", "123456789", EMAIL, PASSWORD) == OPTIONS
    assert cloud.events[0] == ("account", {"account": EMAIL, "password": PASSWORD})
    assert cloud.events[1] == ("login",)
    assert cloud.events[2][0] == "token" and cloud.events[2][2] == 123456789
    fake, host, settings = cloud.clients[0]
    assert host == "192.168.1.50" and settings.version == 3
    assert fake.authenticated == [(TOKEN, KEY)]
    assert fake.closed == 1 and not fake.requests
    assert PASSWORD not in caplog.text


async def test_cloud_rejection_tries_other_byte_order(cloud):
    cloud.replies = [ApiError(9999), (TOKEN, KEY)]
    assert await c.fetch_credentials("192.168.1.50", "123456789", EMAIL, PASSWORD) == OPTIONS
    requests = [event for event in cloud.events if event[0] == "token"]
    assert len(requests) == 2 and requests[0][1] != requests[1][1]


async def test_authentication_failure_never_returns_credentials(cloud):
    cloud.auth_error = True
    with pytest.raises(ValueError, match="Could not verify") as error:
        await c.fetch_credentials("192.168.1.50", "123456789", EMAIL, PASSWORD)
    assert TOKEN not in str(error.value) and KEY not in str(error.value)
    assert len(cloud.clients) == 2
    assert all(fake.closed == 1 and not fake.requests for fake, *_ in cloud.clients)


@pytest.mark.parametrize("failure", [ApiError(3101), CloudError(PASSWORD), ValueError(PASSWORD)])
async def test_login_errors_are_redacted_and_logging_is_restored(cloud, failure):
    cloud.login_error = failure
    logger = logging.getLogger("msmart.cloud")
    old_level = logger.level
    with pytest.raises(ValueError) as error:
        await c.fetch_credentials("192.168.1.50", "123456789", EMAIL, PASSWORD)
    assert PASSWORD not in str(error.value) and TOKEN not in str(error.value)
    assert logger.level == old_level
    assert not cloud.clients


async def test_bounded_timeout(cloud):
    cloud.delay = 1
    with pytest.raises(ValueError, match="timed out"):
        await c.fetch_credentials("192.168.1.50", "123456789", EMAIL, PASSWORD, timeout=0.01)


async def test_timeout_closes_authentication_connection(cloud, monkeypatch):
    client = FakeMidea()

    async def stall(*args):
        await asyncio.sleep(1)

    client.authenticate = stall
    monkeypatch.setattr(p, "create_client", lambda *_: client)
    with pytest.raises(ValueError, match="timed out"):
        await c.fetch_credentials("192.168.1.50", "123456789", EMAIL, PASSWORD, timeout=0.01)
    assert client.closed == 1 and not client.requests


async def test_invalid_token_is_not_authenticated(cloud):
    cloud.replies = [("not-hex", KEY), (TOKEN, "too-short")]
    with pytest.raises(ValueError, match="Could not verify"):
        await c.fetch_credentials("192.168.1.50", "123456789", EMAIL, PASSWORD)
    assert not cloud.clients


async def test_missing_optional_library_has_actionable_error(monkeypatch):
    original = builtins.__import__

    def missing(name, *args, **kwargs):
        if name.startswith("msmart"):
            raise ImportError("not installed")
        return original(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", missing)
    with pytest.raises(ValueError, match="requires msmart-ng"):
        await c.fetch_credentials("192.168.1.50", "123456789", EMAIL, PASSWORD)


def apply_args():
    return ["setup", "apply", "--backend=midea_lan", "--host=192.168.1.50",
            "--device-id=123456789", "--protocol-version=3", "--name=Living room",
            "--smarthome", "--credentials-stdin", "--no-restart", "--json"]


def test_cli_stdin_to_private_config_without_echoing_secrets(cloud, monkeypatch, capsys):
    payload = json.dumps({"email": EMAIL, "password": PASSWORD})
    monkeypatch.setattr(sys, "stdin", io.StringIO(payload))
    assert cli.main(apply_args()) == 0
    captured = capsys.readouterr()
    result = json.loads(captured.out)
    assert result["ok"] and result["created"]
    saved = load_config(default_config_path()).device
    assert saved.options == OPTIONS and saved.name == "Living room"
    assert stat.S_IMODE(default_config_path().stat().st_mode) == 0o600
    for secret in (EMAIL, PASSWORD, TOKEN, KEY):
        assert secret not in captured.out + captured.err
    config_text = default_config_path().read_text()
    assert EMAIL not in config_text and PASSWORD not in config_text


def test_failed_login_preserves_existing_config(cloud, monkeypatch, capsys):
    setup.add_device(default_config_path(), name="Existing", host="192.168.1.50",
                     backend="midea_lan", options=OPTIONS)
    before = default_config_path().read_bytes()
    cloud.login_error = ApiError(3101)
    payload = json.dumps({"email": EMAIL, "password": PASSWORD})
    monkeypatch.setattr(sys, "stdin", io.StringIO(payload))
    assert cli.main(apply_args()) == 1
    captured = capsys.readouterr()
    assert "3101" in captured.err and PASSWORD not in captured.err
    assert default_config_path().read_bytes() == before


@pytest.mark.parametrize("payload", [
    "not json", "[]", '{}', '"password"',
    json.dumps({"email": EMAIL, "password": 123}),
    json.dumps({"email": EMAIL, "password": ""}),
    json.dumps({"email": EMAIL, "password": PASSWORD, "extra": "ignored?"}),
    "x" * 16385,
])
def test_invalid_stdin_fails_before_cloud_or_saving(cloud, monkeypatch, capsys, payload):
    monkeypatch.setattr(sys, "stdin", io.StringIO(payload))
    assert cli.main(apply_args()) == 1
    capsys.readouterr()
    assert not cloud.events and not default_config_path().exists()


def test_interactive_password_prompt_preserves_special_characters(monkeypatch):
    monkeypatch.setattr(sys, "stdin", SimpleNamespace(isatty=lambda: True))
    monkeypatch.setattr("builtins.input", lambda _: " " + EMAIL + " ")
    monkeypatch.setattr(setup_cli.getpass, "getpass", lambda _: PASSWORD)
    assert setup_cli._smarthome_login(False) == (EMAIL, PASSWORD)


def test_interactive_wizard_signs_in_and_adds_device(cloud, monkeypatch, capsys):
    candidate = setup.Candidate(host="192.168.1.50", mac=None, state=None, port=6444,
                                backend="midea_lan",
                                options={"device_id": "123456789", "protocol_version": 3})
    answers = iter(["1", "Bedroom", "1", EMAIL])
    monkeypatch.setattr(setup_cli, "_discover_candidates", lambda _: [candidate])
    monkeypatch.setattr(setup_cli, "_ask", lambda *args: next(answers))
    monkeypatch.setattr(setup_cli.getpass, "getpass", lambda _: PASSWORD)
    monkeypatch.setattr(sys, "stdin", SimpleNamespace(isatty=lambda: True))
    assert cli.main(["setup", "--backend=midea_lan", "--no-restart"]) == 0
    saved = load_config(default_config_path()).device
    assert saved.name == "Bedroom" and saved.options == OPTIONS
    assert PASSWORD not in capsys.readouterr().out


@pytest.mark.parametrize("flags", [
    ["--credentials-stdin"],
    ["--smarthome", "--backend=haier_smartair2"],
    ["--smarthome", "--protocol-version=2"],
])
def test_invalid_login_flags_fail_before_reading_secrets(cloud, capsys, flags):
    args = ["setup", "apply", "--host=192.168.1.50", "--backend=midea_lan",
            "--device-id=123456789", "--no-restart", *flags]
    assert cli.main(args) == 1
    assert not cloud.events and not default_config_path().exists()
    capsys.readouterr()


async def test_actual_library_sends_appliance_id_without_network(monkeypatch):
    module = pytest.importorskip("msmart.cloud")
    cloud = module.SmartHomeCloud(account=EMAIL, password=PASSWORD)
    requests = []

    async def request(endpoint, body):
        requests.append((endpoint, body))
        return {"tokenlist": [{"udpId": "example", "token": TOKEN, "key": KEY}]}

    monkeypatch.setattr(cloud, "_api_request", request)
    assert await cloud.get_token("example", 123456789) == (TOKEN, KEY)
    assert requests[0][1]["applianceCodes"] == "123456789"
