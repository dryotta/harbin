"""Tests for :mod:`harbin.web.serve_manager` and the ``/web`` slash command.

Boots a stub ``harbin serve`` (a tiny Python TCP listener) so the
manager exercises the real ``asyncio.create_subprocess_exec`` +
port-bind-wait path without depending on the full harbin CLI in CI.
"""

from __future__ import annotations

import socket
import sys
import textwrap

import pytest

from harbin.web.serve_manager import WebServeManager

pytestmark = pytest.mark.asyncio


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


# A tiny script we run instead of the real harbin CLI. It just binds the
# requested port and sleeps until killed — enough for the manager's
# port-bind-wait + status logic to exercise the real subprocess path.
_STUB = textwrap.dedent(
    """
    import socket, sys, time, argparse
    p = argparse.ArgumentParser()
    p.add_argument("serve", nargs="?")  # absorb the literal 'serve'
    p.add_argument("--port", type=int, required=True)
    p.add_argument("--host", default="127.0.0.1")
    ns = p.parse_args()
    s = socket.socket()
    s.bind((ns.host, ns.port))
    s.listen(8)
    sys.stdout.write(f"bound {ns.host}:{ns.port}\\n")
    sys.stdout.flush()
    try:
        while True:
            time.sleep(0.5)
    except KeyboardInterrupt:
        pass
    """
)


def _write_stub(tmp_path):
    p = tmp_path / "fake_harbin.py"
    p.write_text(_STUB, encoding="utf-8")
    return p


# ───────────────────────── WebServeManager ────────────────────────


async def test_status_when_not_running() -> None:
    mgr = WebServeManager()
    assert not mgr.is_running()
    assert mgr.url() is None
    assert mgr.status() == "web: not running"


async def test_start_then_stop_lifecycle(tmp_path) -> None:
    """Real subprocess + real port bind."""
    stub = _write_stub(tmp_path)
    port = _free_port()
    mgr = WebServeManager(harbin_argv0=[sys.executable, str(stub)])
    try:
        msg = await mgr.start(port=port, host="127.0.0.1")
        assert "web started" in msg.lower(), msg
        assert mgr.is_running()
        assert mgr.url() == f"http://127.0.0.1:{port}/"
        assert "running" in mgr.status() and str(port) in mgr.status()
    finally:
        msg = await mgr.stop()
        assert msg == "web: stopped"
        assert not mgr.is_running()
        assert mgr.url() is None


async def test_start_when_port_already_in_use(tmp_path) -> None:
    """Pre-flight: refuse to start when the port is already bound."""
    port = _free_port()
    # Hold the port ourselves.
    s = socket.socket()
    s.bind(("127.0.0.1", port))
    s.listen(1)
    try:
        mgr = WebServeManager(harbin_argv0=[sys.executable, "-c", "print('unused')"])
        msg = await mgr.start(port=port, host="127.0.0.1")
        assert "in use" in msg.lower(), msg
        assert not mgr.is_running()
    finally:
        s.close()


async def test_start_is_idempotent(tmp_path) -> None:
    """Calling start() twice doesn't spawn a second child."""
    stub = _write_stub(tmp_path)
    port = _free_port()
    mgr = WebServeManager(harbin_argv0=[sys.executable, str(stub)])
    try:
        msg1 = await mgr.start(port=port, host="127.0.0.1")
        assert "started" in msg1.lower()
        # Second call: already running → status message, NOT another spawn.
        msg2 = await mgr.start(port=port, host="127.0.0.1")
        assert "running" in msg2.lower()
        assert mgr.is_running()
    finally:
        await mgr.stop()


async def test_stop_is_safe_when_not_running() -> None:
    mgr = WebServeManager()
    msg = await mgr.stop()
    assert msg == "web: not running"


async def test_cleanup_stops_running_server(tmp_path) -> None:
    stub = _write_stub(tmp_path)
    port = _free_port()
    mgr = WebServeManager(harbin_argv0=[sys.executable, str(stub)])
    await mgr.start(port=port, host="127.0.0.1")
    assert mgr.is_running()
    await mgr.cleanup()
    assert not mgr.is_running()


# ──────────────────────────── /web command ───────────────────────────


async def test_web_command_status_no_args(harbin_paths, monkeypatch) -> None:
    """``/web`` (no args) calls status()."""
    from harbin.repl.commands.web import WebCommand

    class _StubCtx:
        called: list[str] = []
        log: list[str] = []

        class _Mgr:
            async def start(self, *, port, host):
                _StubCtx.called.append(f"start({port},{host})")
                return "ok"

            async def stop(self):
                _StubCtx.called.append("stop")
                return "stopped"

            def status(self):
                _StubCtx.called.append("status")
                return "web: not running"

        web_server = _Mgr()
        console_writer = staticmethod(lambda s: _StubCtx.log.append(s))

        class _Cfg:
            class web:
                port = 8080
                host = "127.0.0.1"

        config = _Cfg()

    cmd = WebCommand()
    await cmd.execute(_StubCtx(), [])
    assert _StubCtx.called == ["status"]
    assert _StubCtx.log == ["web: not running"]


