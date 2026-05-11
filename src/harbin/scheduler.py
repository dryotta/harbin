"""Scheduler (sub-spec 09)."""

from __future__ import annotations

import asyncio
import datetime as _dt
from collections.abc import Callable
from typing import TYPE_CHECKING
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from croniter import croniter

from harbin.fleet.models import TaskSpec
from harbin.logging import get_logger

if TYPE_CHECKING:  # pragma: no cover
    from harbin.db.store import Store, TaskRow
    from harbin.fleet.dock import DockManager, DockState
    from harbin.runner.runner import AgentRunner

_log = get_logger("scheduler")


def resolve_tz(name: str) -> _dt.tzinfo:
    if name == "system" or not name:
        local = _dt.datetime.now().astimezone().tzinfo
        return local or _dt.UTC
    try:
        return ZoneInfo(name)
    except ZoneInfoNotFoundError:
        _log.warning("unknown timezone %r; falling back to UTC", name)
        return _dt.UTC


class Scheduler:
    """In-process cron scheduler with diff-based hot reload."""

    def __init__(
        self,
        *,
        store: Store,
        dock_manager: DockManager,
        runner: AgentRunner,
        tick_seconds: int = 5,
        timezone_name: str = "system",
        clock: Callable[[], _dt.datetime] | None = None,
    ) -> None:
        self._store = store
        self._dock_manager = dock_manager
        self._runner = runner
        self._tick_seconds = tick_seconds
        self._tz = resolve_tz(timezone_name)
        self._clock = clock or (lambda: _dt.datetime.now(self._tz))
        self._task: asyncio.Task[None] | None = None
        self._stopping = False

        dock_manager.register_reload_callback(self._on_dock_reload)

    @property
    def timezone(self) -> _dt.tzinfo:
        return self._tz

    def update_tick(self, tick_seconds: int) -> None:
        self._tick_seconds = max(1, min(tick_seconds, 60))

    def update_timezone(self, name: str) -> None:
        self._tz = resolve_tz(name)
        self._clock = lambda: _dt.datetime.now(self._tz)

    # ───────────────────────── initial sync ────────────────────────────

    async def reconcile_all(self) -> None:
        """Reconcile DB tasks against every dock's current schedule.yaml."""
        for state in self._dock_manager.states.values():
            await self._reconcile_fleet(state)

    async def _reconcile_fleet(self, state: DockState) -> None:
        if state.disabled or state.fleet_config is None:
            # Tear down existing tasks for a disabled fleet
            for t in await self._store.list_tasks_for_fleet(state.row.id):
                await self._store.delete_task(t.id)
            return
        new_specs: list[TaskSpec] = state.schedule_config.tasks if state.schedule_config else []
        existing = {t.task_id: t for t in await self._store.list_tasks_for_fleet(state.row.id)}
        seen: set[str] = set()
        for spec in new_specs:
            seen.add(spec.id)
            sha = spec.source_sha
            old = existing.get(spec.id)
            if old is not None and old.source_sha == sha:
                continue
            new_row = await self._store.upsert_task(
                fleet_id=state.row.id,
                task_id=spec.id,
                cron=spec.cron,
                prompt=spec.prompt,
                source_sha=sha,
            )
            if old is None:
                # cold-start: anchor next fire to now so we don't fire missed windows
                await self._store.upsert_last_fire(new_row.id, self._clock())
                _log.info("scheduler: added %s.%s", state.row.name, spec.id)
            elif old.cron != spec.cron:
                # re-anchor on cron change
                await self._store.upsert_last_fire(new_row.id, self._clock())
                _log.info("scheduler: re-anchored %s.%s (cron change)", state.row.name, spec.id)
            else:
                _log.info("scheduler: updated %s.%s (prompt change)", state.row.name, spec.id)
        # Remove tasks that disappeared
        for task_id, row in existing.items():
            if task_id not in seen:
                await self._store.delete_task(row.id)
                _log.info("scheduler: removed %s.%s", state.row.name, task_id)

    async def _on_dock_reload(self, state: DockState, filename: str) -> None:
        if filename == "schedule.yaml" or filename == "fleet.yaml":
            await self._reconcile_fleet(state)

    # ──────────────────────────── tick loop ────────────────────────────

    async def start(self) -> None:
        if self._task is not None:
            return
        # Anchor any new tasks at startup so missed fires are skipped.
        for t in await self._store.list_tasks():
            if await self._store.get_last_fire(t.id) is None:
                await self._store.upsert_last_fire(t.id, self._clock())
        n_tasks = len(await self._store.list_tasks())
        _log.info("scheduler: %d tasks loaded; missed fires skipped if any", n_tasks)
        self._task = asyncio.create_task(self._tick(), name="scheduler.tick")

    async def stop(self) -> None:
        self._stopping = True
        t = self._task
        self._task = None
        if t is not None:
            t.cancel()
            try:
                await t
            except asyncio.CancelledError, Exception:
                pass

    async def _tick(self) -> None:
        while not self._stopping:
            try:
                await self._maybe_fire_once()
            except Exception:
                _log.exception("scheduler tick failed")
            try:
                await asyncio.sleep(self._tick_seconds)
            except asyncio.CancelledError:
                return

    async def _maybe_fire_once(self) -> None:
        now_local = self._clock()
        # Fresh read each tick — small task counts make this cheap.
        for task in await self._store.list_tasks():
            await self._consider(task, now_local)

    async def _consider(self, task: TaskRow, now: _dt.datetime) -> None:
        last = await self._store.get_last_fire(task.id)
        if last is None:
            await self._store.upsert_last_fire(task.id, now)
            return
        try:
            anchor = _dt.datetime.fromisoformat(last.replace("Z", "+00:00"))
        except ValueError:
            anchor = now
        anchor_local = anchor.astimezone(self._tz)
        try:
            it = croniter(task.cron, anchor_local)
            next_local = it.get_next(_dt.datetime)
        except Exception:
            _log.warning("invalid cron for task %d: %s", task.id, task.cron)
            return
        if next_local.tzinfo is None:
            next_local = next_local.replace(tzinfo=self._tz)
        if next_local > now:
            return
        await self._fire(task, now)

    async def _fire(self, task: TaskRow, now: _dt.datetime) -> None:
        state = self._dock_manager.states.get(task.fleet_id)
        if state is None or state.disabled or state.fleet_config is None:
            return
        fleet = state.row
        try:
            row = await self._runner.enqueue(
                fleet=fleet,
                prompt=task.prompt,
                source="schedule",
                task_pk=task.id,
                task_label=task.task_id,
            )
        except Exception:
            _log.exception("scheduler: enqueue failed for %s.%s", fleet.name, task.task_id)
            return
        await self._store.upsert_last_fire(task.id, now)
        _log.info(
            "scheduler: fired %s.%s -> job %s",
            fleet.name,
            task.task_id,
            row.short_id,
        )

    # ─────────────────────────── /schedule API ──────────────────────────

    async def describe(self) -> list[dict[str, object]]:
        out: list[dict[str, object]] = []
        for task in await self._store.list_tasks():
            fleet = await self._store.get_fleet(task.fleet_id)
            last = await self._store.get_last_fire(task.id)
            try:
                anchor_dt = (
                    _dt.datetime.fromisoformat(last.replace("Z", "+00:00"))
                    if last
                    else self._clock()
                )
                anchor_local = anchor_dt.astimezone(self._tz)
                next_local = croniter(task.cron, anchor_local).get_next(_dt.datetime)
            except Exception:
                next_local = None
            out.append(
                {
                    "fleet": fleet.name if fleet else f"#{task.fleet_id}",
                    "task_id": task.task_id,
                    "cron": task.cron,
                    "last_fire": last,
                    "next_fire": next_local.isoformat() if next_local else None,
                }
            )
        return out
