"""Smoke test for ``harbin serve``.

Verifies that the textual-serve binding actually starts and binds the
requested port. We don't render the browser-side terminal — just confirm
the HTTP layer is up.
"""

from __future__ import annotations

import os
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

import pytest


def _find_free_port() -> int:
    """Bind a socket to port 0 and report what the kernel gave us."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def _wait_for_http(url: str, timeout: float = 20.0) -> bool:
    """Poll ``url`` until it responds at all (any HTTP status code)."""
    end = time.time() + timeout
    while time.time() < end:
        try:
            with urllib.request.urlopen(url, timeout=1) as resp:
                # textual-serve returns 200 with the HTML shell.
                return resp.status == 200
        except urllib.error.URLError:
            time.sleep(0.2)
        except OSError:
            time.sleep(0.2)
    return False


@pytest.mark.skipif(
    not (Path(__file__).resolve().parents[2] / ".venv").exists() and not sys.executable,
    reason="needs the project's Python on PATH",
)
def test_harbin_serve_binds_port(tmp_path) -> None:
    """``harbin serve --port <free> --host 127.0.0.1`` binds and serves."""
    port = _find_free_port()
    env = os.environ.copy()
    env["HARBIN_HOME"] = str(tmp_path / "home")
    proc = subprocess.Popen(
        [sys.executable, "-m", "harbin", "serve", "--port", str(port), "--host", "127.0.0.1"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=env,
    )
    try:
        ok = _wait_for_http(f"http://127.0.0.1:{port}/", timeout=20)
        assert ok, "harbin serve did not bind in time"
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=5)