async def test_web_command_start_uses_config_defaults(harbin_paths) -> None:
    from harbin.repl.commands.web import WebCommand

    captured: dict = {}

    class _StubCtx:
        class _Mgr:
            async def start(self, *, port, host):
                captured["port"] = port
                captured["host"] = host
                return f"web started on http://{host}:{port}/"

            async def stop(self):  # pragma: no cover - not exercised here
                return "stopped"

            def status(self):  # pragma: no cover
                return "web: not running"

        web_server = _Mgr()
        log: list[str] = []
        console_writer = staticmethod(lambda s: _StubCtx.log.append(s))

        class _Cfg:
            class web:
                port = 8081
                host = "127.0.0.1"

        config = _Cfg()

    await WebCommand().execute(_StubCtx(), ["start"])
    assert captured == {"port": 8081, "host": "127.0.0.1"}
    assert _StubCtx.log[-1].startswith("web started on")


async def test_web_command_start_with_flag_overrides(harbin_paths) -> None:
    from harbin.repl.commands.web import WebCommand

    captured: dict = {}

    class _StubCtx:
        class _Mgr:
            async def start(self, *, port, host):
                captured["port"] = port
                captured["host"] = host
                return "ok"

            async def stop(self):  # pragma: no cover
                return "stopped"

            def status(self):  # pragma: no cover
                return ""

        web_server = _Mgr()
        log: list[str] = []
        console_writer = staticmethod(lambda s: _StubCtx.log.append(s))

        class _Cfg:
            class web:
                port = 8080
                host = "127.0.0.1"

        config = _Cfg()

    await WebCommand().execute(_StubCtx(), ["start", "--port", "9000", "--host", "0.0.0.0"])
    assert captured == {"port": 9000, "host": "0.0.0.0"}


async def test_web_command_stop_invokes_manager(harbin_paths) -> None:
    from harbin.repl.commands.web import WebCommand

    called: list[str] = []

    class _StubCtx:
        class _Mgr:
            async def start(self, *, port, host):  # pragma: no cover
                return ""

            async def stop(self):
                called.append("stop")
                return "web: stopped"

            def status(self):  # pragma: no cover
                return ""

        web_server = _Mgr()
        log: list[str] = []
        console_writer = staticmethod(lambda s: _StubCtx.log.append(s))

        class _Cfg:
            class web:
                port = 8080
                host = "127.0.0.1"

        config = _Cfg()

    await WebCommand().execute(_StubCtx(), ["stop"])
    assert called == ["stop"]
    assert _StubCtx.log[-1] == "web: stopped"


async def test_web_command_unknown_action(harbin_paths) -> None:
    """argparse maps `choices` violations to a UserError, not a bare ValueError."""
    from harbin.errors import UserError
    from harbin.repl.commands.web import WebCommand

    class _StubCtx:
        class _Mgr:  # pragma: no cover - never reached
            async def start(self, *, port, host):
                return ""

            async def stop(self):
                return ""

            def status(self):
                return ""

        web_server = _Mgr()
        console_writer = staticmethod(lambda s: None)

        class _Cfg:
            class web:
                port = 8080
                host = "127.0.0.1"

        config = _Cfg()

    with pytest.raises(UserError):
        await WebCommand().execute(_StubCtx(), ["fly"])


# ────────────────────────── known-commands wiring ────────────────────


def test_web_is_registered_in_parser_and_help() -> None:
    from harbin.repl.commands.help import _DESC
    from harbin.repl.parser import build_registry
    from harbin.repl.suggester import KNOWN_COMMANDS

    reg = build_registry()
    assert "web" in reg
    assert reg["web"].name == "web"
    assert "web" in KNOWN_COMMANDS
    assert "web" in _DESC
    assert "/web" in _DESC["web"]


async def test_web_suggester_completes_subcommands(harbin_paths, store) -> None:
    """Type `/web sta`, get `/web start`."""
    from harbin.repl.suggester import HarbinSuggester

    class _Ctx:
        pass

    ctx = _Ctx()
    ctx.store = store  # type: ignore[attr-defined]
    sugg = HarbinSuggester(ctx)  # type: ignore[arg-type]
    suggestion = await sugg.get_suggestion("/web sta")
    assert suggestion == "/web start"
    assert await sugg.get_suggestion("/we") == "/web"
