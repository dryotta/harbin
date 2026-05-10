"""Agent Runner (sub-spec 10).

Owns the job state machine. Spawns the agent CLI as a subprocess and
streams its stdio to:
  - an in-memory ring buffer (per job, capped),
  - the SQLite ``job_log_chunks`` table,
  - the per-job ``job.log`` file in the artifact dir.
"""

from __future__ import annotations

import asyncio
import collections
import datetime as _dt
import os
import signal
import sys
from collections.abc import Callable, Coroutine
from dataclasses import dataclass, field
from pathlib import Path
from typing import IO, TYPE_CHECKING

from harbin.config.models import AgentCli, Concurrency
from harbin.errors import RunnerError
from harbin.logging import get_logger
from harbin.runner.invocation import build as build_invocation

if TYPE_CHECKING:  # pragma: no cover
    from harbin.db.store import FleetRow, JobRow, Store
    from harbin.fleet.artifacts import ArtifactManager
    from harbin.fleet.dock import DockManager

_log = get_logger("runner")

_RING_CAP_BYTES = 4 * 1024 * 1024
_MAX_LINE_BYTES = 8 * 1024
_FLUSH_INTERVAL = 0.25
_FLUSH_BATCH = 32


@dataclass
class _LiveJob:
    job_id: int
    short_id: str
    fleet_id: int
    fleet_name: str
    task_label: str
    proc: asyncio.subprocess.Process | None = None
    cancel_requested: bool = False
    ring: collections.deque[tuple[str, str]] = field(default_factory=collections.deque)
    ring_size: int = 0
    log_file: IO[str] | None = None
    artifact_dir: Path | None = None
    started: bool = False
    queued_at: _dt.datetime = field(default_factory=lambda: _dt.datetime.now(_dt.UTC))


JobEvent = Callable[[str, dict[str, object]], Coroutine[object, object, None] | None]


@dataclass
class _Snapshot:
    """Config snapshot captured at queued→starting transition."""

    agent_cli: AgentCli
    kill_grace_seconds: int
    task_label: str
    concurrency: str  # 'serial' | 'parallel'


