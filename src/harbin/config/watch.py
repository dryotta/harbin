"""Watchdog-based hot reload of YAML config files (sub-spec 03 §5)."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from watchdog.events import FileSystemEvent, FileSystemEventHandler
from watchdog.observers import Observer
from watchdog.observers.api import BaseObserver

from harbin.logging import get_logger

_log = get_logger("config.watch")


@dataclass(frozen=True)
class WatchEvent:
    path: Path
    kind: str  # 'modified' | 'created' | 'deleted' | 'moved'


_DEBOUNCE_S = 0.25


class _Handler(FileSystemEventHandler):
    def __init__(
        self,
        *,
        loop: asyncio.AbstractEventLoop,
        filenames: set[str],
        on_event: Callable[[WatchEvent], None],
    ) -> None:
        self._loop = loop
        self._filenames = filenames
        self._on_event = on_event
        self._pending: dict[Path, asyncio.TimerHandle] = {}

    def _basename(self, p: str) -> str:
        return Path(p).name

    def _maybe(self, kind: str, path: str) -> None:
        if self._basename(path) not in self._filenames:
            return
        full = Path(path)

        def fire() -> None:
            self._pending.pop(full, None)
            try:
                self._on_event(WatchEvent(full, kind))
            except Exception:  # pragma: no cover
                _log.exception("watcher callback failed for %s", full)

        existing = self._pending.pop(full, None)
        if existing is not None:
            existing.cancel()
        self._pending[full] = self._loop.call_later(_DEBOUNCE_S, fire)

    def on_modified(self, event: FileSystemEvent) -> None:
        if not event.is_directory:
            self._maybe("modified", str(event.src_path))

    def on_created(self, event: FileSystemEvent) -> None:
        if not event.is_directory:
            self._maybe("created", str(event.src_path))

    def on_deleted(self, event: FileSystemEvent) -> None:
        if not event.is_directory:
            self._maybe("deleted", str(event.src_path))

    def on_moved(self, event: FileSystemEvent) -> None:
        if not event.is_directory:
            self._maybe("moved", str(event.dest_path))


class FileWatcher:
    """Wraps a single watchdog Observer for a fixed list of filenames."""

    def __init__(
        self,
        *,
        loop: asyncio.AbstractEventLoop | None = None,
    ) -> None:
        self._loop = loop or asyncio.get_event_loop()
        self._observer: BaseObserver | None = None
        self._handlers: list[tuple[Path, _Handler]] = []

    def watch(
        self,
        directory: Path,
        filenames: set[str],
        on_event: Callable[[WatchEvent], None],
    ) -> None:
        """Add a watch on ``directory`` for files in ``filenames``.

        ``on_event`` is called from the asyncio loop (debounced).
        """
        directory.mkdir(parents=True, exist_ok=True)
        if self._observer is None:
            self._observer = Observer()
            self._observer.start()
        handler = _Handler(loop=self._loop, filenames=filenames, on_event=on_event)
        self._observer.schedule(handler, str(directory), recursive=False)
        self._handlers.append((directory, handler))

    def stop(self) -> None:
        if self._observer is None:
            return
        try:
            self._observer.stop()
            self._observer.join(timeout=2)
        except Exception:
            _log.warning("observer stop raised; ignoring", exc_info=True)
        self._observer = None
        self._handlers.clear()
