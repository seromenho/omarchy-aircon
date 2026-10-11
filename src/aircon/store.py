"""Small JSON persistence for daemon state that must survive restarts.

The file holds one section per device: {"devices": {"<id>": {...}}}. Files written before
multi-device support kept a single device's keys at the top level; the device created with
`adopt_legacy=True` takes them over and they disappear on the next save.
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path

log = logging.getLogger(__name__)

LEGACY_KEYS = ("timers", "sleep_curve", "filter_hours")


def default_state_path() -> Path:
    base = os.environ.get("XDG_STATE_HOME") or str(Path.home() / ".local" / "state")
    return Path(base) / "aircon" / "state.json"


class Store:
    def __init__(self, path: Path, section: str | None = None,
                 adopt_legacy: bool = False) -> None:
        self.path = path
        self.section = section
        self.adopt_legacy = adopt_legacy

    def _read_all(self) -> dict:
        try:
            data = json.loads(self.path.read_text())
        except FileNotFoundError:
            return {}
        except (OSError, json.JSONDecodeError) as exc:
            log.warning("ignoring unreadable state file %s: %s", self.path, exc)
            return {}
        return data if isinstance(data, dict) else {}

    def load(self) -> dict:
        data = self._read_all()
        if self.section is None:
            return data
        devices = data.get("devices")
        if isinstance(devices, dict) and isinstance(devices.get(self.section), dict):
            return devices[self.section]
        if self.adopt_legacy:
            return {key: data[key] for key in LEGACY_KEYS if key in data}
        return {}

    def save(self, payload: dict) -> None:
        if self.section is None:
            data = payload
        else:
            current = self._read_all()
            devices = current.get("devices")
            devices = dict(devices) if isinstance(devices, dict) else {}
            devices[self.section] = payload
            data = {"devices": devices}
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, indent=2))
        os.replace(tmp, self.path)
