"""End-to-end integration tests using the fake agent CLI.

Covers sub-spec 05 §5 must-cover scenarios:
  1. Adhoc happy path
  2. Failure path
  3. Cancel
  6. Retention sweep (smoke)
  8. Per-dock serial concurrency
"""

from __future__ import annotations

import asyncio
import datetime as _dt
import sys
from pathlib import Path

import pytest

from harbin.config.models import AgentCli, Concurrency
from harbin.fleet.artifacts import ArtifactManager
from harbin.fleet.dock import DockManager
from harbin.runner.runner import AgentRunner

pytestmark = pytest.mark.asyncio


async def _make_runner(
    *, store, harbin_paths, fake_agent_cli: Path, per_dock: int = 1, global_cap: int = 4
):
    """Wire up a minimal AgentRunner + DockManager + ArtifactManager."""
    dock_manager = DockManager(store=store, dock_root=harbin_paths.dock_root)
    artifacts = ArtifactManager(
        root=harbin_paths.artifact_root, store=store, default_retention="30d"
    )
    cli = AgentCli(command=[sys.executable, str(fake_agent_cli)], mode="stdin")
    runner = AgentRunner(
        store=store,
        artifacts=artifacts,
        dock_manager=dock_manager,
        agent_cli=cli,
        concurrency=Concurrency(per_dock=per_dock, global_cap=global_cap),
        kill_grace_seconds=2,
        prompts_dir=harbin_paths.prompts_dir,
    )
    return runner, dock_manager, artifacts


async def _register_local_fleet(store, dock_manager, local_fleet):
    state = await dock_manager.register_from_existing_dock(local_fleet.dock_path)
    return state


async def _wait_for_status(store, short_id: str, target: set[str], timeout: float = 15.0):
    end = asyncio.get_event_loop().time() + timeout
    last = None
    while asyncio.get_event_loop().time() < end:
        job = await store.get_job_by_short_id(short_id)
        last = job
        if job is not None and job.status in target:
            return job
        await asyncio.sleep(0.1)
    raise AssertionError(
        f"job {short_id} did not reach {target} within {timeout}s (last status={last.status if last else None})"
    )


async def test_adhoc_happy_path(store, harbin_paths, fake_agent_cli, local_fleet):
    runner, dm, arts = await _make_runner(
        store=store, harbin_paths=harbin_paths, fake_agent_cli=fake_agent_cli
    )
    state = await _register_local_fleet(store, dm, local_fleet)
    try:
        row = await runner.enqueue(
            fleet=state.row, prompt="hello there", source="repl", task_label="adhoc"
        )
        job = await _wait_for_status(store, row.short_id, {"success", "failed"})
        assert job.status == "success"
        assert job.exit_code == 0
        # Artifact present
        artifact = arts.root / state.row.name / "adhoc" / row.short_id / "result.txt"
        assert artifact.exists()
        text = artifact.read_text(encoding="utf-8")
        assert "echoed: hello there" in text
    finally:
        await runner.stop()


async def test_failure_path(store, harbin_paths, fake_agent_cli, local_fleet, monkeypatch):
    monkeypatch.setenv("HARBIN_FAKE_EXIT", "2")
    runner, dm, arts = await _make_runner(
        store=store, harbin_paths=harbin_paths, fake_agent_cli=fake_agent_cli
    )
    state = await _register_local_fleet(store, dm, local_fleet)
    try:
        row = await runner.enqueue(
            fleet=state.row, prompt="boom", source="repl", task_label="adhoc"
        )
        job = await _wait_for_status(store, row.short_id, {"failed", "success"})
        assert job.status == "failed"
        assert job.exit_code == 2
    finally:
        await runner.stop()


async def test_cancel_running(store, harbin_paths, fake_agent_cli, local_fleet, monkeypatch):
    monkeypatch.setenv("HARBIN_FAKE_DURATION", "10")
    runner, dm, arts = await _make_runner(
        store=store, harbin_paths=harbin_paths, fake_agent_cli=fake_agent_cli
    )
    state = await _register_local_fleet(store, dm, local_fleet)
    try:
        row = await runner.enqueue(
            fleet=state.row, prompt="long", source="repl", task_label="adhoc"
        )
        # wait for running
        await _wait_for_status(store, row.short_id, {"running"})
        ok, msg = await runner.cancel(row.short_id)
        assert ok is True, msg
        job = await _wait_for_status(store, row.short_id, {"cancelled"})
        assert job.status == "cancelled"
    finally:
        await runner.stop()


async def test_per_dock_serial(store, harbin_paths, fake_agent_cli, local_fleet, monkeypatch):
    monkeypatch.setenv("HARBIN_FAKE_DURATION", "1")
    runner, dm, arts = await _make_runner(
        store=store, harbin_paths=harbin_paths, fake_agent_cli=fake_agent_cli
    )
    state = await _register_local_fleet(store, dm, local_fleet)
    try:
        r1 = await runner.enqueue(fleet=state.row, prompt="a", source="repl", task_label="adhoc")
        r2 = await runner.enqueue(fleet=state.row, prompt="b", source="repl", task_label="adhoc")
        j1 = await _wait_for_status(store, r1.short_id, {"success", "failed"})
        j2 = await _wait_for_status(store, r2.short_id, {"success", "failed"})
        # Both should succeed; j1 must have started before j2 finished starting/running.
        assert j1.status == "success"
        assert j2.status == "success"
        # j1.ended_at <= j2.started_at  →  truly serial
        assert j1.ended_at is not None and j2.started_at is not None
        assert j1.ended_at <= j2.started_at
    finally:
        await runner.stop()


async def test_retention_sweep_smoke(store, harbin_paths, fake_agent_cli, local_fleet):
    runner, dm, arts = await _make_runner(
        store=store, harbin_paths=harbin_paths, fake_agent_cli=fake_agent_cli
    )
    state = await _register_local_fleet(store, dm, local_fleet)
    try:
        row = await runner.enqueue(fleet=state.row, prompt="x", source="repl", task_label="adhoc")
        await _wait_for_status(store, row.short_id, {"success"})
        # Backdate ended_at so the sweep picks it up.
        await store._conn.execute(  # type: ignore[attr-defined]
            "UPDATE jobs SET ended_at='2000-01-01T00:00:00.000Z' WHERE short_id=?",
            (row.short_id,),
        )
        await store._conn.commit()  # type: ignore[attr-defined]
        fleets = await store.list_fleets()
        archived = await arts.sweep(
            fleets=fleets,
            fleet_configs={state.row.id: state.fleet_config},
            now=_dt.datetime.now(_dt.UTC),
        )
        assert archived == 1
        job = await store.get_job_by_short_id(row.short_id)
        assert job is not None
        assert job.status == "archived"
        artifact_dir = arts.root / state.row.name / "adhoc" / row.short_id
        assert not artifact_dir.exists()
    finally:
        await runner.stop()
