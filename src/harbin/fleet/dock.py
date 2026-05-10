"""Dock Manager (sub-spec 07).

Owns the on-disk per-fleet git clones, the periodic sync loop, push-back
on success, and the watchdog wiring for hot reload of ``.harbin/`` files.
"""

from __future__ import annotations

import asyncio
import datetime as _dt
import shutil
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from harbin.config.loader import load_fleet, load_schedule
from harbin.config.models import parse_retention
from harbin.config.watch import FileWatcher, WatchEvent
from harbin.errors import DockError, FleetError, ValidationError
from harbin.fleet.models import FleetConfig, ScheduleConfig
from harbin.logging import get_logger

if TYPE_CHECKING:  # pragma: no cover
    from harbin.db.store import FleetRow, Store

_log = get_logger("dock")


@dataclass
class DockState:
    """In-memory state for one dock."""

    row: FleetRow
    fleet_config: FleetConfig | None
    schedule_config: ScheduleConfig | None = None
    disabled: bool = False
    last_error: str | None = None
    dirty: bool = False
    last_sync_ok: bool = False
    last_sync_at: _dt.datetime | None = None


@dataclass(frozen=True)
class GitResult:
    returncode: int
    stdout: str
    stderr: str


async def _git(
    *args: str,
    cwd: Path | None = None,
    timeout: float = 60.0,
) -> GitResult:
    proc = await asyncio.create_subprocess_exec(
        "git",
        *args,
        cwd=str(cwd) if cwd else None,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout_b, stderr_b = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except TimeoutError:
        proc.kill()
        await proc.wait()
        raise DockError(
            code="fleet.dock.timeout",
            message=f"git {' '.join(args)} timed out after {timeout}s",
        ) from None
    return GitResult(
        returncode=int(proc.returncode or 0),
        stdout=stdout_b.decode("utf-8", errors="replace"),
        stderr=stderr_b.decode("utf-8", errors="replace"),
    )


class DockManager:
    """Manage on-disk docks and their git-based sync."""

    def __init__(
        self,
        *,
        store: Store,
        dock_root: Path,
        on_event: Callable[[str, dict[str, object]], None] | None = None,
    ) -> None:
        self._store = store
        self._dock_root = dock_root
        self._states: dict[int, DockState] = {}
        self._watcher = FileWatcher()
        self._on_event = on_event or (lambda kind, data: None)
        self._reload_callbacks: list[Callable[[DockState, str], Awaitable[None]]] = []
        self._sync_tasks: dict[int, asyncio.Task[None]] = {}
        self._reload_tasks: set[asyncio.Task[None]] = set()
        self._stopping = False
        dock_root.mkdir(parents=True, exist_ok=True)

    @property
    def states(self) -> dict[int, DockState]:
        return self._states

    def register_reload_callback(self, fn: Callable[[DockState, str], Awaitable[None]]) -> None:
        self._reload_callbacks.append(fn)

    # ─────────────────────── load existing docks ──────────────────────

    async def load_existing(self) -> None:
        for row in await self._store.list_fleets():
            try:
                fleet_cfg = load_fleet(Path(row.dock_path) / ".harbin" / "fleet.yaml")
                schedule_cfg = load_schedule(Path(row.dock_path) / ".harbin" / "schedule.yaml")
                state = DockState(row=row, fleet_config=fleet_cfg, schedule_config=schedule_cfg)
            except FleetError as e:
                state = DockState(row=row, fleet_config=None, disabled=True, last_error=str(e))
                _log.warning("fleet %s disabled at load: %s", row.name, e)
            except ValidationError as e:
                state = DockState(row=row, fleet_config=None, disabled=True, last_error=str(e))
                _log.warning("fleet %s disabled (validation): %s", row.name, e)
            self._states[row.id] = state
            self._watch_dock(state)

    # ────────────────────────── registration ──────────────────────────

    async def register_fleet(self, url: str) -> DockState:
        """Clone, validate, register a fleet by URL."""
        prelim_name = Path(url.rstrip("/").rstrip(".git")).name or "fleet"
        prelim_path = self._dock_root / prelim_name

        if prelim_path.exists():
            raise DockError(
                code="fleet.dock.clone_exists",
                message=f"path already exists: {prelim_path}",
            )
        result = await _git("clone", "--depth=50", url, str(prelim_path), timeout=600)
        if result.returncode != 0:
            self._rmtree_safe(prelim_path)
            raise DockError(
                code="fleet.dock.clone_failed",
                message=f"git clone failed (rc={result.returncode}): {result.stderr.strip()}",
            )

        fleet_yaml = prelim_path / ".harbin" / "fleet.yaml"
        if not fleet_yaml.exists():
            self._rmtree_safe(prelim_path)
            raise DockError(
                code="fleet.dock.no_fleet_yaml",
                message=f"{url}: missing .harbin/fleet.yaml",
            )
        try:
            fleet_cfg = load_fleet(fleet_yaml)
        except (ValidationError, FleetError) as e:
            self._rmtree_safe(prelim_path)
            raise DockError(
                code="fleet.dock.invalid_fleet_yaml",
                message=f"{url}: invalid fleet.yaml: {e}",
            ) from e

        final_path = self._dock_root / fleet_cfg.name
        if final_path != prelim_path:
            if final_path.exists():
                self._rmtree_safe(prelim_path)
                raise DockError(
                    code="fleet.dock.name_collision",
                    message=f"fleet '{fleet_cfg.name}' already has a dock at {final_path}",
                )
            prelim_path.rename(final_path)

        existing = await self._store.get_fleet_by_name(fleet_cfg.name)
        if existing is not None:
            raise DockError(
                code="fleet.dock.already_registered",
                message=f"fleet '{fleet_cfg.name}' already registered",
            )

        row = await self._store.insert_fleet(
            name=fleet_cfg.name, url=url, dock_path=str(final_path)
        )
        schedule_cfg = load_schedule(final_path / ".harbin" / "schedule.yaml")
        state = DockState(row=row, fleet_config=fleet_cfg, schedule_config=schedule_cfg)
        self._states[row.id] = state
        self._watch_dock(state)
        self._on_event("fleet_registered", {"fleet": fleet_cfg.name})
        return state

    async def register_from_existing_dock(self, dock_path: Path) -> DockState:
        """Register an already-cloned dock (used by sample-fleet add)."""
        fleet_yaml = dock_path / ".harbin" / "fleet.yaml"
        if not fleet_yaml.exists():
            raise DockError(
                code="fleet.dock.no_fleet_yaml",
                message=f"{dock_path}: missing .harbin/fleet.yaml",
            )
        fleet_cfg = load_fleet(fleet_yaml)
        existing = await self._store.get_fleet_by_name(fleet_cfg.name)
        if existing is not None:
            state = DockState(
                row=existing,
                fleet_config=fleet_cfg,
                schedule_config=load_schedule(dock_path / ".harbin" / "schedule.yaml"),
            )
            self._states[existing.id] = state
            return state
        # determine URL via `git remote get-url origin`, falling back to ""
        url = ""
        try:
            r = await _git("remote", "get-url", "origin", cwd=dock_path, timeout=5)
            if r.returncode == 0:
                url = r.stdout.strip()
        except DockError:
            pass
        row = await self._store.insert_fleet(name=fleet_cfg.name, url=url, dock_path=str(dock_path))
        state = DockState(
            row=row,
            fleet_config=fleet_cfg,
            schedule_config=load_schedule(dock_path / ".harbin" / "schedule.yaml"),
        )
        self._states[row.id] = state
        self._watch_dock(state)
        return state

    async def remove_fleet(self, fleet_id: int) -> None:
        state = self._states.pop(fleet_id, None)
        if state is None:
            return
        # Stop any sync task
        t = self._sync_tasks.pop(fleet_id, None)
        if t is not None:
            t.cancel()
        self._rmtree_safe(Path(state.row.dock_path))
        await self._store.delete_fleet(fleet_id)
        self._on_event("fleet_removed", {"fleet": state.row.name})

    # ─────────────────────────── watchdog ─────────────────────────────

    def _watch_dock(self, state: DockState) -> None:
        harbin_dir = Path(state.row.dock_path) / ".harbin"
        try:
            harbin_dir.mkdir(parents=True, exist_ok=True)
        except OSError:
            _log.warning("could not create %s; skipping watch", harbin_dir)
            return

        fleet_id = state.row.id
        reload_tasks: set[asyncio.Task[None]] = self._reload_tasks

        def _callback(evt: WatchEvent) -> None:
            task = asyncio.create_task(self._handle_reload(fleet_id, evt))
            reload_tasks.add(task)
            task.add_done_callback(reload_tasks.discard)

        self._watcher.watch(
            harbin_dir,
            {"fleet.yaml", "schedule.yaml"},
            _callback,
        )

    async def _handle_reload(self, fleet_id: int, evt: WatchEvent) -> None:
        state = self._states.get(fleet_id)
        if state is None:
            return
        name = evt.path.name
        try:
            if name == "fleet.yaml":
                if evt.kind == "deleted":
                    state.disabled = True
                    state.last_error = "fleet.yaml deleted"
                    state.fleet_config = None
                    _log.warning("fleet %s disabled: fleet.yaml deleted", state.row.name)
                else:
                    cfg = load_fleet(evt.path)
                    if cfg.name != state.row.name:
                        raise DockError(
                            code="fleet.dock.rename_unsupported",
                            message="renaming fleets is not supported in v1",
                        )
                    state.fleet_config = cfg
                    state.disabled = False
                    state.last_error = None
            elif name == "schedule.yaml":
                if evt.kind == "deleted":
                    state.schedule_config = ScheduleConfig()
                else:
                    state.schedule_config = load_schedule(evt.path)
        except (ValidationError, FleetError) as e:
            state.disabled = True
            state.last_error = str(e)
            _log.warning("fleet %s reload error: %s", state.row.name, e)
        else:
            self._on_event("fleet_reloaded", {"fleet": state.row.name, "file": name})
        for cb in self._reload_callbacks:
            try:
                await cb(state, name)
            except Exception:
                _log.exception("reload callback failed for %s", state.row.name)

    # ───────────────────────── periodic sync ──────────────────────────

    async def start_periodic_sync(self) -> None:
        for fleet_id in list(self._states):
            self._spawn_sync_task(fleet_id)

    def _spawn_sync_task(self, fleet_id: int) -> None:
        task = asyncio.create_task(self._sync_loop(fleet_id))
        task.set_name(
            f"dock.sync:{self._states[fleet_id].row.name}"
            if fleet_id in self._states
            else f"dock.sync:{fleet_id}"
        )
        self._sync_tasks[fleet_id] = task

    async def _sync_loop(self, fleet_id: int) -> None:
        while not self._stopping:
            state = self._states.get(fleet_id)
            if state is None:
                return
            try:
                await self.sync_once(state)
            except asyncio.CancelledError:
                raise
            except Exception:  # pragma: no cover - defensive
                _log.exception("sync loop crashed for %s", state.row.name)
            interval = self._sync_interval_seconds(state)
            try:
                await asyncio.sleep(interval)
            except asyncio.CancelledError:
                return

    @staticmethod
    def _sync_interval_seconds(state: DockState) -> float:
        spec = state.fleet_config.sync_interval if state.fleet_config else "5m"
        if spec is None:
            return 300.0
        try:
            return parse_retention(spec).total_seconds()
        except ValueError:
            return 300.0

    async def sync_once(self, state: DockState) -> str:
        """Run one fetch+ff cycle. Returns a one-line summary."""
        if state.disabled or state.fleet_config is None:
            return f"{state.row.name}: disabled"
        dock = Path(state.row.dock_path)
        try:
            fetch = await _git("fetch", "--prune", "origin", cwd=dock)
        except DockError as e:
            state.last_error = str(e)
            return f"{state.row.name}: fetch failed: {e.message}"
        if fetch.returncode != 0:
            state.last_error = fetch.stderr.strip()
            return f"{state.row.name}: fetch error: {fetch.stderr.strip()}"

        status = await _git("status", "--porcelain", cwd=dock, timeout=5)
        clean = status.returncode == 0 and status.stdout.strip() == ""
        head = await _git("symbolic-ref", "--short", "HEAD", cwd=dock, timeout=5)
        head_branch = head.stdout.strip()
        on_branch = head.returncode == 0 and head_branch == state.fleet_config.default_branch

        if not clean or not on_branch:
            state.dirty = True
            self._on_event("dock_dirty", {"fleet": state.row.name})
            return f"{state.row.name}: dirty — fast-forward skipped"

        state.dirty = False
        ff = await _git(
            "merge",
            "--ff-only",
            f"origin/{state.fleet_config.default_branch}",
            cwd=dock,
        )
        state.last_sync_at = _dt.datetime.now(_dt.UTC)
        state.last_sync_ok = ff.returncode == 0
        if ff.returncode == 0:
            return f"{state.row.name}: synced"
        return f"{state.row.name}: ff error: {ff.stderr.strip()}"

    # ───────────────────────────── push-back ───────────────────────────

    async def push_back(
        self,
        *,
        state: DockState,
        artifact_dir: Path,
        short_id: str,
        task_label: str,
        prompt: str,
    ) -> str | None:
        """Push artifacts that landed inside the dock. Returns warning or None."""
        if state.fleet_config is None:
            return None
        if not state.fleet_config.artifact_policy.push_back:
            return None
        dock = Path(state.row.dock_path)
        try:
            if not artifact_dir.resolve().is_relative_to(dock.resolve()):
                return None
        except OSError:
            return None
        rel = artifact_dir.resolve().relative_to(dock.resolve())

        # Pre-check: clean+on-branch (the runner could have left changes).
        head = await _git("symbolic-ref", "--short", "HEAD", cwd=dock, timeout=5)
        if head.returncode != 0 or head.stdout.strip() != state.fleet_config.default_branch:
            return "push-back skipped: not on default branch"

        add = await _git("add", "--", str(rel), cwd=dock)
        if add.returncode != 0:
            return f"push-back: git add failed: {add.stderr.strip()}"
        diff = await _git("diff", "--cached", "--quiet", cwd=dock)
        if diff.returncode == 0:
            return None  # nothing staged

        now = _dt.datetime.now(_dt.UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
        msg = (
            f"harbin: {task_label} @{now}\n\n"
            f"job: {short_id}\n"
            f"prompt: {prompt[:80].replace(chr(10), ' ')}{'…' if len(prompt) > 80 else ''}\n"
        )
        commit = await _git(
            "-c",
            "user.name=harbin",
            "-c",
            "user.email=harbin@localhost",
            "commit",
            "-m",
            msg,
            cwd=dock,
        )
        if commit.returncode != 0:
            return f"push-back: commit failed: {commit.stderr.strip()}"
        push = await _git(
            "push",
            "origin",
            state.fleet_config.default_branch,
            cwd=dock,
            timeout=120,
        )
        if push.returncode != 0:
            return f"push-back: push failed: {push.stderr.strip()}"
        return None

    # ──────────────────────────── shutdown ────────────────────────────

    async def stop(self) -> None:
        self._stopping = True
        for t in list(self._sync_tasks.values()):
            t.cancel()
        for t in list(self._sync_tasks.values()):
            try:
                await t
            except asyncio.CancelledError, Exception:
                pass
        self._sync_tasks.clear()
        self._watcher.stop()

    @staticmethod
    def _rmtree_safe(path: Path) -> None:
        try:
            if path.exists():
                shutil.rmtree(path, ignore_errors=False)
        except OSError as e:
            _log.warning("could not rmtree %s: %s", path, e)
