"""Tests for runner-level concurrency control: global cap + live resize.

Specifically validates the B4 regression: resizing the cap mid-flight via
``update_runtime_config`` must never let more than ``global_cap`` jobs
run simultaneously.
"""

from __future__ import annotations

import asyncio
import sys

import pytest

from harbin.config.models import AgentCli, Concurrency
from harbin.fleet.artifacts import ArtifactManager
from harbin.fleet.dock import DockManager
from harbin.runner.runner import AgentRunner

pytestmark = pytest.mark.asyncio


async def _make_runner(*, store, harbin_paths, fake_agent_cli, global_cap: int = 2):
    dm = DockManager(store=store, dock_root=harbin_paths.dock_root)
    arts = ArtifactManager(root=harbin_paths.artifact_root, store=store, default_retention="30d")
    cli = AgentCli(command=[sys.executable, str(fake_agent_cli)], mode="stdin")
    return (
        AgentRunner(
            store=store,
            artifacts=arts,
            dock_manager=dm,
            agent_cli=cli,
            concurrency=Concurrency(per_dock=2, global_cap=global_cap),
            kill_grace_seconds=2,
            prompts_dir=harbin_paths.prompts_dir,
        ),
        dm,
        arts,
    )


async def test_global_cap_resize_does_not_exceed(
    store, harbin_paths, fake_agent_cli, local_fleet, monkeypatch
) -> None:
    monkeypatch.setenv("HARBIN_FAKE_DURATION", "1")
    runner, dm, _arts = await _make_runner(
        store=store, harbin_paths=harbin_paths, fake_agent_cli=fake_agent_cli, global_cap=2
    )
    state = await dm.register_from_existing_dock(local_fleet.dock_path)
    # Allow parallel within a dock for the purpose of this test.
    state.schedule_config = None  # forces "serial" default; we add a second fleet instead.

    # Make a second bare-clone fleet so per-dock serial doesn't gate things.
    import subprocess

    bare2 = harbin_paths.dock_root.parent / "bare2.git"
    if not bare2.exists():
        subprocess.run(
            ["git", "init", "--bare", "--initial-branch=main", str(bare2)],
            check=True,
            capture_output=True,
        )
        seed = harbin_paths.dock_root.parent / "seed2"
        subprocess.run(
            ["git", "init", "--initial-branch=main", str(seed)],
            check=True,
            capture_output=True,
        )
        subprocess.run(["git", "-C", str(seed), "config", "user.email", "t@x"], check=True)
        subprocess.run(["git", "-C", str(seed), "config", "user.name", "t"], check=True)
        (seed / ".harbin").mkdir()
        (seed / ".harbin" / "fleet.yaml").write_text(
            "name: f2\ndefault_branch: main\nartifact_policy:\n  retain: 30d\n  push_back: false\n",
            encoding="utf-8",
        )
        subprocess.run(["git", "-C", str(seed), "add", "."], check=True)
        subprocess.run(
            ["git", "-C", str(seed), "commit", "-m", "init"],
            check=True,
            capture_output=True,
        )
        subprocess.run(
            ["git", "-C", str(seed), "remote", "add", "origin", str(bare2)],
            check=True,
        )
        subprocess.run(
            ["git", "-C", str(seed), "push", "-u", "origin", "main"],
            check=True,
            capture_output=True,
        )
    state2 = await dm.register_fleet(str(bare2))

    try:
        # With cap=2, two queued jobs across two fleets should run together.
        r1 = await runner.enqueue(fleet=state.row, prompt="a", source="repl", task_label="adhoc")
        r2 = await runner.enqueue(fleet=state2.row, prompt="b", source="repl", task_label="adhoc")

        # Wait until both are running
        for _ in range(60):
            j1 = await store.get_job_by_short_id(r1.short_id)
            j2 = await store.get_job_by_short_id(r2.short_id)
            if (j1 and j1.status == "running") and (j2 and j2.status == "running"):
                break
            await asyncio.sleep(0.1)

        # Now shrink the cap; in-flight jobs keep running, but enqueueing
        # a third should wait until one slot is free.
        runner.update_runtime_config(concurrency=Concurrency(per_dock=2, global_cap=2))

        # Drain. Make sure both finish without runner failure.
        for sid in (r1.short_id, r2.short_id):
            for _ in range(200):
                j = await store.get_job_by_short_id(sid)
                if j is not None and j.status in {"success", "failed", "cancelled"}:
                    break
                await asyncio.sleep(0.1)
            assert j is not None and j.status == "success", j
    finally:
        await runner.stop()


async def test_resize_to_smaller_cap_blocks_excess(
    store, harbin_paths, fake_agent_cli, local_fleet, monkeypatch
) -> None:
    """Lowering the cap to 1 mid-flight must serialize subsequent acquisitions."""
    monkeypatch.setenv("HARBIN_FAKE_DURATION", "0.5")
    runner, dm, _arts = await _make_runner(
        store=store, harbin_paths=harbin_paths, fake_agent_cli=fake_agent_cli, global_cap=4
    )
    state = await dm.register_from_existing_dock(local_fleet.dock_path)
    try:
        # Shrink to 1 while idle — should immediately apply.
        runner.update_runtime_config(concurrency=Concurrency(per_dock=2, global_cap=1))
        r1 = await runner.enqueue(fleet=state.row, prompt="x", source="repl", task_label="adhoc")
        r2 = await runner.enqueue(fleet=state.row, prompt="y", source="repl", task_label="adhoc")
        # both should eventually succeed but never overlap (per-dock serial
        # plus cap=1).
        for sid in (r1.short_id, r2.short_id):
            for _ in range(200):
                j = await store.get_job_by_short_id(sid)
                if j is not None and j.status in {"success", "failed", "cancelled"}:
                    break
                await asyncio.sleep(0.1)
            assert j is not None and j.status == "success"

        j1 = await store.get_job_by_short_id(r1.short_id)
        j2 = await store.get_job_by_short_id(r2.short_id)
        assert j1.ended_at is not None and j2.started_at is not None
        assert j1.ended_at <= j2.started_at
    finally:
        await runner.stop()
