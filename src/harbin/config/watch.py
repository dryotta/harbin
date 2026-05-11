"""Watchdog-based hot reload of YAML config files (sub-spec 03 §5).

The watchdog observer runs in its own thread; every asyncio interaction is
therefore marshalled onto the event loop via ``call_soon_threadsafe``. All
mutable state (the pending-debounce dict and timer handles) lives only on
the loop thread.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from watchdog.events import FileSystemEvent, FileSystemEventHandler
from watchdog.observers import Observer
from watchdog.observers.api import BaseObserver, ObservedWatch

from harbin.logging import get_logger

_log = get_logger("config.watch")


@dataclass(frozen=True)
class WatchEvent:
    path: Path
    kind: str  # 'modified' | 'created' | 'deleted' | 'moved'


_DEBOUNCE_S = 0.25


class _Handler(FileSystemEventHandler):
    """Watchdog handler. Runs on the observer thread; never touches asyncio
    state directly — all loop interactions go through ``call_soon_threadsafe``.
    """

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
        # ``_pending`` is mutated ONLY on the loop thread (via
        # ``_schedule_fire``/``_fire``).
        self._pending: dict[Path, asyncio.TimerHandle] = {}

    def _basename(self, p: str) -> str:
        return Path(p).name

    def _fire(self, kind: str, full: Path) -> None:
        """Run on the loop thread: invoke the user callback after debounce."""
        self._pending.pop(full, None)
        try:
            self._on_event(WatchEvent(full, kind))
        except Exception:  # pragma: no cover - defensive
            _log.exception("watcher callback failed for %s", full)

    def _schedule_fire(self, kind: str, full: Path) -> None:
        """Run on the loop thread: cancel any existing timer, install new one."""
        existing = self._pending.pop(full, None)
        if existing is not None:
            existing.cancel()
        self._pending[full] = self._loop.call_later(_DEBOUNCE_S, self._fire, kind, full)

    def _maybe(self, kind: str, path: str) -> None:
        """Run on the WATCHDOG thread. Marshal everything to the loop."""
        if self._basename(path) not in self._filenames:
            return
        full = Path(path)
        try:
            self._loop.call_soon_threadsafe(self._schedule_fire, kind, full)
        except RuntimeError:
            # Loop already closed during shutdown — drop the event.
            pass

    def cancel_pending(self) -> None:
        """Cancel any pending debounce timers. MUST run on the loop thread."""
        for handle in self._pending.values():
            handle.cancel()
        self._pending.clear()

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
        # Each entry: (directory, handler, scheduled-watch). The watch handle
        # is kept so a specific watch can be unscheduled when its fleet is
        # removed.
        self._handlers: list[tuple[Path, _Handler, ObservedWatch]] = []

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
        scheduled = self._observer.schedule(handler, str(directory), recursive=False)
        self._handlers.append((directory, handler, scheduled))

    def unwatch(self, directory: Path) -> None:
        """Remove the watch (and any pending debounce) for ``directory``.

        Safe to call when nothing matches. ``cancel_pending`` runs on the
        watchdog stop — schedule it on the loop instead, because timer
        handles only exist there.
        """
        if self._observer is None:
            return
        remaining: list[tuple[Path, _Handler, ObservedWatch]] = []
        for d, h, w in self._handlers:
            if d == directory:
                try:
                    self._observer.unschedule(w)
                except Exception:  # pragma: no cover - defensive
                    _log.warning("unschedule failed for %s", d, exc_info=True)
                try:
                    self._loop.call_soon_threadsafe(h.cancel_pending)
                except RuntimeError:
                    pass
            else:
                remaining.append((d, h, w))
        self._handlers = remaining

    def stop(self) -> None:
        if self._observer is None:
            return
        try:
            self._observer.stop()
            self._observer.join(timeout=2)
        except Exception:
            _log.warning("observer stop raised; ignoring", exc_info=True)
        for _, h, _ in self._handlers:
            try:
                self._loop.call_soon_threadsafe(h.cancel_pending)
            except RuntimeError:
                pass
        self._observer = None
        self._handlers.clear()
