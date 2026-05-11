"""/web [start|stop|status] — control the local textual-serve HTTP layer.

Sub-spec 14 §1 specifies ``harbin serve`` as the CLI entrypoint that
binds the local HTTP/WebSocket port; this slash command is the in-TUI
toggle. The companion ``/tunnel`` command then fronts the local port
with a public ``devtunnels.ms`` URL.
"""

from __future__ import annotations

import argparse
from typing import TYPE_CHECKING

from harbin.errors import UserError
from harbin.repl.commands._base import Command

if TYPE_CHECKING:  # pragma: no cover
    from harbin.context import AppContext


class WebCommand(Command):
    name = "web"
    one_line = "/web [start|stop|status]   — toggle the local web UI server"

    def build_parser(self) -> argparse.ArgumentParser:
        p = argparse.ArgumentParser(prog="/web", add_help=False)
        p.add_argument("action", nargs="?", default="status", choices=["start", "stop", "status"])
        p.add_argument("--port", type=int, default=None)
        p.add_argument("--host", type=str, default=None)
        return p

    async def execute(self, ctx: AppContext, args: list[str]) -> None:
        try:
            ns = self.parse(args)
        except ValueError as e:
            raise UserError(code="user.usage", message=str(e)) from e
        manager = ctx.web_server
        if ns.action == "start":
            port = ns.port if ns.port is not None else ctx.config.web.port
            host = ns.host if ns.host is not None else ctx.config.web.host
            msg = await manager.start(port=port, host=host)
            ctx.console_writer(msg)
        elif ns.action == "stop":
            ctx.console_writer(await manager.stop())
        else:
            ctx.console_writer(manager.status())
