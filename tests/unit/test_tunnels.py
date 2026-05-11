"""Tests for harbin.web.tunnels.TunnelManager.

devtunnel is not assumed to be installed; we mock or skip auth paths.
"""

from __future__ import annotations

import asyncio
import sys

import pytest

from harbin.web.tunnels import _URL_RE, TunnelManager


def test_url_regex_matches_devtunnels_ms() -> None:
    sample = "Connect via https://ab12-cd-34.usw3.devtunnels.ms/ ok\n"
    m = _URL_RE.search(sample)
    assert m is not None
    assert m.group(0).startswith("https://")
    assert "devtunnels.ms" in m.group(0)


def test_url_regex_case_insensitive() -> None:
    assert _URL_RE.search("https://X.Y.DEVTUNNELS.MS") is not None


@pytest.mark.asyncio
async def test_status_when_not_running() -> None:
    t = TunnelManager(devtunnel_path="devtunnel")
    assert t.is_running() is False
    assert t.status() == "tunnel: not running"


@pytest.mark.asyncio
async def test_start_without_binary_reports_error(tmp_path) -> None:
    # Use a binary name that definitely doesn't resolve.
    t = TunnelManager(devtunnel_path="this-binary-does-not-exist-xyz")
    msg = await t.start(port=12345, tunnel_id=None, allow_anonymous=True)
    assert "devtunnel binary not found" in msg
    assert t.is_running() is False


@pytest.mark.asyncio
async def test_capture_url_writes_state() -> None:
    """Drive _capture_url directly with a mocked stream reader."""
    t = TunnelManager(devtunnel_path="devtunnel")
    reader = asyncio.StreamReader()
    reader.feed_data(b"some noise\n")
    reader.feed_data(b"Tunnel ready at https://aa-bb.usw.devtunnels.ms\n")
    reader.feed_eof()

    class _FakeProc:
        stdout = reader
        returncode = None

        def kill(self) -> None: ...
        async def wait(self) -> int:
            return 0

    fake = _FakeProc()
    await t._capture_url(fake)  # type: ignore[arg-type]
    assert t.state.public_url == "https://aa-bb.usw.devtunnels.ms"


@pytest.mark.asyncio
async def test_stop_when_not_running_short_circuits() -> None:
    t = TunnelManager(devtunnel_path="devtunnel")
    msg = await t.stop()
    assert msg == "tunnel: not running"


@pytest.mark.asyncio
async def test_cleanup_is_safe_when_not_running() -> None:
    t = TunnelManager(devtunnel_path="devtunnel")
    await t.cleanup()


@pytest.mark.asyncio
async def test_start_auth_failure_path(monkeypatch, tmp_path) -> None:
    """If the devtunnel binary exists but `user show` fails, we should
    report a login hint without spawning `host`."""
    if sys.platform == "win32":
        stub = tmp_path / "devtunnel.bat"
        stub.write_text("@echo off\nexit /b 1\n", encoding="utf-8")
    else:
        stub = tmp_path / "devtunnel"
        stub.write_text("#!/bin/sh\nexit 1\n", encoding="utf-8")
        stub.chmod(0o755)
    monkeypatch.setenv("PATH", f"{tmp_path}{':' if sys.platform != 'win32' else ';'}")
    t = TunnelManager(devtunnel_path=str(stub))
    msg = await t.start(port=12345, tunnel_id=None, allow_anonymous=True)
    lower = msg.lower()
    assert "not logged in" in lower or "user show" in lower or "binary" in lower
    assert t.is_running() is False
