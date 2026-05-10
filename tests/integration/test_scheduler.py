"""Integration tests for the scheduler."""

from __future__ import annotations

import asyncio
import datetime as _dt
import sys

import pytest

from harbin.config.models import AgentCli, Concurrency
from harbin.fleet.artifacts import ArtifactManager
from harbin.fleet.dock import DockManager
from harbin.runner.runner import AgentRunner
from harbin.scheduler import Scheduler

pytestmark = pytest.mark.asyncio


async def test_scheduled_happy_path(store, harbin_paths, fake_agent_cli, local_fleet):
    # Add a schedule.yaml to the dock and commit it
    schedule = local_fleet.dock_path / ".harbin" / "schedule.yaml"
    schedule.write_text(
        "tasks:\n  - id: every-min\n    cron: '* * * * *'\n    prompt: 'scheduled hello'\n",
        encoding="utf-8",
    )

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
        concurrency=Concurrency(per_dock=1, global_cap=4),
        kill_grace_seconds=2,
        prompts_dir=harbin_paths.prompts_dir,
    )
    state = await dock_manager.register_from_existing_dock(local_fleet.dock_path)

    # Use a custom clock so the scheduler thinks it's well past the next firing
    fake_now = [_dt.datetime(2030, 1, 1, 12, 0, 0, tzinfo=_dt.UTC)]
    sched = Scheduler(
        store=store,
        dock_manager=dock_manager,
        runner=runner,
        tick_seconds=1,
        timezone_name="system",
        clock=lambda: fake_now[0],
    )

    try:
        await sched.reconcile_all()
        await sched.start()
        # Advance the clock by 2 minutes; cron "* * * * *" should fire.
        fake_now[0] = fake_now[0] + _dt.timedelta(minutes=2)
        # Wait up to 5s for the scheduler tick to enqueue
        end = asyncio.get_event_loop().time() + 5
        fired = False
        while asyncio.get_event_loop().time() < end:
            recent = await store.list_recent_jobs(limit=10)
            if recent:
                fired = True
                break
            await asyncio.sleep(0.1)
        assert fired, "scheduler never enqueued a job"

        # And the job should reach success
        end = asyncio.get_event_loop().time() + 10
        while asyncio.get_event_loop().time() < end:
            recent = await store.list_recent_jobs(limit=10)
            if recent and recent[0].status in {"success", "failed"}:
                assert recent[0].status == "success"
                return
            await asyncio.sleep(0.1)
        raise AssertionError("scheduled job did not complete in time")
    finally:
        await sched.stop()
        await runner.stop()
        await dock_manager.stop()
