"""Offline fake of the msmart-ng client; the optional library owns the wire protocol."""

import asyncio
from enum import IntEnum
from types import SimpleNamespace


class FakeMidea:
    class OperationalMode(IntEnum):
        AUTO = 1
        COOL = 2
        DRY = 3
        HEAT = 4
        FAN_ONLY = 5

    class FanSpeed(IntEnum):
        LOW = 40
        MEDIUM = 60
        HIGH = 80
        AUTO = 102

    class SwingMode(IntEnum):
        OFF = 0
        VERTICAL = 12
        HORIZONTAL = 3
        BOTH = 15

    def __init__(self):
        self.saved = {
            "power_state": True, "operational_mode": self.OperationalMode.COOL,
            "fan_speed": self.FanSpeed.LOW, "swing_mode": self.SwingMode.OFF,
            "target_temperature": 24.5, "indoor_temperature": 26.5, "sleep": False,
            "purifier": False, "error_code": 0, "eco": True, "fahrenheit": True,
        }
        self.__dict__.update(self.saved)
        self.supported_operation_modes = list(self.OperationalMode)
        self.supported_fan_speeds = list(self.FanSpeed)
        self.supported_swing_modes = list(self.SwingMode)
        self.min_target_temperature = 16
        self.max_target_temperature = 30
        self.supports_purifier = True
        self.online = self.supported = False
        self.delay = 0
        self.unreachable = False
        self.ignore_apply = False
        self.auth_error = False
        self.authenticated = []
        self.requests = []
        self.closed = 0
        self._lan = SimpleNamespace(_disconnect=self.disconnect)

    def disconnect(self):
        self.closed += 1

    async def authenticate(self, token, key):
        self.authenticated.append((token, key))
        if self.auth_error:
            raise ValueError(f"sensitive authentication material: {token} {key}")

    async def get_capabilities(self):
        self.requests.append("capabilities")

    async def refresh(self):
        self.requests.append("refresh")
        await asyncio.sleep(self.delay)
        self.online = not self.unreachable
        if self.online:
            self.supported = True
            self.__dict__.update(self.saved)

    async def apply(self):
        self.requests.append("apply")
        self.online = not self.ignore_apply
        if self.online:
            self.saved = {k: getattr(self, k) for k in self.saved}
