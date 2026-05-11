"""Additional runner tests — flag/tempfile invocation modes + cancel-during-spawn race."""

from __future__ import annotations

import asyncio
import sys

import pytest

from harbin.config.models import AgentCli, Concurrency
from harbin.fleet.artifacts import ArtifactManager
from harbin.fleet.dock import DockManager
from harbin.runner.runner import AgentRunner

pytestmark = pytest.mark.asyncio


async def _make_runner(*, store, harbin_paths, cli: AgentCli):
    dock_manager = DockManager(store=store, dock_root=harbin_paths.dock_root)
    artifacts = ArtifactManager(
        root=harbin_paths.artifact_root, store=store, default_retention="30d"
    )
    runner = AgentRunner(
        store=store,
        artifacts=artifacts,
        dock_manager=dock_manager,
        agent_cli=cli,
        concurrency=Concurrency(per_dock=1, global_cap=4),
        kill_grace_seconds=2,
        prompts_dir=harbin_paths.prompts_dir,
    )
    return runner, dock_manager, artifacts


async def _wait_for_status(store, short_id: str, target: set[str], timeout: float = 15.0):
    end = asyncio.get_event_loop().time() + timeout
    while asyncio.get_event_loop().time() < end:
        job = await store.get_job_by_short_id(short_id)
        if job is not None and job.status in target:
            return job
        await asyncio.sleep(0.1)
    raise AssertionError(f"{short_id} never reached {target}")


async def test_flag_mode_invocation(store, harbin_paths, fake_agent_cli, local_fleet) -> None:
    cli = AgentCli(
        command=[sys.executable, str(fake_agent_cli), "--prompt", "${PROMPT}"],
        mode="flag",
    )
    runner, dm, arts = await _make_runner(store=store, harbin_paths=harbin_paths, cli=cli)
    state = await dm.register_from_existing_dock(local_fleet.dock_path)
    try:
        import os

        os.environ["HARBIN_FAKE_MODE"] = "flag"
        try:
            row = await runner.enqueue(
                fleet=state.row, prompt="flag-mode hi", source="repl", task_label="adhoc"
            )
            job = await _wait_for_status(store, row.short_id, {"success", "failed"})
            assert job.status == "success", job
            artifact = arts.root / state.row.name / "adhoc" / row.short_id / "result.txt"
            assert artifact.exists()
            assert "flag-mode hi" in artifact.read_text(encoding="utf-8")
        finally:
            os.environ.pop("HARBIN_FAKE_MODE", None)
    finally:
        await runner.stop()


async def test_tempfile_mode_invocation(store, harbin_paths, fake_agent_cli, local_fleet) -> None:
    cli = AgentCli(
        command=[sys.executable, str(fake_agent_cli), "--prompt-file", "${PROMPT}"],
        mode="tempfile",
    )
    runner, dm, arts = await _make_runner(store=store, harbin_paths=harbin_paths, cli=cli)
    state = await dm.register_from_existing_dock(local_fleet.dock_path)
    try:
        import os

        os.environ["HARBIN_FAKE_MODE"] = "tempfile"
        try:
            row = await runner.enqueue(
                fleet=state.row,
                prompt="tempfile prompt content",
                source="repl",
                task_label="adhoc",
            )
            job = await _wait_for_status(store, row.short_id, {"success", "failed"})
            assert job.status == "success", job
            artifact = arts.root / state.row.name / "adhoc" / row.short_id / "result.txt"
            assert artifact.exists()
            assert "tempfile prompt content" in artifact.read_text(encoding="utf-8")
        finally:
            os.environ.pop("HARBIN_FAKE_MODE", None)
    finally:
        await runner.stop()


async def test_cancel_queued_job(store, harbin_paths, fake_agent_cli, local_fleet) -> None:
    """Cancel a job that is still 'queued' (not yet dispatched)."""
    cli = AgentCli(command=[sys.executable, str(fake_agent_cli)], mode="stdin")
    runner, dm, arts = await _make_runner(store=store, harbin_paths=harbin_paths, cli=cli)
    state = await dm.register_from_existing_dock(local_fleet.dock_path)
    try:
        # Hold the dispatcher: enqueue a long job and then a second one,
        # then cancel the second while it's still queued.
        import os

        os.environ["HARBIN_FAKE_DURATION"] = "8"
        try:
            r1 = await runner.enqueue(
                fleet=state.row, prompt="blocker", source="repl", task_label="adhoc"
            )
            r2 = await runner.enqueue(
                fleet=state.row, prompt="should-cancel", source="repl", task_label="adhoc"
            )
            # Wait until r1 is starting/running so r2 is firmly queued.
            await _wait_for_status(store, r1.short_id, {"starting", "running"})
            ok, msg = await runner.cancel(r2.short_id)
            assert ok, msg
            job2 = await store.get_job_by_short_id(r2.short_id)
            assert job2 is not None
            assert job2.status == "cancelled"
        finally:
            os.environ.pop("HARBIN_FAKE_DURATION", None)
    finally:
        await runner.stop()
