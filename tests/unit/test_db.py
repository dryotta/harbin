"""Tests for harbin.db.store."""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.asyncio


async def test_migrate_and_insert(store) -> None:
    # initial schema_version is "1"
    v = await store.app_state_get("schema_version")
    assert v == "1"


async def test_fleets_crud(store) -> None:
    row = await store.insert_fleet(name="news", url="https://x/news", dock_path="/tmp/news")
    assert row.id > 0
    assert row.name == "news"
    fetched = await store.get_fleet_by_name("news")
    assert fetched is not None
    assert fetched.id == row.id


async def test_jobs_lifecycle(store) -> None:
    fleet = await store.insert_fleet(name="news", url="https://x/news", dock_path="/tmp/news")
    job = await store.insert_job(
        fleet_id=fleet.id,
        task_pk=None,
        prompt="hi",
        source="repl",
        artifact_dir="/tmp/a",
    )
    assert job.status == "queued"
    assert len(job.short_id) == 6
    await store.set_job_status(job.id, "starting")
    await store.set_job_status(job.id, "running", started=True)
    await store.set_job_status(job.id, "success", exit_code=0, ended=True)
    finished = await store.get_job(job.id)
    assert finished is not None
    assert finished.status == "success"
    assert finished.exit_code == 0
    assert finished.started_at is not None
    assert finished.ended_at is not None


async def test_log_chunks(store) -> None:
    fleet = await store.insert_fleet(name="news", url="x", dock_path="/tmp/x")
    job = await store.insert_job(
        fleet_id=fleet.id,
        task_pk=None,
        prompt="hi",
        source="repl",
        artifact_dir="/tmp/a",
    )
    await store.append_log_chunks(job.id, [("stdout", "line1\n"), ("stderr", "line2\n")])
    chunks = await store.tail_log_chunks(job.id, n=10)
    assert len(chunks) == 2
    assert chunks[0].text == "line1\n"
    assert chunks[1].stream == "stderr"


async def test_log_chunk_eviction(store) -> None:
    fleet = await store.insert_fleet(name="news", url="x", dock_path="/tmp/x")
    job = await store.insert_job(
        fleet_id=fleet.id,
        task_pk=None,
        prompt="hi",
        source="repl",
        artifact_dir="/tmp/a",
    )
    # Exceed the 4 MiB cap (~5 MiB of text)
    big = "x" * 1024  # 1 KiB
    batches = [("stdout", big) for _ in range(5500)]
    await store.append_log_chunks(job.id, batches)
    chunks = await store.tail_log_chunks(job.id, n=10000)
    total = sum(len(c.text) for c in chunks)
    # Cap is enforced before the synthetic truncation marker is added, so
    # the final total may exceed the cap by exactly the marker length.
    assert total <= 4 * 1024 * 1024 + 128
    assert any(c.stream == "system" and "truncated" in c.text for c in chunks)
