"""aiosqlite-backed state store (sub-spec 02).

One process-wide connection, accessed only from the event loop.
Typed dataclass rows; no ORM.
"""

from __future__ import annotations

import asyncio
import datetime as _dt
import secrets
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path

import aiosqlite

from harbin.db import migrate
from harbin.logging import get_logger

_log = get_logger("db.store")

_LOG_CHUNK_CAP_BYTES = 4 * 1024 * 1024  # 4 MiB per job


def _iso_utc(dt: _dt.datetime | None = None) -> str:
    dt = dt or _dt.datetime.now(_dt.UTC)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=_dt.UTC)
    return dt.astimezone(_dt.UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


# ─────────────────────────────── row types ────────────────────────────────


@dataclass(frozen=True)
class FleetRow:
    id: int
    name: str
    url: str
    dock_path: str
    registered_at: str


@dataclass(frozen=True)
class TaskRow:
    id: int
    fleet_id: int
    task_id: str
    cron: str
    prompt: str
    source_sha: str
    registered_at: str


@dataclass(frozen=True)
class JobRow:
    id: int
    short_id: str
    fleet_id: int
    task_pk: int | None
    prompt: str
    source: str
    status: str
    exit_code: int | None
    started_at: str | None
    ended_at: str | None
    artifact_dir: str


@dataclass(frozen=True)
class LogChunk:
    seq: int
    ts: str
    stream: str
    text: str


# ─────────────────────────────── store ────────────────────────────────────


class Store:
    """Wraps a single aiosqlite connection. Open via :meth:`open`."""

    def __init__(self, conn: aiosqlite.Connection) -> None:
        self._conn = conn
        # Per-job locks for append_log_chunks: serialize the
        # SELECT MAX(seq) + INSERT … against concurrent writers from the
        # same job (stdout, stderr, and system records overlap).
        self._log_locks: dict[int, asyncio.Lock] = {}

    @classmethod
    async def open(cls, path: Path) -> Store:
        path.parent.mkdir(parents=True, exist_ok=True)
        conn = await aiosqlite.connect(str(path))
        await conn.execute("PRAGMA journal_mode = WAL")
        await conn.execute("PRAGMA synchronous  = NORMAL")
        await conn.execute("PRAGMA foreign_keys = ON")
        await conn.execute("PRAGMA busy_timeout = 5000")
        await migrate.run(conn)
        return cls(conn)

    async def close(self) -> None:
        await self._conn.close()

    # ───────────────────── app_state ──────────────────────

    async def app_state_get(self, key: str) -> str | None:
        async with self._conn.execute("SELECT value FROM app_state WHERE key=?", (key,)) as cur:
            row = await cur.fetchone()
            return None if row is None else str(row[0])

    async def app_state_set(self, key: str, value: str) -> None:
        await self._conn.execute(
            "INSERT OR REPLACE INTO app_state(key,value) VALUES(?,?)",
            (key, value),
        )
        await self._conn.commit()

    # ─────────────────────── fleets ───────────────────────

    async def insert_fleet(self, name: str, url: str, dock_path: str) -> FleetRow:
        await self._conn.execute(
            "INSERT INTO fleets(name,url,dock_path) VALUES(?,?,?)",
            (name, url, dock_path),
        )
        await self._conn.commit()
        row = await self.get_fleet_by_name(name)
        assert row is not None
        return row

    async def get_fleet_by_name(self, name: str) -> FleetRow | None:
        async with self._conn.execute(
            "SELECT id,name,url,dock_path,registered_at FROM fleets WHERE name=?",
            (name,),
        ) as cur:
            r = await cur.fetchone()
        return None if r is None else FleetRow(*r)

    async def get_fleet(self, fleet_id: int) -> FleetRow | None:
        async with self._conn.execute(
            "SELECT id,name,url,dock_path,registered_at FROM fleets WHERE id=?",
            (fleet_id,),
        ) as cur:
            r = await cur.fetchone()
        return None if r is None else FleetRow(*r)

    async def list_fleets(self) -> list[FleetRow]:
        async with self._conn.execute(
            "SELECT id,name,url,dock_path,registered_at FROM fleets ORDER BY name"
        ) as cur:
            rows = await cur.fetchall()
        return [FleetRow(*r) for r in rows]

    async def delete_fleet(self, fleet_id: int) -> None:
        await self._conn.execute("DELETE FROM fleets WHERE id=?", (fleet_id,))
        await self._conn.commit()

    # ──────────────────────── tasks ───────────────────────

    async def list_tasks_for_fleet(self, fleet_id: int) -> list[TaskRow]:
        async with self._conn.execute(
            "SELECT id,fleet_id,task_id,cron,prompt,source_sha,registered_at "
            "FROM tasks WHERE fleet_id=? ORDER BY task_id",
            (fleet_id,),
        ) as cur:
            rows = await cur.fetchall()
        return [TaskRow(*r) for r in rows]

    async def list_tasks(self) -> list[TaskRow]:
        async with self._conn.execute(
            "SELECT id,fleet_id,task_id,cron,prompt,source_sha,registered_at "
            "FROM tasks ORDER BY fleet_id, task_id"
        ) as cur:
            rows = await cur.fetchall()
        return [TaskRow(*r) for r in rows]

    async def upsert_task(
        self,
        fleet_id: int,
        task_id: str,
        cron: str,
        prompt: str,
        source_sha: str,
    ) -> TaskRow:
        await self._conn.execute(
            """
            INSERT INTO tasks(fleet_id,task_id,cron,prompt,source_sha)
            VALUES(?,?,?,?,?)
            ON CONFLICT(fleet_id, task_id) DO UPDATE SET
              cron=excluded.cron,
              prompt=excluded.prompt,
              source_sha=excluded.source_sha
            """,
            (fleet_id, task_id, cron, prompt, source_sha),
        )
        await self._conn.commit()
        async with self._conn.execute(
            "SELECT id,fleet_id,task_id,cron,prompt,source_sha,registered_at "
            "FROM tasks WHERE fleet_id=? AND task_id=?",
            (fleet_id, task_id),
        ) as cur:
            r = await cur.fetchone()
        assert r is not None
        return TaskRow(*r)

    async def delete_task(self, task_pk: int) -> None:
        await self._conn.execute("DELETE FROM tasks WHERE id=?", (task_pk,))
        await self._conn.commit()

    # ───────────────────── schedule state ─────────────────

    async def get_last_fire(self, task_pk: int) -> str | None:
        async with self._conn.execute(
            "SELECT last_fire_ts FROM schedule_state WHERE task_pk=?",
            (task_pk,),
        ) as cur:
            r = await cur.fetchone()
        return None if r is None else str(r[0])

    async def upsert_last_fire(self, task_pk: int, when: _dt.datetime) -> None:
        await self._conn.execute(
            """
            INSERT INTO schedule_state(task_pk,last_fire_ts) VALUES(?,?)
            ON CONFLICT(task_pk) DO UPDATE SET last_fire_ts=excluded.last_fire_ts
            """,
            (task_pk, _iso_utc(when)),
        )
        await self._conn.commit()

    # ────────────────────────── jobs ──────────────────────

    @staticmethod
    def _make_short_id() -> str:
        return secrets.token_hex(3)

    async def insert_job(
        self,
        *,
        fleet_id: int,
        task_pk: int | None,
        prompt: str,
        source: str,
        artifact_dir: str,
    ) -> JobRow:
        # Generate unique short_id (retry on rare collision).
        for _ in range(8):
            short_id = self._make_short_id()
            try:
                await self._conn.execute(
                    """
                    INSERT INTO jobs(short_id,fleet_id,task_pk,prompt,source,
                                     status,artifact_dir)
                    VALUES(?,?,?,?,?,?,?)
                    """,
                    (short_id, fleet_id, task_pk, prompt, source, "queued", artifact_dir),
                )
                await self._conn.commit()
                row = await self.get_job_by_short_id(short_id)
                assert row is not None
                return row
            except aiosqlite.IntegrityError:
                continue
        raise RuntimeError("could not generate unique short_id after 8 attempts")

    async def get_job(self, job_id: int) -> JobRow | None:
        async with self._conn.execute(self._JOB_SELECT + " WHERE id=?", (job_id,)) as cur:
            r = await cur.fetchone()
        return None if r is None else JobRow(*r)

    async def get_job_by_short_id(self, short_id: str) -> JobRow | None:
        async with self._conn.execute(self._JOB_SELECT + " WHERE short_id=?", (short_id,)) as cur:
            r = await cur.fetchone()
        return None if r is None else JobRow(*r)

    _JOB_SELECT = (
        "SELECT id,short_id,fleet_id,task_pk,prompt,source,status,"
        "exit_code,started_at,ended_at,artifact_dir FROM jobs"
    )

    async def list_recent_jobs(
        self, *, since_seconds: int = 86400 * 7, limit: int = 50
    ) -> list[JobRow]:
        since = _iso_utc(_dt.datetime.now(_dt.UTC) - _dt.timedelta(seconds=since_seconds))
        async with self._conn.execute(
            self._JOB_SELECT + " WHERE status IN ('queued','starting','running') OR ended_at > ? "
            "ORDER BY COALESCE(started_at, ended_at) DESC LIMIT ?",
            (since, limit),
        ) as cur:
            rows = await cur.fetchall()
        return [JobRow(*r) for r in rows]

    async def list_active_jobs(self) -> list[JobRow]:
        async with self._conn.execute(
            self._JOB_SELECT + " WHERE status IN ('queued','starting','running') ORDER BY id"
        ) as cur:
            rows = await cur.fetchall()
        return [JobRow(*r) for r in rows]

    async def list_all_jobs(self, *, limit: int = 200) -> list[JobRow]:
        async with self._conn.execute(
            self._JOB_SELECT + " WHERE status != 'archived' ORDER BY id DESC LIMIT ?",
            (limit,),
        ) as cur:
            rows = await cur.fetchall()
        return [JobRow(*r) for r in rows]

    async def list_jobs_for_retention(self, fleet_id: int, cutoff: _dt.datetime) -> list[JobRow]:
        async with self._conn.execute(
            self._JOB_SELECT + " WHERE fleet_id=? AND status IN ('success','failed','cancelled') "
            "AND ended_at < ?",
            (fleet_id, _iso_utc(cutoff)),
        ) as cur:
            rows = await cur.fetchall()
        return [JobRow(*r) for r in rows]

    async def set_job_status(
        self,
        job_id: int,
        status: str,
        *,
        exit_code: int | None = None,
        started: bool = False,
        ended: bool = False,
    ) -> None:
        fields = ["status=?"]
        values: list[object] = [status]
        if started:
            fields.append("started_at=?")
            values.append(_iso_utc())
        if ended:
            fields.append("ended_at=?")
            values.append(_iso_utc())
        if exit_code is not None:
            fields.append("exit_code=?")
            values.append(exit_code)
        values.append(job_id)
        await self._conn.execute(
            f"UPDATE jobs SET {', '.join(fields)} WHERE id=?",
            values,
        )
        await self._conn.commit()

    async def archive_jobs(self, job_ids: Sequence[int]) -> None:
        if not job_ids:
            return
        qmarks = ",".join("?" * len(job_ids))
        await self._conn.execute(
            f"DELETE FROM job_log_chunks WHERE job_id IN ({qmarks})", tuple(job_ids)
        )
        await self._conn.execute(
            f"UPDATE jobs SET status='archived' WHERE id IN ({qmarks})", tuple(job_ids)
        )
        await self._conn.commit()

    # ──────────────────────── log chunks ──────────────────

    def _log_lock(self, job_id: int) -> asyncio.Lock:
        lock = self._log_locks.get(job_id)
        if lock is None:
            lock = asyncio.Lock()
            self._log_locks[job_id] = lock
        return lock

    async def append_log_chunks(self, job_id: int, items: Iterable[tuple[str, str]]) -> None:
        """``items`` is iterable of ``(stream, text)`` tuples.

        Serialized per-job via :meth:`_log_lock` to avoid TOCTOU on
        ``MAX(seq)`` when multiple appenders (stdout reader, stderr reader,
        and system records) run concurrently for the same job.
        """
        items = list(items)
        if not items:
            return
        async with self._log_lock(job_id):
            async with self._conn.execute(
                "SELECT COALESCE(MAX(seq),-1) FROM job_log_chunks WHERE job_id=?",
                (job_id,),
            ) as cur:
                r = await cur.fetchone()
                next_seq = int(r[0]) + 1 if r and r[0] is not None else 0
            now = _iso_utc()
            rows = [
                (job_id, next_seq + i, now, stream, text) for i, (stream, text) in enumerate(items)
            ]
            await self._conn.executemany(
                "INSERT INTO job_log_chunks(job_id,seq,ts,stream,text) VALUES(?,?,?,?,?)",
                rows,
            )
            await self._enforce_cap(job_id)
            await self._conn.commit()

    def release_log_lock(self, job_id: int) -> None:
        """Drop the per-job log lock after a job ends to bound memory."""
        self._log_locks.pop(job_id, None)

    async def _enforce_cap(self, job_id: int) -> None:
        async with self._conn.execute(
            "SELECT COALESCE(SUM(LENGTH(text)),0) FROM job_log_chunks WHERE job_id=?",
            (job_id,),
        ) as cur:
            r = await cur.fetchone()
            total = int(r[0]) if r else 0
        if total <= _LOG_CHUNK_CAP_BYTES:
            return
        truncated = False
        while total > _LOG_CHUNK_CAP_BYTES:
            async with self._conn.execute(
                "SELECT id, LENGTH(text) FROM job_log_chunks WHERE job_id=? "
                "ORDER BY seq ASC LIMIT 32",
                (job_id,),
            ) as cur:
                batch = await cur.fetchall()
            if not batch:
                break
            for cid, length in batch:
                await self._conn.execute("DELETE FROM job_log_chunks WHERE id=?", (cid,))
                total -= int(length)
                truncated = True
                if total <= _LOG_CHUNK_CAP_BYTES:
                    break
        if truncated:
            # Insert one synthetic system line announcing the truncation
            async with self._conn.execute(
                "SELECT COALESCE(MAX(seq),-1) FROM job_log_chunks WHERE job_id=?",
                (job_id,),
            ) as cur:
                r2 = await cur.fetchone()
                seq = int(r2[0]) + 1 if r2 and r2[0] is not None else 0
            await self._conn.execute(
                "INSERT INTO job_log_chunks(job_id,seq,ts,stream,text) VALUES(?,?,?,?,?)",
                (
                    job_id,
                    seq,
                    _iso_utc(),
                    "system",
                    "earlier lines truncated; see job.log",
                ),
            )

    async def tail_log_chunks(self, job_id: int, n: int = 200) -> list[LogChunk]:
        async with self._conn.execute(
            "SELECT seq,ts,stream,text FROM job_log_chunks "
            "WHERE job_id=? ORDER BY seq DESC LIMIT ?",
            (job_id, n),
        ) as cur:
            rows = await cur.fetchall()
        ordered = list(rows)
        ordered.reverse()
        return [LogChunk(seq=r[0], ts=r[1], stream=r[2], text=r[3]) for r in ordered]

    async def reap_orphan_running(self) -> int:
        """Mark any 'queued'/'starting'/'running' rows as 'failed' at startup.

        These rows exist because harbin crashed or was killed before the
        runner could finalize them (sub-spec 02 §4 — log-chunk ring startup
        sweep). Returns the number of rows reaped.
        """
        await self._conn.execute(
            "UPDATE jobs SET status='failed', exit_code=COALESCE(exit_code, -1), "
            "ended_at=COALESCE(ended_at, ?) "
            "WHERE status IN ('queued','starting','running')",
            (_iso_utc(),),
        )
        async with self._conn.execute("SELECT changes()") as cur:
            r = await cur.fetchone()
            n = int(r[0]) if r and r[0] is not None else 0
        await self._conn.commit()
        return n

    # ───────────────────────── vacuum ─────────────────────

    async def update_job_artifact_dir(self, job_id: int, artifact_dir: str) -> None:
        await self._conn.execute(
            "UPDATE jobs SET artifact_dir=? WHERE id=?", (artifact_dir, job_id)
        )
        await self._conn.commit()

    async def maybe_vacuum(self) -> None:
        last = await self.app_state_get("last_vacuum_at")
        now = _dt.datetime.now(_dt.UTC)
        if last:
            try:
                last_dt = _dt.datetime.fromisoformat(last.replace("Z", "+00:00"))
                if (now - last_dt) < _dt.timedelta(days=30):
                    return
            except ValueError:
                pass
        await self._conn.execute("VACUUM")
        await self.app_state_set("last_vacuum_at", _iso_utc(now))
