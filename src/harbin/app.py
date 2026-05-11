"""AppCore - startup/shutdown orchestration for harbin (sub-spec 04 §2-3)."""

from __future__ import annotations

import asyncio
import datetime as _dt
import signal
import sys
from typing import TYPE_CHECKING

from harbin.config.loader import load_config
from harbin.config.models import Config
from harbin.config.watch import FileWatcher, WatchEvent
from harbin.context import AppContext
from harbin.db.store import Store
from harbin.fleet.artifacts import ArtifactManager
from harbin.fleet.dock import DockManager
from harbin.logging import get_logger
from harbin.logging import setup as setup_logging
from harbin.paths import HarbinPaths, ensure_all, resolve
from harbin.runner.runner import AgentRunner
from harbin.scheduler import Scheduler
from harbin.web.serve_manager import WebServeManager
from harbin.web.tunnels import TunnelManager

if TYPE_CHECKING:  # pragma: no cover
    pass

_log = get_logger("app")


class AppCore:
    """Owns the event loop and every subsystem.

    Use :meth:`startup` to bring everything online, :meth:`shutdown` to
    tear it down. ``AppCore`` does not own the TUI/web mounting — the
    caller chooses which UI to attach.
    """

    def __init__(self, paths: HarbinPaths, config: Config) -> None:
        self.paths = paths
        self.config = config
        self.store: Store | None = None
        self.artifacts: ArtifactManager | None = None
        self.dock_manager: DockManager | None = None
        self.runner: AgentRunner | None = None
        self.scheduler: Scheduler | None = None
        self.tunnels: TunnelManager | None = None
        self.web_server: WebServeManager | None = None
        self._console_writer = lambda s: print(s)
        self._shutdown_event = asyncio.Event()
        self._shutdown_started = False
        self._sweep_task: asyncio.Task[None] | None = None
        self._config_watcher: FileWatcher | None = None
        self._config_reload_tasks: set[asyncio.Task[None]] = set()

    def set_console_writer(self, fn) -> None:  # type: ignore[no-untyped-def]
        self._console_writer = fn

    @classmethod
    async def startup(cls, *, console_writer=None) -> AppCore:  # type: ignore[no-untyped-def]
        paths = resolve()
        ensure_all(paths)
        setup_logging(log_dir=paths.log_dir, level="info")
        cfg = load_config(paths.config_dir / "config.yaml")
        from harbin.logging import set_level

        set_level(cfg.ui.log_verbosity)
        core = cls(paths, cfg)
        if console_writer is not None:
            core.set_console_writer(console_writer)
        await core._bring_up()
        return core

    async def _bring_up(self) -> None:
        # 1. DB
        self.store = await Store.open(self.paths.db_path)
        # Reap any 'queued'/'starting'/'running' rows left by a previous
        # crash so the TUI doesn't show phantom jobs.
        reaped = await self.store.reap_orphan_running()
        if reaped:
            _log.info("reaped %d orphan running jobs from a prior crash", reaped)

        # 2. Artifact root
        artifact_root = (
            self.config.artifacts.root if self.config.artifacts.root else self.paths.artifact_root
        )
        artifact_root.mkdir(parents=True, exist_ok=True)

        # 3. Artifact manager + dock manager
        self.artifacts = ArtifactManager(
            root=artifact_root,
            store=self.store,
            default_retention=self.config.artifacts.retention,
        )
        self.dock_manager = DockManager(
            store=self.store,
            dock_root=self.paths.dock_root,
            on_event=lambda kind, data: _log.info("dock event: %s %s", kind, data),
        )
        await self.dock_manager.load_existing()

        # 4. Runner
        self.runner = AgentRunner(
            store=self.store,
            artifacts=self.artifacts,
            dock_manager=self.dock_manager,
            agent_cli=self.config.agent_runner.agent_cli,
            concurrency=self.config.agent_runner.concurrency,
            kill_grace_seconds=self.config.agent_runner.kill_grace_seconds,
            prompts_dir=self.paths.prompts_dir,
            on_event=lambda kind, data: None,
        )

        # 4a. Wire fleet-removal cleanup: runner dispatcher + artifact tree.
        artifacts_ref = self.artifacts
        runner_ref = self.runner

        async def _on_remove(fleet_id: int, fleet_name: str) -> None:
            runner_ref.remove_fleet(fleet_id)
            artifacts_ref.remove_fleet_tree(fleet_name)

        self.dock_manager.register_remove_callback(_on_remove)

        # 5. Scheduler
        self.scheduler = Scheduler(
            store=self.store,
            dock_manager=self.dock_manager,
            runner=self.runner,
            tick_seconds=self.config.scheduler.tick_seconds,
            timezone_name=self.config.timezone,
        )
        await self.scheduler.reconcile_all()
        await self.scheduler.start()

        # 6. Tunnels + web server (managed children — both expose
        # start/stop/status via slash commands).
        self.tunnels = TunnelManager(devtunnel_path=self.config.tunnels.devtunnel_path)
        self.web_server = WebServeManager()

        # 6a. Honor config.web.autostart (sub-spec 14 §1.1).
        if self.config.web.autostart:
            try:
                msg = await self.web_server.start(
                    port=self.config.web.port,
                    host=self.config.web.host,
                )
                _log.info("web autostart: %s", msg)
            except Exception:
                _log.warning("web autostart failed", exc_info=True)

        # 7. Periodic dock sync
        await self.dock_manager.start_periodic_sync()

        # 8. Daily artifact sweep
        self._sweep_task = asyncio.create_task(self._sweep_loop(), name="artifacts.sweep")

        # 9. Vacuum (best-effort)
        try:
            await self.store.maybe_vacuum()
        except Exception:
            _log.debug("vacuum skipped", exc_info=True)

        # 10. Signal handlers
        self._install_signals()

        # 11. config.yaml hot reload (sub-spec 03 §5).
        self._install_config_watch()

        n_fleets = len(self.dock_manager.states)
        n_tasks = len(await self.store.list_tasks())
        _log.info("harbin ready · %d fleets · %d tasks", n_fleets, n_tasks)

    # ─────────────────────── config hot reload ───────────────────────

    def _install_config_watch(self) -> None:
        """Watch ``config.yaml`` and propagate apply-live fields on change."""
        try:
            self._config_watcher = FileWatcher()
            self._config_watcher.watch(
                self.paths.config_dir,
                {"config.yaml"},
                self._on_config_event,
            )
        except Exception:
            _log.warning("could not install config.yaml watcher", exc_info=True)

    def _on_config_event(self, evt: WatchEvent) -> None:
        """Watcher callback (already runs on the loop after debounce)."""
        task = asyncio.create_task(self._reload_config(evt), name="config.reload")
        # Keep a reference until done so the task isn't GC'd early.
        self._config_reload_tasks.add(task)
        task.add_done_callback(self._config_reload_tasks.discard)

    async def _reload_config(self, evt: WatchEvent) -> None:
        if evt.kind == "deleted":
            _log.warning("config.yaml deleted; keeping in-memory config")
            return
        try:
            new_cfg = load_config(evt.path)
        except Exception:
            _log.exception("config.yaml reload failed")
            return
        self.apply_live_config(new_cfg)

    def apply_live_config(self, new_cfg: Config) -> None:
        """Apply the subset of config that updates at runtime without a restart.

        Apply-live (sub-spec 03 §5.3):
            * ``ui.log_verbosity`` → :func:`logging.set_level`
            * ``timezone``, ``scheduler.tick_seconds`` → scheduler
            * ``agent_runner.{agent_cli, concurrency, kill_grace_seconds}`` →
              runner.update_runtime_config

        Restart-required fields (theme, web bind, artifact root, etc.) are
        copied into the in-memory ``Config`` so the values displayed by the
        Config modal stay consistent, but they take effect on next launch.
        """
        from harbin.logging import set_level

        try:
            set_level(new_cfg.ui.log_verbosity)
        except Exception:
            _log.warning("set_level failed", exc_info=True)
        if self.scheduler is not None:
            try:
                self.scheduler.update_tick(new_cfg.scheduler.tick_seconds)
                self.scheduler.update_timezone(new_cfg.timezone)
            except Exception:
                _log.warning("scheduler live update failed", exc_info=True)
        if self.runner is not None:
            try:
                self.runner.update_runtime_config(
                    agent_cli=new_cfg.agent_runner.agent_cli,
                    concurrency=new_cfg.agent_runner.concurrency,
                    kill_grace_seconds=new_cfg.agent_runner.kill_grace_seconds,
                )
            except Exception:
                _log.warning("runner live update failed", exc_info=True)
        # Replace the in-memory snapshot (used by the modal + status bar).
        self.config = new_cfg
        _log.info("config: apply-live propagated")

    # ─────────────────────── sweep loop ──────────────────────────

    async def _sweep_loop(self) -> None:
        from croniter import croniter

        while not self._shutdown_event.is_set():
            try:
                cron = self.config.artifacts.sweep_cron
                now = _dt.datetime.now()
                it = croniter(cron, now)
                nxt = it.get_next(_dt.datetime)
                wait = max(1.0, (nxt - now).total_seconds())
            except Exception:
                wait = 3600.0
            try:
                await asyncio.wait_for(self._shutdown_event.wait(), timeout=wait)
                # If we get here, shutdown was requested.
                return
            except TimeoutError:
                pass
            try:
                assert self.dock_manager is not None
                assert self.artifacts is not None
                fleets = await self.store.list_fleets()  # type: ignore[union-attr]
                fleet_configs = {
                    s.row.id: s.fleet_config for s in self.dock_manager.states.values()
                }
                n = await self.artifacts.sweep(fleets=fleets, fleet_configs=fleet_configs)
                if n:
                    _log.info("artifacts sweep: archived %d jobs", n)
            except Exception:
                _log.exception("artifact sweep crashed")

    # ─────────────────────── signals ────────────────────────────

    def _install_signals(self) -> None:
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:  # pragma: no cover - only inside async context
            return
        if sys.platform != "win32":
            for sig in (signal.SIGINT, signal.SIGTERM):
                try:
                    loop.add_signal_handler(sig, self.request_shutdown)
                except NotImplementedError, RuntimeError:
                    pass
        else:

            def _handler(signum: int, frame: object) -> None:
                loop.call_soon_threadsafe(self.request_shutdown)

            for sig_name in ("SIGINT", "SIGBREAK"):
                sig = getattr(signal, sig_name, None)
                if sig is None:
                    continue
                try:
                    signal.signal(sig, _handler)
                except OSError, ValueError:
                    pass

    # ─────────────────────── shutdown ───────────────────────────

    def request_shutdown(self) -> None:
        if self._shutdown_started:
            return
        self._shutdown_started = True
        self._shutdown_event.set()

    async def wait_for_shutdown(self) -> None:
        await self._shutdown_event.wait()

    async def shutdown(self) -> None:
        if not self._shutdown_started:
            self._shutdown_started = True
        try:
            await asyncio.wait_for(self._shutdown_inner(), timeout=30)
        except TimeoutError:
            _log.error("shutdown watchdog tripped")
            import os

            os._exit(2)

    async def _shutdown_inner(self) -> None:
        if self._config_watcher is not None:
            try:
                self._config_watcher.stop()
            except Exception:
                _log.warning("config watcher stop failed", exc_info=True)
            self._config_watcher = None
        if self.scheduler is not None:
            await self.scheduler.stop()
        if self._sweep_task is not None:
            self._sweep_task.cancel()
            try:
                await self._sweep_task
            except asyncio.CancelledError, Exception:
                pass
        if self.runner is not None:
            await self.runner.stop()
        if self.dock_manager is not None:
            await self.dock_manager.stop()
        if self.web_server is not None:
            await self.web_server.cleanup()
        if self.tunnels is not None:
            await self.tunnels.cleanup()
        if self.store is not None:
            await self.store.close()
        _log.info("harbin exited cleanly")

    # ─────────────────────── make context ────────────────────────

    def make_context(self) -> AppContext:
        assert self.store is not None
        assert self.artifacts is not None
        assert self.dock_manager is not None
        assert self.runner is not None
        assert self.scheduler is not None
        assert self.tunnels is not None
        assert self.web_server is not None
        return AppContext(
            config=self.config,
            paths=self.paths,
            store=self.store,
            artifacts=self.artifacts,
            dock_manager=self.dock_manager,
            runner=self.runner,
            scheduler=self.scheduler,
            tunnels=self.tunnels,
            web_server=self.web_server,
            console_writer=self._console_writer,
            request_shutdown=self.request_shutdown,
            apply_live_config=self.apply_live_config,
        )
