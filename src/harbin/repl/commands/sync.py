"""/sync [fleet] — sub-spec 13 §3.6."""

from __future__ import annotations

import argparse
from typing import TYPE_CHECKING

from harbin.errors import UserError
from harbin.repl.commands._base import Command

if TYPE_CHECKING:  # pragma: no cover
    from harbin.context import AppContext


class SyncCommand(Command):
    name = "sync"
    one_line = "/sync [fleet]            — force git fetch + ff"

    def build_parser(self) -> argparse.ArgumentParser:
        p = argparse.ArgumentParser(prog="/sync", add_help=False)
        p.add_argument("fleet", nargs="?", default=None)
        return p

    async def execute(self, ctx: AppContext, args: list[str]) -> None:
        ns = self.parse(args)
        states = list(ctx.dock_manager.states.values())
        if ns.fleet:
            states = [s for s in states if s.row.name == ns.fleet]
            if not states:
                raise UserError(
                    code="user.unknown_fleet",
                    message=f"unknown fleet '{ns.fleet}'",
                )
        for s in states:
            summary = await ctx.dock_manager.sync_once(s)
            ctx.console_writer(summary)
