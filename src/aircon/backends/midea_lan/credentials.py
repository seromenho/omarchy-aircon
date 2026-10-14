"""Explicit, one-time SmartHome sign-in; never used by discovery or the daemon."""

from __future__ import annotations

import asyncio
import contextlib
import inspect
import logging

from aircon.config import DeviceConfig

from . import protocol as p


class CredentialError(ValueError):
    """An account error that is safe to show in the panel."""


async def fetch_credentials(host: str, device_id: str, email: str, password: str, *,
                            port: int = 6444, timeout: float = 120) -> dict:
    """Return local credentials only after a successful LAN authentication.

    Account credentials live only in this setup process. No AC settings are sent.
    """
    if (not isinstance(email, str) or not email.strip() or len(email) > 4096
            or not isinstance(password, str) or not password or len(password) > 4096):
        raise ValueError("Enter your SmartHome email and password.")
    device = DeviceConfig(name="Midea setup", backend="midea_lan", host=host, port=port,
                          options={"device_id": device_id})
    validated = p.settings(device)
    try:
        from msmart.cloud import ApiError, CloudError, SmartHomeCloud
        from msmart.lan import AuthenticationError, Security
    except ImportError:
        raise ValueError(p.DEPENDENCY_HELP) from None

    # Older libraries lack the appliance ID argument required by SmartHome.
    if "appliance_id" not in inspect.signature(SmartHomeCloud.get_token).parameters:
        raise ValueError("Update Midea support: run install.sh --midea from the plugin folder.")

    # Third-party debug logs include passwords, session tokens and authentication packets.
    loggers = [logging.getLogger(name) for name in
               ("msmart", "msmart.cloud", "msmart.lan", "msmart.base_device")]
    levels = [logger.level for logger in loggers]
    for logger in loggers:
        logger.setLevel(logging.CRITICAL + 1)
    try:
        async with asyncio.timeout(timeout):
            cloud = SmartHomeCloud(account=email.strip(), password=password)
            try:
                await cloud.login()
            except ApiError as exc:
                raise CredentialError(
                    f"SmartHome sign-in failed{_code(exc)}. Check your email/password and app "
                    "service region. If you use email codes, set a password in SmartHome first."
                ) from None
            except CloudError:
                raise CredentialError(
                    "Could not reach SmartHome. Check your internet connection.") from None

            codes = set()
            for endian in ("little", "big"):
                client = None
                try:
                    udpid = Security.udpid(validated.device_id.to_bytes(6, endian)).hex()
                    token, key = await cloud.get_token(udpid, validated.device_id)
                    options = {"device_id": str(validated.device_id), "protocol_version": 3,
                               "token": token, "key": key}
                    device.options = options
                    settings = p.settings(device)
                    client = p.create_client(device, settings)
                    await client.authenticate(settings.token, settings.key)
                    return options
                except ApiError as exc:
                    codes.add(_code(exc))
                except (CloudError, AuthenticationError, TimeoutError, ValueError):
                    pass
                finally:
                    if client is not None:
                        with contextlib.suppress(Exception):
                            p.close_client(client)
            detail = "; ".join(sorted(c for c in codes if c))
            raise CredentialError(
                f"Could not verify a SmartHome token/key{detail}. Add this AC to your fresh "
                "SmartHome account first; migrated accounts may fail. Try again when it is online."
            )
    except TimeoutError:
        raise CredentialError(
            "SmartHome setup timed out. Check your internet connection and AC.") from None
    except CredentialError:
        raise
    except Exception:
        # Never forward third-party exception text containing secrets to the panel or logs.
        raise CredentialError(
            "SmartHome setup failed. Check your account and AC, then try again.") from None
    finally:
        for logger, level in zip(loggers, levels, strict=True):
            logger.setLevel(level)


def _code(error: Exception) -> str:
    code = getattr(error, "code", None)
    return f" (API code {code})" if isinstance(code, int) and not isinstance(code, bool) else ""
