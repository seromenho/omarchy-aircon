from datetime import UTC, datetime, timedelta

import pytest

from aircon.model import Command, State
from aircon.scheduler import Scheduler
from aircon.store import Store


class FakeClock:
    def __init__(self) -> None:
        self.now = datetime(2026, 10, 11, 22, 0, tzinfo=UTC)

    def __call__(self) -> datetime:
        return self.now

    def advance(self, **kwargs) -> None:
        self.now += timedelta(**kwargs)


@pytest.fixture
def clock():
    return FakeClock()


@pytest.fixture
def applied():
    return []


@pytest.fixture
def make(tmp_path, clock, applied):
    async def apply(cmd: Command) -> None:
        applied.append(cmd)

    def factory(threshold_hours: int = 300) -> Scheduler:
        return Scheduler(Store(tmp_path / "state.json"), apply, threshold_hours, clock=clock)

    return factory


async def test_timer_fires_once_and_is_removed(make, clock, applied):
    sched = make()
    sched.set_timer("off", clock.now + timedelta(minutes=90))
    await sched.tick()
    assert applied == []
    clock.advance(minutes=90)
    await sched.tick()
    await sched.tick()
    assert applied == [Command(power=False)]
    assert sched.timers() == {}


async def test_cancel_timer(make, clock, applied):
    sched = make()
    sched.set_timer("on", clock.now + timedelta(minutes=5))
    sched.cancel_timer("on")
    clock.advance(minutes=10)
    await sched.tick()
    assert applied == []


def test_rejects_unknown_timer_action(make, clock):
    with pytest.raises(ValueError):
        make().set_timer("explode", clock.now)


async def test_timers_survive_restart(make, clock, applied):
    make().set_timer("on", clock.now + timedelta(hours=1))
    restarted = make()
    assert "on" in restarted.timers()
    clock.advance(hours=1)
    await restarted.tick()
    assert applied == [Command(power=True)]


async def test_sleep_curve_in_cool_raises_temperature(make, clock, applied):
    sched = make()
    sched.start_sleep_curve(State(power=True, mode="cool", target_temp=24), hours=8)
    await sched.tick()
    assert applied == []
    clock.advance(minutes=60)
    await sched.tick()
    clock.advance(minutes=60)
    await sched.tick()
    assert applied == [Command(target_temp=25), Command(target_temp=26)]
    assert sched.sleep_curve()["target"] == 26


async def test_sleep_curve_in_heat_lowers_temperature(make, clock, applied):
    sched = make()
    sched.start_sleep_curve(State(power=True, mode="heat", target_temp=22))
    clock.advance(minutes=125)
    await sched.tick()
    assert applied == [Command(target_temp=20)]


async def test_sleep_curve_powers_off_at_end_when_requested(make, clock, applied):
    sched = make()
    sched.start_sleep_curve(State(power=True, mode="cool", target_temp=24), hours=2,
                            off_at_end=True)
    clock.advance(hours=2)
    await sched.tick()
    assert applied[-1] == Command(power=False)
    assert sched.sleep_curve() is None


async def test_sleep_curve_survives_restart(make, clock, applied):
    make().start_sleep_curve(State(power=True, mode="cool", target_temp=24))
    restarted = make()
    clock.advance(minutes=61)
    await restarted.tick()
    assert applied == [Command(target_temp=25)]


async def test_manual_temperature_change_cancels_curve(make, clock):
    sched = make()
    sched.start_sleep_curve(State(power=True, mode="cool", target_temp=24))
    sched.note_manual_temp(24)
    assert sched.sleep_curve() is not None
    sched.note_manual_temp(21)
    assert sched.sleep_curve() is None


def test_sleep_curve_rejects_bad_duration(make):
    with pytest.raises(ValueError):
        make().start_sleep_curve(State(mode="cool"), hours=0)


def test_filter_counts_only_while_on(make, clock):
    sched = make(threshold_hours=2)
    sched.filter_tick(True)
    clock.advance(hours=1)
    sched.filter_tick(True)
    clock.advance(hours=5)
    sched.filter_tick(False)
    assert sched.filter_status() == {"hours": 1.0, "threshold": 2, "due": False}
    clock.advance(hours=1)
    sched.filter_tick(True)
    clock.advance(hours=1)
    sched.filter_tick(True)
    assert sched.filter_status()["due"] is True
    sched.filter_reset()
    assert sched.filter_status()["hours"] == 0.0


def test_filter_hours_survive_restart(make, clock):
    sched = make()
    sched.filter_tick(True)
    clock.advance(hours=3)
    sched.filter_tick(True)
    assert make().filter_status()["hours"] == 3.0


def test_corrupt_store_starts_empty(tmp_path, clock):
    path = tmp_path / "state.json"
    path.write_text("{not json")

    async def apply(cmd):
        pass

    sched = Scheduler(Store(path), apply, clock=clock)
    assert sched.timers() == {}
