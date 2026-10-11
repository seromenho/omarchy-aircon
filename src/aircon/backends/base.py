"""The interface every backend implements. The daemon only talks to backends through it."""

from __future__ import annotations

from collections.abc import Callable
from typing import Protocol

from aircon.model import Capabilities, Command, State


class Backend(Protocol):
    name: str
    capabilities: Capabilities
    connected: bool
    state: State
    last_error: str | None

    async def start(self) -> None:
        """Begin connecting in the background; must return without waiting for the device."""

    async def close(self) -> None: ...

    async def refresh(self) -> State:
        """Ask the device for its current state."""

    async def apply(self, cmd: Command) -> State:
        """Apply a validated command and return the state reported afterwards."""

    def on_state(self, callback: Callable[[State], None]) -> None: ...

    def on_connection(self, callback: Callable[[bool], None]) -> None: ...
