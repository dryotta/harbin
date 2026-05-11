"""Tests for runner-level concurrency control: global cap + live resize.

Specifically validates the B4 regression: resizing the cap mid-flight via
``update_runtime_config`` must never let more than ``global_cap`` jobs run
simultaneously, and raising the cap must wake any blocked acquirers.

Adhoc jobs default to ``concurrency="serial"`` (sub-spec 10), so to isolate
the **global** cap from the **per-dock** gate, each test spreads jobs across
multiple fleets — one fleet per concurrent job.
"""

from __future__ import annotations

import asyncio
import subprocess
import sys
from pathlib import Path

import pytest

from harbin.config.models import AgentCli, Concurrency
from harbin.fleet.artifacts import ArtifactManager
from harbin.fleet.dock import DockManager
from harbin.runner.runner import AgentRunner

pytestmark = pytest.mark.asyncio


def _make_bare_fleet(tmp: Path, name: str) -> Path:
    """Make a self-contained bare repo for use as a fleet remote."""
    bare = tmp / f"{name}.git"
    if bare.exists():
        return bare
    subprocess.run(
        ["git", "init", "--bare", "--initial-branch=main", str(bare)],
        check=True,
        capture_output=True,
    )
    seed = tmp / f"{name}-seed"
    subprocess.run(
        ["git", "init", "--initial-branch=main", str(seed)],
        check=True,
        capture_output=True,
    )
    subprocess.run(["git", "-C", str(seed), "config", "user.email", "t@x"], check=True)
    subprocess.run(["git", "-C", str(seed), "config", "user.name", "t"], check=True)
    (seed / ".harbin").mkdir()
    (seed / ".harbin" / "fleet.yaml").write_text(
        f"name: {name}\ndefault_branch: main\nartifact_policy:\n"
        "  retain: 30d\n  push_back: false\n",
        encoding="utf-8",
    )
    subprocess.run(["git", "-C", str(seed), "add", "."], check=True)
    subprocess.run(
        ["git", "-C", str(seed), "commit", "-m", "init"],
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "-C", str(seed), "remote", "add", "origin", str(bare)],
        check=True,
    )
    subprocess.run(
        ["git", "-C", str(seed), "push", "-u", "origin", "main"],
        check=True,
        capture_output=True,
    )
    return bare


async def _make_runner(*, store, harbin_paths, fake_agent_cli, global_cap: int):
    dm = DockManager(store=store, dock_root=harbin_paths.dock_root)
    arts = ArtifactManager(root=harbin_paths.artifact_root, store=store, default_retention="30d")
    cli = AgentCli(command=[sys.executable, str(fake_agent_cli)], mode="stdin")
    runner = AgentRunner(
        store=store,
        artifacts=arts,
        dock_manager=dm,
        agent_cli=cli,
        concurrency=Concurrency(per_dock=2, global_cap=global_cap),
        kill_grace_seconds=2,
        prompts_dir=harbin_paths.prompts_dir,
    )
    return runner, dm, arts


async def _wait_status(store, short_id: str, target: set[str], timeout: float = 20.0):
    end = asyncio.get_event_loop().time() + timeout
    while asyncio.get_event_loop().time() < end:
        job = await store.get_job_by_short_id(short_id)
        if job is not None and job.status in target:
            return job
        await asyncio.sleep(0.05)
    raise AssertionError(f"{short_id} never reached {target}")


async def test_shrink_cap_midflight_blocks_new_acquisitions(
    store, harbin_paths, fake_agent_cli, tmp_path, monkeypatch
) -> None:
    """B4 regression: with 3 jobs running at cap=3, shrinking the cap to 1
    must prevent a 4th job from starting until the in-flight set has drained
    below the new cap (i.e. all 3 must finish first)."""
    # Long-running jobs so we have time to observe the resize behavior.
    monkeypatch.setenv("HARBIN_FAKE_DURATION", "2.5")
    runner, dm, _arts = await _make_runner(
        store=store, harbin_paths=harbin_paths, fake_agent_cli=fake_agent_cli, global_cap=3
    )
    # Three distinct fleets so the per-dock serial gate doesn't dominate.
    fleets = []
    for i in range(3):
        bare = _make_bare_fleet(tmp_path, f"fleet-cap-{i}")
        fleets.append(await dm.register_fleet(str(bare)))
    bare4 = _make_bare_fleet(tmp_path, "fleet-cap-late")
    fleet4 = await dm.register_fleet(str(bare4))

    try:
        # Enqueue one job per fleet — should all run together.
        rows = []
        for f in fleets:
            rows.append(
                await runner.enqueue(fleet=f.row, prompt="x", source="repl", task_label="adhoc")
            )
        for r in rows:
            await _wait_status(store, r.short_id, {"running"})
        # Sanity: all 3 should be 'running' simultaneously.
        statuses = []
        for r in rows:
            j = await store.get_job_by_short_id(r.short_id)
            statuses.append(j.status if j else None)
        assert statuses == ["running", "running", "running"], statuses
        # Internal inflight counter matches.
        assert runner._inflight == 3  # type: ignore[attr-defined]

        # Shrink the cap. In-flight jobs are not pre-empted, but new
        # acquisitions must now wait for inflight to fall below 1.
        runner.update_runtime_config(concurrency=Concurrency(per_dock=2, global_cap=1))

        # Enqueue a 4th job on a 4th fleet.
        late = await runner.enqueue(
            fleet=fleet4.row, prompt="late", source="repl", task_label="adhoc"
        )

        # Snapshot: the late job must be 'queued' while any of the first 3
        # is still running.
        await asyncio.sleep(0.3)
        late_job = await store.get_job_by_short_id(late.short_id)
        assert late_job is not None
        # Any of the first three should still be running.
        in_flight_count = 0
        for r in rows:
            j = await store.get_job_by_short_id(r.short_id)
            if j and j.status in {"starting", "running"}:
                in_flight_count += 1
        assert in_flight_count >= 1
        assert late_job.status == "queued", (
            f"late job should be queued while {in_flight_count} of the "
            f"first 3 are still running; status was {late_job.status}"
        )

        # Wait for everything to settle. The late job must eventually run.
        for r in [*rows, late]:
            await _wait_status(store, r.short_id, {"success", "failed"}, timeout=30.0)
        late_job = await store.get_job_by_short_id(late.short_id)
        assert late_job is not None and late_job.status == "success", late_job
        # And the late job's started_at must be ≥ the latest of the first 3
        # ended_at (i.e. all 3 had to drain first because cap=1).
        ends: list[str] = []
        for r in rows:
            j = await store.get_job_by_short_id(r.short_id)
            assert j is not None and j.ended_at is not None
            ends.append(j.ended_at)
        latest_end = max(ends)
        assert late_job.started_at is not None
        assert late_job.started_at >= latest_end, (
            f"late job started at {late_job.started_at}, "
            f"but the last in-flight job ended at {latest_end}"
        )
    finally:
        await runner.stop()


