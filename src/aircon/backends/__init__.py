"""Backend registry. Adding a brand means adding a module and one entry here."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from aircon.backends.base import Backend
    from aircon.config import DeviceConfig


def _haier_smartair2(device: DeviceConfig) -> Backend:
    from aircon.backends.haier_smartair2.backend import HaierSmartAir2Backend

    return HaierSmartAir2Backend(device)


BACKENDS = {
    "haier_smartair2": _haier_smartair2,
}


def create_backend(device: DeviceConfig) -> Backend:
    factory = BACKENDS.get(device.backend)
    if factory is None:
        available = ", ".join(sorted(BACKENDS))
        raise ValueError(f"unknown backend {device.backend!r} (available: {available})")
    return factory(device)
