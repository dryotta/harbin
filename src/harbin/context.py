"""Lightweight context object passed to REPL commands and widgets."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover
    from harbin.config.models import Config
    from harbin.db.store import Store
    from harbin.fleet.artifacts import ArtifactManager
    from harbin.fleet.dock import DockManager
    from harbin.paths import HarbinPaths
    from harbin.runner.runner import AgentRunner
    from harbin.scheduler import Scheduler
    from harbin.web.tunnels import TunnelManager


@dataclass
class AppContext:
    """Bundle of subsystems made available to REPL commands & TUI widgets."""

    config: Config
    paths: HarbinPaths
    store: Store
    artifacts: ArtifactManager
    dock_manager: DockManager
    runner: AgentRunner
    scheduler: Scheduler
    tunnels: TunnelManager
    console_writer: Callable[[str], None]
    request_shutdown: Callable[[], None]