async def test_grow_cap_midflight_unblocks_waiters(
    store, harbin_paths, fake_agent_cli, tmp_path, monkeypatch
) -> None:
    """Growing the cap must notify blocked acquirers. With cap=1 and two
    queued jobs on different fleets, the second job is blocked. After
    raising cap to 2 it should start without waiting for the first to
    finish."""
    monkeypatch.setenv("HARBIN_FAKE_DURATION", "3")
    runner, dm, _arts = await _make_runner(
        store=store, harbin_paths=harbin_paths, fake_agent_cli=fake_agent_cli, global_cap=1
    )
    bare_a = _make_bare_fleet(tmp_path, "fleet-grow-a")
    bare_b = _make_bare_fleet(tmp_path, "fleet-grow-b")
    fa = await dm.register_fleet(str(bare_a))
    fb = await dm.register_fleet(str(bare_b))

    try:
        ra = await runner.enqueue(fleet=fa.row, prompt="a", source="repl", task_label="adhoc")
        rb = await runner.enqueue(fleet=fb.row, prompt="b", source="repl", task_label="adhoc")
        # a is the first to acquire the cap; b should be 'queued'.
        await _wait_status(store, ra.short_id, {"running"})
        await asyncio.sleep(0.3)
        jb = await store.get_job_by_short_id(rb.short_id)
        assert jb is not None and jb.status == "queued", jb

        # Now grow the cap. b must start while a is still running.
        runner.update_runtime_config(concurrency=Concurrency(per_dock=2, global_cap=2))
        await _wait_status(store, rb.short_id, {"running"}, timeout=5.0)

        # At this moment both should be running concurrently.
        ja = await store.get_job_by_short_id(ra.short_id)
        jb = await store.get_job_by_short_id(rb.short_id)
        assert ja and ja.status == "running"
        assert jb and jb.status == "running"

        # Drain.
        for r in (ra, rb):
            await _wait_status(store, r.short_id, {"success", "failed"}, timeout=30.0)
        ja = await store.get_job_by_short_id(ra.short_id)
        jb = await store.get_job_by_short_id(rb.short_id)
        assert ja and ja.status == "success", ja
        assert jb and jb.status == "success", jb
    finally:
        await runner.stop()


async def test_cap_idle_resize_takes_effect_for_next_jobs(
    store, harbin_paths, fake_agent_cli, tmp_path, monkeypatch
) -> None:
    """Resizing the cap while idle takes effect for subsequent acquisitions.
    Start at cap=4 with no jobs in flight, shrink to cap=1, then enqueue
    two jobs across two fleets and confirm they serialize (only the
    global cap can serialize them — per-dock serial is not in play
    because the fleets are distinct)."""
    monkeypatch.setenv("HARBIN_FAKE_DURATION", "0.8")
    runner, dm, _arts = await _make_runner(
        store=store, harbin_paths=harbin_paths, fake_agent_cli=fake_agent_cli, global_cap=4
    )
    bare_a = _make_bare_fleet(tmp_path, "fleet-idle-a")
    bare_b = _make_bare_fleet(tmp_path, "fleet-idle-b")
    fa = await dm.register_fleet(str(bare_a))
    fb = await dm.register_fleet(str(bare_b))

    try:
        runner.update_runtime_config(concurrency=Concurrency(per_dock=2, global_cap=1))
        ra = await runner.enqueue(fleet=fa.row, prompt="x", source="repl", task_label="adhoc")
        rb = await runner.enqueue(fleet=fb.row, prompt="y", source="repl", task_label="adhoc")
        for r in (ra, rb):
            await _wait_status(store, r.short_id, {"success", "failed"}, timeout=30.0)
        ja = await store.get_job_by_short_id(ra.short_id)
        jb = await store.get_job_by_short_id(rb.short_id)
        assert ja and ja.status == "success"
        assert jb and jb.status == "success"
        # The two jobs ran on different fleets, so per-dock serial does
        # not gate them. The only thing that can serialize them is the
        # global cap of 1.
        first, second = sorted([ja, jb], key=lambda j: j.started_at or "")
        assert first.ended_at is not None and second.started_at is not None
        assert first.ended_at <= second.started_at, (
            f"cap=1 should have serialized two cross-fleet jobs: "
            f"first ended {first.ended_at}, second started {second.started_at}"
        )
    finally:
        await runner.stop()
