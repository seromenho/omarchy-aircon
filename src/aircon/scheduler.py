"""Timers, the sleep curve and the filter-hours counter. Backend-independent."""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from datetime import datetime, timedelta

from aircon.model import Command, State
from aircon.store import Store

Clock = Callable[[], datetime]
Apply = Callable[[Command], Awaitable[object]]

log = logging.getLogger(__name__)

DEVICE_ERRORS = (TimeoutError, ConnectionError)
TIMER_ACTIONS = ("on", "off")
# How long a due timer keeps retrying while the unit does not answer.
TIMER_GRACE = timedelta(minutes=15)
# (minutes since start, temperature offset); cooling moves up, heating moves down.
SLEEP_CURVE_STEPS = ((0, 0), (60, 1), (120, 2))
MAX_SLEEP_HOURS = 12


def local_now() -> datetime:
    return datetime.now().astimezone()


def _valid_curve(curve: object) -> bool:
    """Ignore a hand-edited or old-format curve instead of crashing on it later."""
    if not isinstance(curve, dict):
        return False
    try:
        datetime.fromisoformat(curve["started"])
        return (isinstance(curve["base_temp"], int) and curve["direction"] in (-1, 0, 1)
                and isinstance(curve["hours"], int) and isinstance(curve["off_at_end"], bool)
                and curve["step"] in range(len(SLEEP_CURVE_STEPS)))
    except (KeyError, TypeError, ValueError):
        return False


class Scheduler:
    def __init__(
        self,
        store: Store,
        apply: Apply,
        threshold_hours: int = 300,
        clock: Clock = local_now,
    ) -> None:
        self._store = store
        self._apply = apply
        self._threshold = threshold_hours
        self._clock = clock

        data = store.load()
        self._timers: dict[str, datetime] = {}
        for action, when in (data.get("timers") or {}).items():
            if action in TIMER_ACTIONS:
                try:
                    self._timers[action] = datetime.fromisoformat(when)
                except (TypeError, ValueError):
                    pass
        curve = data.get("sleep_curve")
        self._curve: dict | None = curve if _valid_curve(curve) else None
        self._filter_hours = float(data.get("filter_hours") or 0.0)
        self._last_filter_tick: datetime | None = None

    # -- timers ---------------------------------------------------------------------

    def set_timer(self, action: str, at: datetime) -> None:
        if action not in TIMER_ACTIONS:
            raise ValueError(f"timer action must be one of: {', '.join(TIMER_ACTIONS)}")
        self._timers[action] = at
        self._persist()

    def cancel_timer(self, action: str) -> None:
        self._timers.pop(action, None)
        self._persist()

    def timers(self) -> dict[str, str]:
        return {action: when.isoformat() for action, when in sorted(self._timers.items())}

    # -- sleep curve ----------------------------------------------------------------

    def start_sleep_curve(self, state: State, hours: int = 8, off_at_end: bool = False) -> None:
        if not 1 <= hours <= MAX_SLEEP_HOURS:
            raise ValueError(f"hours must be between 1 and {MAX_SLEEP_HOURS}")
        direction = {"cool": 1, "heat": -1}.get(state.mode, 0)
        self._curve = {
            "started": self._clock().isoformat(),
            "base_temp": state.target_temp,
            "direction": direction,
            "hours": hours,
            "off_at_end": off_at_end,
            "step": 0,
        }
        self._persist()

    def cancel_sleep_curve(self) -> None:
        self._curve = None
        self._persist()

    def sleep_curve(self) -> dict | None:
        if self._curve is None:
            return None
        started = datetime.fromisoformat(self._curve["started"])
        return {
            "started": self._curve["started"],
            "ends_at": (started + timedelta(hours=self._curve["hours"])).isoformat(),
            "hours": self._curve["hours"],
            "off_at_end": self._curve["off_at_end"],
            "step": self._curve["step"],
            "steps": len(SLEEP_CURVE_STEPS),
            "target": self._curve_target(),
        }

    def note_manual_temp(self, temp: int) -> None:
        """A user-chosen temperature that differs from the curve's target cancels the curve."""
        if self._curve is not None and temp != self._curve_target():
            self.cancel_sleep_curve()

    def _curve_target(self) -> int:
        assert self._curve is not None
        offset = SLEEP_CURVE_STEPS[self._curve["step"]][1] * self._curve["direction"]
        return self._curve["base_temp"] + offset

    # -- filter ---------------------------------------------------------------------

    def filter_tick(self, power: bool) -> None:
        now = self._clock()
        if power and self._last_filter_tick is not None:
            self._filter_hours += (now - self._last_filter_tick).total_seconds() / 3600
            self._persist()
        self._last_filter_tick = now if power else None

    def filter_status(self) -> dict:
        hours = round(self._filter_hours, 1)
        return {"hours": hours, "threshold": self._threshold, "due": hours >= self._threshold}

    def filter_reset(self) -> None:
        self._filter_hours = 0.0
        self._persist()

    # -- clock tick -----------------------------------------------------------------

    async def tick(self, power: bool = True) -> None:
        """Run what is due. `power` is the unit's current state: a unit switched off by a
        timer, the remote or an app ends the sleep curve instead of being turned back on."""
        now = self._clock()
        for action, when in list(self._timers.items()):
            if when > now:
                continue
            try:
                await self._apply(Command(power=action == "on"))
            except DEVICE_ERRORS:
                if now - when < TIMER_GRACE:
                    continue  # the unit may be reconnecting; try again on the next tick
                log.warning("giving up on the power-%s timer: the unit did not answer", action)
            else:
                power = action == "on"
            self._timers.pop(action)
            self._persist()

        if self._curve is None:
            return
        if not power:
            self.cancel_sleep_curve()
            return
        started = datetime.fromisoformat(self._curve["started"])
        elapsed_min = (now - started).total_seconds() / 60
        if elapsed_min >= self._curve["hours"] * 60:
            end = Command(power=False) if self._curve["off_at_end"] else Command(sleep=False)
            try:
                await self._apply(end)
            except DEVICE_ERRORS:
                return  # try again on the next tick
            self.cancel_sleep_curve()
            return
        step = max(i for i, (minute, _) in enumerate(SLEEP_CURVE_STEPS) if elapsed_min >= minute)
        if step > self._curve["step"]:
            if self._curve["direction"]:
                offset = SLEEP_CURVE_STEPS[step][1] * self._curve["direction"]
                try:
                    await self._apply(Command(target_temp=self._curve["base_temp"] + offset))
                except DEVICE_ERRORS:
                    return  # try again on the next tick
            self._curve["step"] = step
            self._persist()

    def _persist(self) -> None:
        self._store.save({
            "timers": self.timers(),
            "sleep_curve": self._curve,
            "filter_hours": round(self._filter_hours, 4),
        })
