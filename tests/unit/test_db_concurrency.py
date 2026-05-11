"""Tests for the new Store concurrency + reap behaviors (B3, reap_orphan)."""

from __future__ import annotations

import asyncio

import pytest

pytestmark = pytest.mark.asyncio


async def test_concurrent_log_appends_no_seq_collision(store) -> None:
    """B3 regression: per-job appender lock keeps SELECT MAX(seq) + INSERT
    serial, so concurrent stdout/stderr/system writers never collide."""
    fleet = await store.insert_fleet(name="news", url="x", dock_path="/tmp/x")
    job = await store.insert_job(
        fleet_id=fleet.id,
        task_pk=None,
        prompt="hi",
        source="repl",
        artifact_dir="/tmp/a",
    )

    async def writer(stream: str, lines: int) -> None:
        for i in range(lines):
            await store.append_log_chunks(job.id, [(stream, f"{stream}-{i}\n")])

    await asyncio.gather(
        writer("stdout", 20),
        writer("stderr", 20),
        writer("system", 5),
    )
    chunks = await store.tail_log_chunks(job.id, n=1000)
    seqs = [c.seq for c in chunks]
    # Every seq must be unique. ORDER BY seq DESC + reverse = ascending.
    assert seqs == sorted(seqs)
    assert len(seqs) == len(set(seqs))
    assert len(seqs) == 20 + 20 + 5


async def test_reap_orphan_running(store) -> None:
    """reap_orphan_running flips queued/starting/running rows to failed."""
    fleet = await store.insert_fleet(name="news", url="x", dock_path="/tmp/x")
    j1 = await store.insert_job(
        fleet_id=fleet.id,
        task_pk=None,
        prompt="q",
        source="repl",
        artifact_dir="/tmp/a",
    )  # 'queued'
    j2 = await store.insert_job(
        fleet_id=fleet.id,
        task_pk=None,
        prompt="r",
        source="repl",
        artifact_dir="/tmp/b",
    )
    await store.set_job_status(j2.id, "running", started=True)
    j3 = await store.insert_job(
        fleet_id=fleet.id,
        task_pk=None,
        prompt="ok",
        source="repl",
        artifact_dir="/tmp/c",
    )
    await store.set_job_status(j3.id, "success", exit_code=0, ended=True)

    n = await store.reap_orphan_running()
    assert n == 2

    out1 = await store.get_job(j1.id)
    out2 = await store.get_job(j2.id)
    out3 = await store.get_job(j3.id)
    assert out1 is not None and out1.status == "failed"
    assert out2 is not None and out2.status == "failed"
    assert out3 is not None and out3.status == "success"
