"""Error taxonomy for harbin (sub-spec 04 §5).

Every error raised by harbin is a subclass of :class:`HarbinError`. The
class hierarchy classifies where the error surfaces (user, fleet, internal).
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class HarbinError(Exception):
    """Abstract base for every harbin-raised exception.

    Attributes:
        code: Stable dotted identifier, e.g. ``"fleet.dock.dirty"``.
        message: Single-line user-facing message.
        detail: Optional multi-line elaboration shown on expand.
    """

    code: str = "harbin.unknown"
    message: str = ""
    detail: str | None = None

    def __post_init__(self) -> None:
        super().__init__(self.message or self.code)

    def __str__(self) -> str:
        return self.message or self.code


# ────────────────────────────── user-facing ───────────────────────────────


@dataclass
class UserError(HarbinError):
    """A mistake the user can fix immediately (bad command, bad YAML at edge)."""

    code: str = "user.error"


@dataclass
class ParseError(UserError):
    """REPL parse failure, YAML parse failure at a user boundary."""

    code: str = "user.parse"


@dataclass
class ValidationError(UserError):
    """pydantic-derived schema validation problem."""

    code: str = "user.validation"
    errors: list[dict[str, object]] = field(default_factory=list)


# ─────────────────────────────── fleet-scope ──────────────────────────────


@dataclass
class FleetError(HarbinError):
    """A per-fleet failure that must NOT crash harbin."""

    code: str = "fleet.error"
    fleet: str | None = None


@dataclass
class DockError(FleetError):
    """git clone/fetch/push/status problem."""

    code: str = "fleet.dock"


@dataclass
class ScheduleError(FleetError):
    """Cron parse / schedule.yaml problem detected post-load."""

    code: str = "fleet.schedule"


@dataclass
class RunnerError(FleetError):
    """Subprocess spawn / IO failure in the agent runner."""

    code: str = "fleet.runner"


# ──────────────────────────────── internal ────────────────────────────────


@dataclass
class InternalError(HarbinError):
    """A bug in harbin itself — surfaces as a modal with traceback."""

    code: str = "internal.unhandled"
