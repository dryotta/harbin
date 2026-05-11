"""Config / fleet / schedule YAML loaders with friendly error mapping.

Sub-spec 03 §4: error rows like ``config.yaml:12:5  ui.theme: not a registered theme``.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml
from pydantic import ValidationError as _PydanticValidationError

from harbin.config.models import Config
from harbin.errors import ParseError, ValidationError
from harbin.fleet.models import FleetConfig, ScheduleConfig
from harbin.paths import atomic_write_text

DEFAULT_CONFIG_TEMPLATE = """\
# harbin user config — see doc/design/03-configuration.md for full schema.
ui:
  theme: harbor
  log_verbosity: info

timezone: system

scheduler:
  tick_seconds: 5

agent_runner:
  agent_cli:
    command: ["copilot"]
    mode: stdin
    placeholder: "${PROMPT}"
  concurrency:
    per_dock: 1
    global_cap: 4
  kill_grace_seconds: 10

artifacts:
  root: null
  retention: 30d
  sweep_cron: "0 4 * * *"

web:
  port: 8080
  host: "127.0.0.1"
  autostart: false

tunnels:
  devtunnel_path: "devtunnel"
  tunnel_id: null
  allow_anonymous: false

keybindings: {}
"""


_ENV_OVERRIDES = {
    "HARBIN_UI_THEME": ("ui", "theme", str),
    "HARBIN_TIMEZONE": ("timezone", None, str),
    "HARBIN_WEB_PORT": ("web", "port", int),
    "HARBIN_WEB_HOST": ("web", "host", str),
    "HARBIN_LOG_VERBOSITY": ("ui", "log_verbosity", str),
}


@dataclass(frozen=True)
class LoadError:
    file: str
    line: int
    col: int
    field: str
    message: str

    def __str__(self) -> str:
        return f"{self.file}:{self.line}:{self.col}  {self.field}: {self.message}"


def _safe_load(path: Path) -> Any:
    if not path.exists():
        return None
    try:
        return yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as e:
        mark = getattr(e, "problem_mark", None)
        line = (mark.line + 1) if mark else 0
        col = (mark.column + 1) if mark else 0
        problem = e.problem if hasattr(e, "problem") else str(e)
        raise ParseError(
            code="user.parse",
            message=f"YAML parse error at {path.name}:{line}:{col}: {problem}",
            detail=str(e),
        ) from e


def _resolve_loc(data: Any, loc: tuple[Any, ...]) -> tuple[int, int]:
    """Best-effort (line,col) for a pydantic error location. 0,0 if unknown."""
    # Without ruamel.yaml we can't easily map deep field paths to line/col.
    # The path itself is shown — line/col is best-effort, defaults to 0,0.
    return (0, 0)


def _apply_env_overrides(data: dict[str, Any]) -> dict[str, Any]:
    for env_key, (top, sub, caster) in _ENV_OVERRIDES.items():
        raw = os.environ.get(env_key)
        if raw is None:
            continue
        try:
            value: Any = caster(raw)
        except ValueError as e:
            raise ValidationError(
                code="user.validation",
                message=f"env {env_key}={raw!r}: {e}",
            ) from e
        if sub is None:
            data[top] = value
        else:
            section = data.setdefault(top, {})
            if not isinstance(section, dict):
                section = {}
                data[top] = section
            section[sub] = value
    return data


def _format_validation_errors(file: str, exc: _PydanticValidationError, raw: Any) -> str:
    lines: list[str] = []
    for err in exc.errors():
        loc = err["loc"]
        field_path = ".".join(str(x) for x in loc)
        msg = err["msg"]
        line, col = _resolve_loc(raw, loc)
        lines.append(f"{file}:{line}:{col}  {field_path}: {msg}")
    return "\n".join(lines)


def load_config(path: Path, *, write_default_if_missing: bool = True) -> Config:
    """Load ``config.yaml`` with env overrides. Writes defaults if missing."""
    if not path.exists():
        if write_default_if_missing:
            atomic_write_text(path, DEFAULT_CONFIG_TEMPLATE)
        return _validate_config(_apply_env_overrides({}), path)
    raw = _safe_load(path)
    if not isinstance(raw, dict):
        raw = {}
    raw = _apply_env_overrides(raw)
    return _validate_config(raw, path)


def _validate_config(raw: dict[str, Any], path: Path) -> Config:
    try:
        return Config.model_validate(raw)
    except _PydanticValidationError as exc:
        raise ValidationError(
            code="user.validation",
            message=_format_validation_errors(path.name, exc, raw),
            errors=[dict(e) for e in exc.errors()],
        ) from exc


def load_fleet(path: Path) -> FleetConfig:
    raw = _safe_load(path)
    if not isinstance(raw, dict):
        raise ValidationError(
            code="user.validation",
            message=f"{path.name}: fleet.yaml must be a mapping (got {type(raw).__name__})",
        )
    try:
        return FleetConfig.model_validate(raw)
    except _PydanticValidationError as exc:
        raise ValidationError(
            code="user.validation",
            message=_format_validation_errors(path.name, exc, raw),
            errors=[dict(e) for e in exc.errors()],
        ) from exc


def load_schedule(path: Path) -> ScheduleConfig:
    if not path.exists():
        return ScheduleConfig()
    raw = _safe_load(path)
    if raw is None:
        return ScheduleConfig()
    if not isinstance(raw, dict):
        raise ValidationError(
            code="user.validation",
            message=f"{path.name}: schedule.yaml must be a mapping",
        )
    try:
        return ScheduleConfig.model_validate(raw)
    except _PydanticValidationError as exc:
        raise ValidationError(
            code="user.validation",
            message=_format_validation_errors(path.name, exc, raw),
            errors=[dict(e) for e in exc.errors()],
        ) from exc
