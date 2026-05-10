"""Pydantic schemas for ``fleet.yaml`` and ``schedule.yaml`` (sub-spec 03 §2-3)."""

from __future__ import annotations

import hashlib
import re
from typing import Annotated, Literal

from croniter import croniter
from pydantic import BaseModel, ConfigDict, Field, field_validator

from harbin.config.models import AgentCli, Retention, parse_retention

FLEET_NAME_RE = re.compile(r"^[a-z][a-z0-9-]{1,30}$")
TASK_ID_RE = re.compile(r"^[a-z][a-z0-9-]{1,40}$")

FleetName = Annotated[str, Field(pattern=FLEET_NAME_RE.pattern)]
TaskId = Annotated[str, Field(pattern=TASK_ID_RE.pattern)]


class ArtifactPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid")

    retain: Retention = "30d"
    push_back: bool = False

    @field_validator("retain")
    @classmethod
    def _validate_retention(cls, v: str) -> str:
        parse_retention(v)
        return v


class FleetConfig(BaseModel):
    """``<dock>/.harbin/fleet.yaml``."""

    model_config = ConfigDict(extra="forbid")

    name: FleetName
    default_branch: str = "main"
    agent_cli: AgentCli | None = None
    artifact_policy: ArtifactPolicy = Field(default_factory=ArtifactPolicy)
    sync_interval: str | None = "5m"

    @field_validator("sync_interval")
    @classmethod
    def _validate_sync_interval(cls, v: str | None) -> str | None:
        if v is None:
            return None
        parse_retention(v)
        return v


class TaskSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: TaskId
    cron: str
    prompt: str
    concurrency: Literal["serial", "parallel"] = "serial"

    @field_validator("cron")
    @classmethod
    def _validate_cron(cls, v: str) -> str:
        if not croniter.is_valid(v):
            raise ValueError(f"invalid cron expression: '{v}'")
        return v

    @property
    def source_sha(self) -> str:
        h = hashlib.sha256()
        h.update(self.cron.encode("utf-8"))
        h.update(b"\0")
        h.update(self.prompt.encode("utf-8"))
        return h.hexdigest()


class ScheduleConfig(BaseModel):
    """``<dock>/.harbin/schedule.yaml``."""

    model_config = ConfigDict(extra="forbid")

    tasks: list[TaskSpec] = Field(default_factory=list)

    @field_validator("tasks")
    @classmethod
    def _validate_unique_ids(cls, v: list[TaskSpec]) -> list[TaskSpec]:
        ids = [t.id for t in v]
        if len(set(ids)) != len(ids):
            dupes = {x for x in ids if ids.count(x) > 1}
            raise ValueError(f"duplicate task ids: {sorted(dupes)}")
        return v