class AgentRunner:
    """Spawn and shepherd agent jobs.

    Concurrency model:
      * One asyncio.Queue per dock (created lazily).
      * One dispatch coroutine per dock pulls from its queue.
      * A global :class:`asyncio.Semaphore` caps total in-flight jobs.
    """

    def __init__(
        self,
        *,
        store: Store,
        artifacts: ArtifactManager,
        dock_manager: DockManager,
        agent_cli: AgentCli,
        concurrency: Concurrency,
        kill_grace_seconds: int,
        prompts_dir: Path,
        on_event: JobEvent | None = None,
    ) -> None:
        self._store = store
        self._artifacts = artifacts
        self._dock_manager = dock_manager
        self._global_agent_cli = agent_cli
        self._concurrency = concurrency
        self._kill_grace = kill_grace_seconds
        self._prompts_dir = prompts_dir
        self._on_event = on_event or (lambda kind, data: None)

        self._queues: dict[int, asyncio.Queue[tuple[int, str]]] = {}
        self._dispatchers: dict[int, asyncio.Task[None]] = {}
        self._global_sem = asyncio.Semaphore(concurrency.global_cap)
        self._dock_locks: dict[int, asyncio.Lock] = {}
        self._live: dict[int, _LiveJob] = {}
        self._stopping = False

    # ─────────────────────── public API ───────────────────────

    def update_runtime_config(
        self,
        *,
        agent_cli: AgentCli | None = None,
        concurrency: Concurrency | None = None,
        kill_grace_seconds: int | None = None,
    ) -> None:
        """Apply-live config changes for **new** jobs (sub-spec 03 §5.3)."""
        if agent_cli is not None:
            self._global_agent_cli = agent_cli
        if concurrency is not None:
            # Resize the global semaphore by replacing it. In-flight jobs hold
            # the old one; new acquisitions go to the new one. Acceptable
            # transient over/undershoot.
            if concurrency.global_cap != self._concurrency.global_cap:
                self._global_sem = asyncio.Semaphore(concurrency.global_cap)
            self._concurrency = concurrency
        if kill_grace_seconds is not None:
            self._kill_grace = kill_grace_seconds

    def live_jobs(self) -> list[int]:
        return list(self._live)

    async def enqueue(
        self,
        *,
        fleet: FleetRow,
        prompt: str,
        source: str,
        task_pk: int | None = None,
        task_label: str = "adhoc",
    ) -> JobRow:
        """Enqueue a new job. Returns the inserted :class:`JobRow`."""
        artifact_dir = self._artifacts.location_for(
            fleet_name=fleet.name, task_label=task_label, short_id="pending"
        )
        # We'll fix artifact_dir post-insert (it depends on short_id).
        row = await self._store.insert_job(
            fleet_id=fleet.id,
            task_pk=task_pk,
            prompt=prompt,
            source=source,
            artifact_dir=str(artifact_dir),
        )
        artifact_dir = self._artifacts.location_for(
            fleet_name=fleet.name, task_label=task_label, short_id=row.short_id
        )
        await self._store.update_job_artifact_dir(row.id, str(artifact_dir))
        refreshed = await self._store.get_job(row.id)
        assert refreshed is not None
        row = refreshed

        await self._emit("job_queued", {"job_id": row.id, "short_id": row.short_id})
        queue = self._queue_for(fleet.id)
        await queue.put((row.id, task_label))
        # Ensure a dispatcher is running for this dock.
        if fleet.id not in self._dispatchers:
            self._dispatchers[fleet.id] = asyncio.create_task(
                self._dispatch_loop(fleet.id),
                name=f"runner.dispatch:{fleet.name}",
            )
        return row

    async def cancel(self, short_id: str) -> tuple[bool, str]:
        """Cancel a job by short_id. Returns (ok, message)."""
        job = await self._store.get_job_by_short_id(short_id)
        if job is None:
            return False, f"no such job: {short_id}"
        if job.status == "queued":
            await self._store.set_job_status(job.id, "cancelled", ended=True)
            await self._emit(
                "job_ended",
                {
                    "job_id": job.id,
                    "short_id": short_id,
                    "status": "cancelled",
                },
            )
            return True, f"cancelled queued job #{short_id}"
        if job.status in {"starting", "running"}:
            live = self._live.get(job.id)
            if live is None:
                # Job is in DB as running but we don't track it (crash/reboot).
                await self._store.set_job_status(job.id, "cancelled", ended=True)
                return True, f"marked #{short_id} cancelled (no live process)"
            live.cancel_requested = True
            await self._terminate(live)
            return True, f"cancelling #{short_id}…"
        return False, f"job #{short_id} already {job.status}"

    # ─────────────────────── dispatch loop ────────────────────────

    def _queue_for(self, fleet_id: int) -> asyncio.Queue[tuple[int, str]]:
        q = self._queues.get(fleet_id)
        if q is None:
            q = asyncio.Queue()
            self._queues[fleet_id] = q
        return q

    def _lock_for(self, fleet_id: int) -> asyncio.Lock:
        lock = self._dock_locks.get(fleet_id)
        if lock is None:
            lock = asyncio.Lock()
            self._dock_locks[fleet_id] = lock
        return lock

    async def _dispatch_loop(self, fleet_id: int) -> None:
        queue = self._queue_for(fleet_id)
        while not self._stopping:
            try:
                job_id, task_label = await queue.get()
            except asyncio.CancelledError:
                return
            try:
                await self._run_job(fleet_id, job_id, task_label)
            except Exception:
                _log.exception("job %d crashed in dispatch", job_id)
            finally:
                queue.task_done()

    async def _resolve_snapshot(self, fleet_id: int, task_label: str) -> _Snapshot:
        state = self._dock_manager.states.get(fleet_id)
        cli = self._global_agent_cli
        if state is not None and state.fleet_config is not None:
            if state.fleet_config.agent_cli is not None:
                cli = state.fleet_config.agent_cli
        concurrency = "serial"
        if state is not None and state.schedule_config is not None and task_label != "adhoc":
            for t in state.schedule_config.tasks:
                if t.id == task_label:
                    concurrency = t.concurrency
                    break
        return _Snapshot(
            agent_cli=cli,
            kill_grace_seconds=self._kill_grace,
            task_label=task_label,
            concurrency=concurrency,
        )

    async def _run_job(self, fleet_id: int, job_id: int, task_label: str) -> None:
        job = await self._store.get_job(job_id)
        if job is None or job.status != "queued":
            return
        fleet = await self._store.get_fleet(fleet_id)
        if fleet is None:
            await self._store.set_job_status(job_id, "failed", ended=True)
            return

        snap = await self._resolve_snapshot(fleet_id, task_label)
        await self._global_sem.acquire()
        lock = self._lock_for(fleet_id) if snap.concurrency == "serial" else None
        try:
            if lock is not None:
                await lock.acquire()
            try:
                await self._spawn_and_run(fleet, job, snap)
            finally:
                if lock is not None:
                    lock.release()
        finally:
            self._global_sem.release()

    async def _spawn_and_run(self, fleet: FleetRow, job: JobRow, snap: _Snapshot) -> None:
        await self._store.set_job_status(job.id, "starting")
        live = _LiveJob(
            job_id=job.id,
            short_id=job.short_id,
            fleet_id=fleet.id,
            fleet_name=fleet.name,
            task_label=snap.task_label,
        )
        self._live[job.id] = live
        try:
            artifact_dir = await self._artifacts.prepare(
                fleet_name=fleet.name,
                task_label=snap.task_label,
                short_id=job.short_id,
            )
            live.artifact_dir = artifact_dir

            try:
                inv = build_invocation(
                    snap.agent_cli,
                    job.prompt,
                    prompts_dir=self._prompts_dir,
                    short_id=job.short_id,
                )
            except RunnerError as e:
                _log.warning("job %s: %s", job.short_id, e)
                await self._record_log(live, "system", f"invocation error: {e.message}\n")
                await self._store.set_job_status(job.id, "failed", ended=True)
                await self._emit_job_ended(live, "failed", None)
                return

            env = os.environ.copy()
            env.update(
                {
                    "HARBIN_ARTIFACT_DIR": str(artifact_dir),
                    "HARBIN_FLEET": fleet.name,
                    "HARBIN_TASK_ID": snap.task_label,
                    "HARBIN_JOB_ID": job.short_id,
                    "HARBIN_PROMPT": job.prompt,
                }
            )

            stdin_setting: int = (
                asyncio.subprocess.PIPE
                if snap.agent_cli.mode == "stdin"
                else asyncio.subprocess.DEVNULL
            )

            try:
                if sys.platform == "win32":
                    proc = await asyncio.create_subprocess_exec(
                        *inv.argv,
                        cwd=fleet.dock_path,
                        env=env,
                        stdin=stdin_setting,
                        stdout=asyncio.subprocess.PIPE,
                        stderr=asyncio.subprocess.PIPE,
                        creationflags=0x00000200,  # CREATE_NEW_PROCESS_GROUP
                    )
                else:
                    proc = await asyncio.create_subprocess_exec(
                        *inv.argv,
                        cwd=fleet.dock_path,
                        env=env,
                        stdin=stdin_setting,
                        stdout=asyncio.subprocess.PIPE,
                        stderr=asyncio.subprocess.PIPE,
                        start_new_session=True,
                    )
            except FileNotFoundError as e:
                msg = f"agent binary not found: {inv.argv[0]} ({e})"
                _log.warning("job %s spawn failed: %s", job.short_id, msg)
                await self._record_log(live, "system", msg + "\n")
                await self._store.set_job_status(job.id, "failed", ended=True)
                await self._emit_job_ended(live, "failed", None)
                if inv.cleanup_path and inv.cleanup_path.exists():
                    inv.cleanup_path.unlink(missing_ok=True)
                return

            live.proc = proc
            live.log_file = (artifact_dir / "job.log").open("a", encoding="utf-8", errors="replace")
            live.started = True
            await self._record_log(
                live,
                "system",
                f"START job {job.short_id} at {_iso_now()}\n",
            )
            await self._store.set_job_status(job.id, "running", started=True)
            await self._emit("job_started", {"job_id": job.id, "short_id": job.short_id})

            # Pipe prompt to stdin if needed
            stdin_task: asyncio.Task[None] | None = None
            should_pipe_stdin = (
                snap.agent_cli.mode == "stdin"
                and inv.stdin_text is not None
                and proc.stdin is not None
            )
            if should_pipe_stdin:
                assert inv.stdin_text is not None
                stdin_task = asyncio.create_task(
                    self._write_stdin(proc, inv.stdin_text),
                    name=f"runner.stdin:{job.short_id}",
                )

            reader_tasks = [
                asyncio.create_task(self._reader(live, proc.stdout, "stdout")),
                asyncio.create_task(self._reader(live, proc.stderr, "stderr")),
            ]

            try:
                rc = await proc.wait()
            except asyncio.CancelledError:
                rc = -1
                raise
            finally:
                for t in reader_tasks:
                    try:
                        await t
                    except Exception:
                        pass
                if stdin_task is not None:
                    try:
                        await stdin_task
                    except Exception:
                        pass
                if inv.cleanup_path and inv.cleanup_path.exists():
                    inv.cleanup_path.unlink(missing_ok=True)
                if live.log_file is not None:
                    try:
                        live.log_file.close()
                    except Exception:
                        pass
                    live.log_file = None

            if live.cancel_requested:
                status = "cancelled"
            elif rc == 0:
                status = "success"
            else:
                status = "failed"
            await self._record_log(
                live,
                "system",
                f"EXIT code={rc}\n",
            )
            await self._store.set_job_status(job.id, status, exit_code=rc, ended=True)
            await self._emit_job_ended(live, status, rc)

            # Post-run: push-back on success
            if status == "success":
                state = self._dock_manager.states.get(fleet.id)
                if state is not None and live.artifact_dir is not None:
                    warning = await self._dock_manager.push_back(
                        state=state,
                        artifact_dir=live.artifact_dir,
                        short_id=job.short_id,
                        task_label=snap.task_label,
                        prompt=job.prompt,
                    )
                    if warning:
                        _log.warning("push-back: %s", warning)
                        await self._record_log(live, "system", warning + "\n")

            await self._artifacts.finalize(job.id)
        finally:
            self._live.pop(job.id, None)

    async def _write_stdin(self, proc: asyncio.subprocess.Process, text: str) -> None:
        if proc.stdin is None:
            return
        try:
            proc.stdin.write(text.encode("utf-8"))
            await proc.stdin.drain()
            proc.stdin.close()
            try:
                await proc.stdin.wait_closed()
            except Exception:
                pass
        except BrokenPipeError, ConnectionResetError:
            pass

    async def _reader(
        self,
        live: _LiveJob,
        stream: asyncio.StreamReader | None,
        kind: str,
    ) -> None:
        if stream is None:
            return
        buffer = bytearray()
        pending: list[tuple[str, str]] = []
        last_flush = asyncio.get_event_loop().time()
        while True:
            try:
                chunk = await stream.read(_MAX_LINE_BYTES)
            except Exception:
                break
            if not chunk:
                # Final flush of any leftover buffer as a line
                if buffer:
                    pending.append((kind, buffer.decode("utf-8", errors="replace")))
                    buffer.clear()
                if pending:
                    await self._flush(live, pending)
                    pending.clear()
                break
            buffer.extend(chunk)
            while True:
                idx = buffer.find(b"\n")
                if idx < 0:
                    if len(buffer) >= _MAX_LINE_BYTES:
                        pending.append(
                            (kind, buffer[:_MAX_LINE_BYTES].decode("utf-8", errors="replace"))
                        )
                        del buffer[:_MAX_LINE_BYTES]
                        continue
                    break
                line = buffer[: idx + 1].decode("utf-8", errors="replace")
                del buffer[: idx + 1]
                pending.append((kind, line))
            now = asyncio.get_event_loop().time()
            if len(pending) >= _FLUSH_BATCH or (now - last_flush) >= _FLUSH_INTERVAL:
                if pending:
                    await self._flush(live, pending)
                    pending.clear()
                last_flush = now

    async def _flush(self, live: _LiveJob, items: list[tuple[str, str]]) -> None:
        for kind, text in items:
            # ring buffer
            live.ring.append((kind, text))
            live.ring_size += len(text)
            while live.ring_size > _RING_CAP_BYTES and live.ring:
                _, dropped = live.ring.popleft()
                live.ring_size -= len(dropped)
            # job.log
            if live.log_file is not None:
                try:
                    live.log_file.write(text)
                except Exception:  # pragma: no cover
                    pass
        try:
            await self._store.append_log_chunks(live.job_id, items)
        except Exception:
            _log.exception("DB log append failed for %s", live.short_id)
        # emit a non-awaited notification so the TUI refreshes
        try:
            self._on_event(
                "job_log",
                {"job_id": live.job_id, "short_id": live.short_id, "n": len(items)},
            )
        except Exception:
            pass

    async def _record_log(self, live: _LiveJob, kind: str, text: str) -> None:
        await self._flush(live, [(kind, text)])

    # ───────────────────────── cancellation ────────────────────────────

    async def _terminate(self, live: _LiveJob) -> None:
        proc = live.proc
        if proc is None or proc.returncode is not None:
            return
        # Pre-flight stash inside the dock for forensic preservation
        state = self._dock_manager.states.get(live.fleet_id)
        if state is not None:
            try:
                from harbin.fleet.dock import _git as _git_call

                await _git_call(
                    "stash",
                    "push",
                    "-u",
                    "-m",
                    f"harbin cancel {live.short_id}",
                    cwd=Path(state.row.dock_path),
                    timeout=10,
                )
            except Exception:
                _log.debug("pre-cancel stash failed; ignoring", exc_info=True)
        try:
            if sys.platform == "win32":
                proc.send_signal(signal.CTRL_BREAK_EVENT)
            else:
                try:
                    os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
                except ProcessLookupError, PermissionError:
                    proc.terminate()
        except Exception:
            _log.warning("SIGTERM failed for %s", live.short_id, exc_info=True)

        try:
            await asyncio.wait_for(proc.wait(), timeout=self._kill_grace)
        except TimeoutError:
            try:
                if sys.platform == "win32":
                    proc.kill()
                else:
                    try:
                        os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
                    except ProcessLookupError, PermissionError:
                        proc.kill()
            except Exception:
                _log.warning("SIGKILL failed for %s", live.short_id, exc_info=True)

    # ─────────────────────────── shutdown ──────────────────────────────

    async def stop(self) -> None:
        self._stopping = True
        # Cancel each live job (sends SIGTERM, waits grace, then SIGKILLs).
        cancellations = [
            asyncio.create_task(self._terminate(live)) for live in list(self._live.values())
        ]
        for c in cancellations:
            try:
                await c
            except Exception:
                pass
        for task in list(self._dispatchers.values()):
            task.cancel()
        for task in list(self._dispatchers.values()):
            try:
                await task
            except asyncio.CancelledError, Exception:
                pass
        self._dispatchers.clear()

    # ─────────────────────────── helpers ───────────────────────────────

    async def _emit_job_ended(self, live: _LiveJob, status: str, rc: int | None) -> None:
        await self._emit(
            "job_ended",
            {
                "job_id": live.job_id,
                "short_id": live.short_id,
                "status": status,
                "exit_code": rc,
            },
        )

    async def _emit(self, kind: str, data: dict[str, object]) -> None:
        try:
            result = self._on_event(kind, data)
            if asyncio.iscoroutine(result):
                await result
        except Exception:
            _log.exception("event handler failed for %s", kind)


def _iso_now() -> str:
    return _dt.datetime.now(_dt.UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
