"""/tunnel [start|stop|status] — sub-spec 14 §2."""

from __future__ import annotations

import argparse
from typing import TYPE_CHECKING

from harbin.errors import UserError
from harbin.repl.commands._base import Command

if TYPE_CHECKING:  # pragma: no cover
    from harbin.context import AppContext


class TunnelCommand(Command):
    name = "tunnel"
    one_line = "/tunnel [start|stop|status] — manage devtunnel host"

    def build_parser(self) -> argparse.ArgumentParser:
        p = argparse.ArgumentParser(prog="/tunnel", add_help=False)
        p.add_argument("action", nargs="?", default="status", choices=["start", "stop", "status"])
        return p

    async def execute(self, ctx: AppContext, args: list[str]) -> None:
        try:
            ns = self.parse(args)
        except ValueError as e:
            raise UserError(code="user.usage", message=str(e)) from e
        if ns.action == "start":
            res = await ctx.tunnels.start(
                port=ctx.config.web.port,
                tunnel_id=ctx.config.tunnels.tunnel_id,
                allow_anonymous=ctx.config.tunnels.allow_anonymous,
            )
            ctx.console_writer(res)
        elif ns.action == "stop":
            res = await ctx.tunnels.stop()
            ctx.console_writer(res)
        else:
            ctx.console_writer(ctx.tunnels.status())
