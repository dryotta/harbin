"""Tests for harbin.errors hierarchy."""

from __future__ import annotations

from harbin.errors import (
    DockError,
    FleetError,
    HarbinError,
    InternalError,
    RunnerError,
    ScheduleError,
    UserError,
    ValidationError,
)


def test_subclassing() -> None:
    assert issubclass(UserError, HarbinError)
    assert issubclass(ValidationError, UserError)
    assert issubclass(FleetError, HarbinError)
    assert issubclass(DockError, FleetError)
    assert issubclass(ScheduleError, FleetError)
    assert issubclass(RunnerError, FleetError)
    assert issubclass(InternalError, HarbinError)


def test_message_str() -> None:
    err = DockError(code="fleet.dock.test", message="boom", fleet="news")
    assert str(err) == "boom"
    assert err.code == "fleet.dock.test"
    assert err.fleet == "news"
