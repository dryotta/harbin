"""Pydantic schemas for ``config.yaml`` (sub-spec 03 §1)."""

from __future__ import annotations

import datetime as _dt
import re
from pathlib import Path
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

_RETENTION_RE = re.compile(r"^\s*(\d+)\s*([smhd])\s*$", re.IGNORECASE)


def parse_retention(value: str) -> _dt.timedelta:
    """Parse a retention literal like ``30d``, ``12h``, ``45m``, ``300s``."""
    m = _RETENTION_RE.fullmatch(value)
    if not m:
        raise ValueError(f"invalid retention '{value}'; expected <int>(s|m|h|d) e.g. 30d")
    n = int(m.group(1))
    unit = m.group(2).lower()
    if n <= 0:
        raise ValueError(f"retention must be positive (got '{value}')")
    return {
        "s": _dt.timedelta(seconds=n),
        "m": _dt.timedelta(minutes=n),
        "h": _dt.timedelta(hours=n),
        "d": _dt.timedelta(days=n),
    }[unit]


Retention = Annotated[str, Field(pattern=_RETENTION_RE.pattern, default="30d")]


# ────────────────────────── leaf models ───────────────────────────────


class UISettings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    theme: str = "harbor"
    log_verbosity: Literal["debug", "info", "warning", "error"] = "info"


class SchedulerSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tick_seconds: int = Field(default=5, ge=1, le=60)


class AgentCli(BaseModel):
    model_config = ConfigDict(extra="forbid")

    command: list[str] = Field(default_factory=lambda: ["copilot"], min_length=1)
    mode: Literal["stdin", "flag", "tempfile"] = "stdin"
    placeholder: str = "${PROMPT}"

    @model_validator(mode="after")
    def _check_placeholder(self) -> AgentCli:
        joined = " ".join(self.command)
        if self.mode == "stdin":
            if "${PROMPT}" in joined or "${PROMPT_FILE}" in joined:
                raise ValueError(
                    "mode=stdin must not include ${PROMPT} or ${PROMPT_FILE} in command"
                )
        else:
            if self.placeholder not in joined:
                raise ValueError(
                    f"mode={self.mode}: placeholder '{self.placeholder}' must appear in command"
                )
        return self


class Concurrency(BaseModel):
    model_config = ConfigDict(extra="forbid")

    per_dock: int = Field(default=1, ge=1, le=4)
    global_cap: int = Field(default=4, ge=1, le=16)


class AgentRunnerSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    agent_cli: AgentCli = Field(default_factory=AgentCli)
    concurrency: Concurrency = Field(default_factory=Concurrency)
    kill_grace_seconds: int = Field(default=10, ge=1, le=300)


class ArtifactSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    root: Path | None = None
    retention: Retention = "30d"
    sweep_cron: str = "0 4 * * *"

    @field_validator("retention")
    @classmethod
    def _validate_retention(cls, v: str) -> str:
        parse_retention(v)
        return v


class WebSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    port: int = Field(default=8080, ge=1, le=65535)
    host: str = "127.0.0.1"
    autostart: bool = False


class TunnelSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    devtunnel_path: str = "devtunnel"
    tunnel_id: str | None = None
    allow_anonymous: bool = False


class Config(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ui: UISettings = Field(default_factory=UISettings)
    timezone: str = "system"
    scheduler: SchedulerSettings = Field(default_factory=SchedulerSettings)
    agent_runner: AgentRunnerSettings = Field(default_factory=AgentRunnerSettings)
    artifacts: ArtifactSettings = Field(default_factory=ArtifactSettings)
    web: WebSettings = Field(default_factory=WebSettings)
    tunnels: TunnelSettings = Field(default_factory=TunnelSettings)
    keybindings: dict[str, str] = Field(default_factory=dict)
