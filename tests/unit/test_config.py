"""Tests for config / fleet / schedule loading + retention parsing."""

from __future__ import annotations

import datetime as _dt
from pathlib import Path

import pytest

from harbin.config.loader import load_config, load_fleet, load_schedule
from harbin.config.models import AgentCli, parse_retention
from harbin.errors import ValidationError


def test_parse_retention_units() -> None:
    assert parse_retention("30d") == _dt.timedelta(days=30)
    assert parse_retention("12h") == _dt.timedelta(hours=12)
    assert parse_retention("45m") == _dt.timedelta(minutes=45)
    assert parse_retention("60s") == _dt.timedelta(seconds=60)


def test_parse_retention_bad() -> None:
    with pytest.raises(ValueError):
        parse_retention("xyz")
    with pytest.raises(ValueError):
        parse_retention("0d")


def test_load_config_writes_default(tmp_path: Path) -> None:
    target = tmp_path / "config.yaml"
    cfg = load_config(target)
    assert target.exists()
    assert cfg.ui.theme == "harbor"
    assert cfg.scheduler.tick_seconds == 5
    assert cfg.agent_runner.agent_cli.command == ["copilot"]


def test_load_config_validation_error(tmp_path: Path) -> None:
    target = tmp_path / "config.yaml"
    target.write_text("scheduler:\n  tick_seconds: 0\n", encoding="utf-8")
    with pytest.raises(ValidationError):
        load_config(target, write_default_if_missing=False)


def test_env_override(tmp_path: Path, monkeypatch) -> None:
    target = tmp_path / "config.yaml"
    monkeypatch.setenv("HARBIN_WEB_PORT", "9999")
    cfg = load_config(target)
    assert cfg.web.port == 9999


def test_agent_cli_validation() -> None:
    # stdin with placeholder → error
    with pytest.raises(Exception):
        AgentCli(command=["a", "${PROMPT}"], mode="stdin")
    # flag without placeholder → error
    with pytest.raises(Exception):
        AgentCli(command=["a"], mode="flag", placeholder="${PROMPT}")
    # ok flag
    a = AgentCli(command=["a", "${PROMPT}"], mode="flag", placeholder="${PROMPT}")
    assert a.mode == "flag"


def test_fleet_yaml(tmp_path: Path) -> None:
    p = tmp_path / "fleet.yaml"
    p.write_text(
        "name: my-fleet\ndefault_branch: main\nartifact_policy:\n  retain: 7d\n  push_back: true\n",
        encoding="utf-8",
    )
    cfg = load_fleet(p)
    assert cfg.name == "my-fleet"
    assert cfg.artifact_policy.push_back is True


def test_fleet_yaml_bad_name(tmp_path: Path) -> None:
    p = tmp_path / "fleet.yaml"
    p.write_text("name: BAD-name!\ndefault_branch: main\n", encoding="utf-8")
    with pytest.raises(ValidationError):
        load_fleet(p)


def test_schedule_valid(tmp_path: Path) -> None:
    p = tmp_path / "schedule.yaml"
    p.write_text(
        "tasks:\n  - id: t1\n    cron: '0 7 * * *'\n    prompt: hello\n",
        encoding="utf-8",
    )
    sc = load_schedule(p)
    assert len(sc.tasks) == 1
    assert sc.tasks[0].id == "t1"


def test_schedule_invalid_cron(tmp_path: Path) -> None:
    p = tmp_path / "schedule.yaml"
    p.write_text(
        "tasks:\n  - id: t1\n    cron: 'definitely bad'\n    prompt: x\n",
        encoding="utf-8",
    )
    with pytest.raises(ValidationError):
        load_schedule(p)


def test_schedule_duplicate_ids(tmp_path: Path) -> None:
    p = tmp_path / "schedule.yaml"
    p.write_text(
        "tasks:\n"
        "  - id: t1\n    cron: '* * * * *'\n    prompt: a\n"
        "  - id: t1\n    cron: '* * * * *'\n    prompt: b\n",
        encoding="utf-8",
    )
    with pytest.raises(ValidationError):
        load_schedule(p)
