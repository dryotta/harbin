"""TunnelManager — wraps the external ``devtunnel host`` subprocess (sub-spec 14)."""

from __future__ import annotations

import asyncio
import datetime as _dt
import re
import shutil
import signal
import sys
from dataclasses import dataclass

from harbin.logging import get_logger

_log = get_logger("tunnels")

_URL_RE = re.compile(r"https://[a-z0-9-]+\.[a-z0-9-]+\.devtunnels\.ms/?", re.IGNORECASE)


@dataclass
class TunnelState:
    public_url: str | None = None
    started_at: _dt.datetime | None = None


class TunnelManager:
    """Minimal wrapper around the user-installed ``devtunnel`` binary."""

    def __init__(self, *, devtunnel_path: str = "devtunnel") -> None:
        self._cmd = devtunnel_path
        self._proc: asyncio.subprocess.Process | None = None
        self._state = TunnelState()
        self._url_task: asyncio.Task[None] | None = None

    @property
    def state(self) -> TunnelState:
        return self._state

    def is_running(self) -> bool:
        return self._proc is not None and self._proc.returncode is None

    def status(self) -> str:
        if self.is_running():
            return f"tunnel: running · {self._state.public_url or '(awaiting URL)'}"
        return "tunnel: not running"

    async def start(self, *, port: int, tunnel_id: str | None, allow_anonymous: bool) -> str:
        if self.is_running():
            return self.status()
        if shutil.which(self._cmd) is None:
            return "error: devtunnel binary not found.\n  install: see doc/remote-access.md"

        # Precheck auth via `devtunnel user show`.
        try:
            who = await asyncio.create_subprocess_exec(
                self._cmd,
                "user",
                "show",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            try:
                await asyncio.wait_for(who.wait(), timeout=5)
            except TimeoutError:
                who.kill()
                await who.wait()
                return "error: 'devtunnel user show' timed out"
            if who.returncode != 0:
                return (
                    "not logged in to devtunnel. run:\n"
                    "  devtunnel user login -g\n"
                    "then try /tunnel start again."
                )
        except FileNotFoundError:
            return "error: devtunnel binary not found on PATH"

        args = [
            self._cmd,
            "host",
            "-p",
            str(port),
            "--allow-anonymous",
            "true" if allow_anonymous else "false",
        ]
        if tunnel_id:
            args += ["--tunnel-id", tunnel_id]

        if sys.platform == "win32":
            proc = await asyncio.create_subprocess_exec(
                *args,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
                creationflags=0x00000200,
            )
        else:
            proc = await asyncio.create_subprocess_exec(
                *args,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
                start_new_session=True,
            )
        self._proc = proc
        self._state.started_at = _dt.datetime.now(_dt.UTC)
        self._url_task = asyncio.create_task(self._capture_url(proc), name="tunnel.capture_url")
        return f"tunnel started on port {port} (awaiting public URL)"

    async def _capture_url(self, proc: asyncio.subprocess.Process) -> None:
        if proc.stdout is None:
            return
        while True:
            try:
                line = await proc.stdout.readline()
            except Exception:
                break
            if not line:
                break
            text = line.decode("utf-8", errors="replace")
            m = _URL_RE.search(text)
            if m and self._state.public_url is None:
                self._state.public_url = m.group(0).rstrip("/")
                _log.info("tunnel: public URL captured: %s", self._state.public_url)

    async def stop(self) -> str:
        if not self.is_running():
            return "tunnel: not running"
        proc = self._proc
        assert proc is not None
        try:
            if sys.platform == "win32":
                proc.send_signal(signal.CTRL_BREAK_EVENT)  # type: ignore[attr-defined]
            else:
                proc.terminate()
        except Exception:
            _log.warning("tunnel terminate raised", exc_info=True)
        try:
            await asyncio.wait_for(proc.wait(), timeout=5)
        except TimeoutError:
            try:
                proc.kill()
            except Exception:
                pass
            await proc.wait()
        self._proc = None
        self._state.public_url = None
        self._state.started_at = None
        if self._url_task is not None:
            self._url_task.cancel()
            self._url_task = None
        return "tunnel: stopped"

    async def cleanup(self) -> None:
        # Note: per sub-spec 14 §1.6, the tunnel subprocess outlives harbin
        # by design. We only cancel our reader tasks here.
        if self._url_task is not None:
            self._url_task.cancel()
            self._url_task = None
