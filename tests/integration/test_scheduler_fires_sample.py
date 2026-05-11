"""Scheduler-fires-sample integration test.

Drives the real ``Scheduler`` against one of the bundled sample fleets:

1. Stage the price-monitor sample as a real dock.
2. Insert a task with cron ``* * * * *`` so the next-tick is always due.
3. Tick the scheduler once and confirm a job lands and the agent runs.

This is the regression guard for the design's "one-way-to-do-things"
principle (overview §4.1): a cron fire goes through the SAME enqueue path
as a typed ``@fleet`` prompt.
"""

from __future__ import annotations

import asyncio
import datetime as _dt
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from harbin.config.models import AgentCli, Concurrency
from harbin.fleet.artifacts import ArtifactManager
from harbin.fleet.dock import DockManager
from harbin.runner.runner import AgentRunner
from harbin.scheduler import Scheduler

_REPO_ROOT = Path(__file__).resolve().parents[2]
_EXAMPLES = _REPO_ROOT / "examples"
_PRICES = _EXAMPLES / "harbin-agent-sample-price-monitor"


def _have_submodules() -> bool:
    return (_PRICES / ".harbin" / "fleet.yaml").exists()


pytestmark = [
    pytest.mark.asyncio,
    pytest.mark.skipif(
        not _have_submodules(),
        reason="sample fleet submodules not initialised",
    ),
]


def _stage_clone(source: Path, dest: Path) -> Path:
    shutil.copytree(source, dest, dirs_exist_ok=False)
    submod_git = dest / ".git"
    if submod_git.exists():
        if submod_git.is_dir():
            shutil.rmtree(submod_git)
        else:
            submod_git.unlink()
    subprocess.run(
        ["git", "init", "--initial-branch=main", str(dest)],
        check=True,
        capture_output=True,
    )
    subprocess.run(["git", "-C", str(dest), "config", "user.email", "t@x"], check=True)
    subprocess.run(["git", "-C", str(dest), "config", "user.name", "t"], check=True)
    subprocess.run(["git", "-C", str(dest), "add", "-A"], check=True)
    subprocess.run(
        ["git", "-C", str(dest), "commit", "-m", "stage"],
        check=True,
        capture_output=True,
    )
    return dest


async def _wait_for_status(store, target: set[str], timeout: float = 30.0):
    end = asyncio.get_event_loop().time() + timeout
    while asyncio.get_event_loop().time() < end:
        jobs = await store.list_recent_jobs(limit=10)
        for job in jobs:
            if job.status in target:
                return job
        await asyncio.sleep(0.1)
    raise AssertionError(f"no job reached {target} within {timeout}s")


async def test_scheduler_fires_price_monitor_sample(store, harbin_paths, tmp_path) -> None:
    """Scheduler tick → runner enqueue → real agent run → prices.json."""
    dock = harbin_paths.dock_root / "harbin-agent-sample-price-monitor"
    _stage_clone(_PRICES, dock)

    dm = DockManager(store=store, dock_root=harbin_paths.dock_root)
    state = await dm.register_from_existing_dock(dock)

    cli = AgentCli(command=[sys.executable, "agent/run.py"], mode="stdin")
    arts = ArtifactManager(root=harbin_paths.artifact_root, store=store, default_retention="30d")
    runner = AgentRunner(
        store=store,
        artifacts=arts,
        dock_manager=dm,
        agent_cli=cli,
        concurrency=Concurrency(per_dock=1, global_cap=1),
        kill_grace_seconds=2,
        prompts_dir=harbin_paths.prompts_dir,
    )

    # Pin scheduler to UTC and freeze "now" one minute in the future of
    # the task's last_fire anchor. With cron ``* * * * *`` (every minute),
    # croniter's next-fire from a one-minute-old anchor is "now" → due.
    fixed_now = _dt.datetime(2026, 1, 1, 12, 1, 0, tzinfo=_dt.UTC)

    scheduler = Scheduler(
        store=store,
        dock_manager=dm,
        runner=runner,
        tick_seconds=1,
        timezone_name="UTC",
        clock=lambda: fixed_now,
    )
    # Reconcile picks up the schedule.yaml task (``hourly-prices`` cron
    # ``0 * * * *``). Anchor it to a time that makes the next fire due.
    await scheduler.reconcile_all()
    tasks = await store.list_tasks_for_fleet(state.row.id)
    assert any(t.task_id == "hourly-prices" for t in tasks)
    # Override the task's cron in-place to "* * * * *" and anchor at
    # 11:59:00 so the next fire is exactly 12:00:00 ≤ fixed_now (12:01).
    hourly = next(t for t in tasks if t.task_id == "hourly-prices")
    # Direct DB poke: simpler than rebuilding the schedule.yaml.
    async with store._conn.execute(  # type: ignore[attr-defined]
        "UPDATE tasks SET cron=? WHERE id=?", ("* * * * *", hourly.id)
    ):
        pass
    await store._conn.commit()  # type: ignore[attr-defined]
    await store.upsert_last_fire(hourly.id, _dt.datetime(2026, 1, 1, 11, 59, 0, tzinfo=_dt.UTC))

    try:
        # One scheduler iteration is enough — _maybe_fire_once enqueues.
        await scheduler._maybe_fire_once()  # type: ignore[attr-defined]
        # Now poll for the resulting job. ``source='schedule'`` proves
        # the scheduler did the enqueueing, not us.
        job = await _wait_for_status(store, {"success", "failed"})
        assert job.source == "schedule", job
        assert job.status == "success", job
        prices = arts.root / state.row.name / "hourly-prices" / job.short_id / "prices.json"
        assert prices.exists()
    finally:
        await runner.stop()
        await dm.stop()
