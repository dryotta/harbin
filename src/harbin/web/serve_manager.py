"""WebServeManager — wraps a child ``harbin serve`` subprocess (sub-spec 14 §1).

Symmetric with :class:`harbin.web.tunnels.TunnelManager`: ``/web start``
launches ``harbin serve`` as a managed child so the TUI can toggle the
HTTP/WebSocket layer on and off without restarting. The serve process
spawns its own per-connection harbin subprocesses through
``textual-serve`` (one per browser tab); state is shared across all
those processes via the on-disk SQLite WAL (sub-spec 02 §1).
"""

from __future__ import annotations

import asyncio
import datetime as _dt
import signal
import socket
import sys
from dataclasses import dataclass

from harbin.logging import get_logger

_log = get_logger("web.serve")


@dataclass
class WebState:
    port: int | None = None
    host: str | None = None
    started_at: _dt.datetime | None = None


class WebServeManager:
    """Lifecycle manager for the child ``harbin serve`` process."""

    def __init__(
        self,
        *,
        harbin_argv0: list[str] | None = None,
    ) -> None:
        # By default we re-invoke harbin via ``python -m harbin`` so the
        # child sees the exact same interpreter the user is running. A
        # test can override this to point at a stub.
        self._argv0 = harbin_argv0 or [sys.executable, "-m", "harbin"]
        self._proc: asyncio.subprocess.Process | None = None
        self._state = WebState()

    @property
    def state(self) -> WebState:
        return self._state

    def is_running(self) -> bool:
        return self._proc is not None and self._proc.returncode is None

    def url(self) -> str | None:
        if not self.is_running() or self._state.host is None or self._state.port is None:
            return None
        return f"http://{self._state.host}:{self._state.port}/"

    def status(self) -> str:
        if self.is_running():
            return f"web: running · {self.url()}"
        return "web: not running"

    async def start(self, *, port: int, host: str) -> str:
        """Spawn ``harbin serve --port P --host H``. Idempotent."""
        if self.is_running():
            return self.status()

        # Pre-flight: bail with a clear error if the port is already
        # bound (likely by a previous serve invocation we lost track of).
        if _port_in_use(host, port):
            return (
                f"error: port {port} is already in use. "
                f"choose another with `/web start --port <N>`."
            )

        argv = [*self._argv0, "serve", "--port", str(port), "--host", host]
        try:
            if sys.platform == "win32":
                proc = await asyncio.create_subprocess_exec(
                    *argv,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.STDOUT,
                    creationflags=0x00000200,  # CREATE_NEW_PROCESS_GROUP
                )
            else:
                proc = await asyncio.create_subprocess_exec(
                    *argv,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.STDOUT,
                    start_new_session=True,
                )
        except FileNotFoundError as e:
            return f"error: could not spawn harbin serve: {e}"
        self._proc = proc
        self._state.port = port
        self._state.host = host
        self._state.started_at = _dt.datetime.now(_dt.UTC)

        # Wait briefly for the port to bind so users get a confirmation
        # in the same turn instead of a bare "started".
        bound = await _await_port_bound(host, port, timeout=5.0)
        if not bound:
            # The process is alive but didn't bind in time — surface a
            # warning rather than a hard error so the user can decide.
            _log.warning("web serve started but port %s:%d not bound yet", host, port)
            return f"web started on {host}:{port} (binding…)"
        return f"web started on http://{host}:{port}/"

    async def stop(self) -> str:
        if not self.is_running():
            return "web: not running"
        proc = self._proc
        assert proc is not None
        try:
            if sys.platform == "win32":
                proc.send_signal(signal.CTRL_BREAK_EVENT)  # type: ignore[attr-defined]
            else:
                proc.terminate()
        except Exception:
            _log.warning("web terminate raised", exc_info=True)
        try:
            await asyncio.wait_for(proc.wait(), timeout=5)
        except TimeoutError:
            try:
                proc.kill()
            except Exception:
                pass
            await proc.wait()
        self._proc = None
        self._state = WebState()
        return "web: stopped"

    async def cleanup(self) -> None:
        """Called at AppCore shutdown. Stops the child if still running."""
        if self.is_running():
            await self.stop()


def _port_in_use(host: str, port: int) -> bool:
    """True iff something is already accepting on ``host:port``."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.settimeout(0.2)
            return s.connect_ex((host, port)) == 0
    except OSError:
        return False


async def _await_port_bound(host: str, port: int, *, timeout: float) -> bool:
    """Poll until something is accepting on ``host:port`` or timeout."""
    end = asyncio.get_event_loop().time() + timeout
    while asyncio.get_event_loop().time() < end:
        if _port_in_use(host, port):
            return True
        await asyncio.sleep(0.1)
    return False
