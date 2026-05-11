"""Tests for scheduler reconcile/re-anchor behaviour."""

from __future__ import annotations

import datetime as _dt
from unittest.mock import AsyncMock

import pytest

from harbin.config.models import AgentCli, Concurrency
from harbin.fleet.dock import DockManager, DockState
from harbin.fleet.models import ArtifactPolicy, FleetConfig, ScheduleConfig, TaskSpec
from harbin.runner.runner import AgentRunner
from harbin.scheduler import Scheduler

pytestmark = pytest.mark.asyncio


def _fleet_cfg() -> FleetConfig:
    return FleetConfig(
        name="testf",
        default_branch="main",
        artifact_policy=ArtifactPolicy(retain="30d", push_back=False),
    )


async def test_reanchor_on_cron_change(store, harbin_paths) -> None:
    fleet = await store.insert_fleet(name="testf", url="x", dock_path=str(harbin_paths.dock_root))
    dm = DockManager(store=store, dock_root=harbin_paths.dock_root)
    cfg = _fleet_cfg()
    schedule_cfg = ScheduleConfig(tasks=[TaskSpec(id="morning", cron="0 7 * * *", prompt="hi")])
    state = DockState(row=fleet, fleet_config=cfg, schedule_config=schedule_cfg)
    dm._states[fleet.id] = state  # type: ignore[attr-defined]

    runner = AgentRunner(
        store=store,
        artifacts=AsyncMock(),
        dock_manager=dm,
        agent_cli=AgentCli(command=["true"], mode="stdin"),
        concurrency=Concurrency(per_dock=1, global_cap=1),
        kill_grace_seconds=1,
        prompts_dir=harbin_paths.prompts_dir,
    )

    sched = Scheduler(
        store=store,
        dock_manager=dm,
        runner=runner,
        tick_seconds=5,
        timezone_name="UTC",
        clock=lambda: _dt.datetime(2026, 1, 1, 6, 0, tzinfo=_dt.UTC),
    )

    # First reconcile: task added; anchor set.
    await sched._reconcile_fleet(state)  # type: ignore[attr-defined]
    tasks = await store.list_tasks_for_fleet(fleet.id)
    assert len(tasks) == 1
    first_fire = await store.get_last_fire(tasks[0].id)
    assert first_fire is not None

    # Change the cron; reconcile again; anchor should be reset.
    state.schedule_config = ScheduleConfig(
        tasks=[TaskSpec(id="morning", cron="30 7 * * *", prompt="hi")]
    )
    sched._clock = lambda: _dt.datetime(  # type: ignore[attr-defined]
        2026, 1, 2, 6, 0, tzinfo=_dt.UTC
    )
    await sched._reconcile_fleet(state)  # type: ignore[attr-defined]
    second_fire = await store.get_last_fire(tasks[0].id)
    assert second_fire is not None
    assert second_fire != first_fire


async def test_task_removed_when_disappears_from_schedule(store, harbin_paths) -> None:
    fleet = await store.insert_fleet(name="testf", url="x", dock_path=str(harbin_paths.dock_root))
    dm = DockManager(store=store, dock_root=harbin_paths.dock_root)
    cfg = _fleet_cfg()
    state = DockState(
        row=fleet,
        fleet_config=cfg,
        schedule_config=ScheduleConfig(tasks=[TaskSpec(id="hourly", cron="0 * * * *", prompt="x")]),
    )
    dm._states[fleet.id] = state  # type: ignore[attr-defined]
    runner = AgentRunner(
        store=store,
        artifacts=AsyncMock(),
        dock_manager=dm,
        agent_cli=AgentCli(command=["true"], mode="stdin"),
        concurrency=Concurrency(per_dock=1, global_cap=1),
        kill_grace_seconds=1,
        prompts_dir=harbin_paths.prompts_dir,
    )
    sched = Scheduler(
        store=store,
        dock_manager=dm,
        runner=runner,
        tick_seconds=5,
        timezone_name="UTC",
        clock=lambda: _dt.datetime(2026, 1, 1, tzinfo=_dt.UTC),
    )
    await sched._reconcile_fleet(state)  # type: ignore[attr-defined]
    assert len(await store.list_tasks_for_fleet(fleet.id)) == 1
    state.schedule_config = ScheduleConfig(tasks=[])
    await sched._reconcile_fleet(state)  # type: ignore[attr-defined]
    assert await store.list_tasks_for_fleet(fleet.id) == []
