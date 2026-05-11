"""/cancel <job-id> — sub-spec 13 §3.4."""

from __future__ import annotations

import argparse
from typing import TYPE_CHECKING

from harbin.errors import UserError
from harbin.repl.commands._base import Command

if TYPE_CHECKING:  # pragma: no cover
    from harbin.context import AppContext


class CancelCommand(Command):
    name = "cancel"
    one_line = "/cancel <job-id>         — abort a running or queued job"

    def build_parser(self) -> argparse.ArgumentParser:
        p = argparse.ArgumentParser(prog="/cancel", add_help=False)
        p.add_argument("job_id")
        return p

    async def execute(self, ctx: AppContext, args: list[str]) -> None:
        ns = self.parse(args)
        ok, msg = await ctx.runner.cancel(ns.job_id)
        if not ok:
            raise UserError(code="user.cancel", message=msg)
        ctx.console_writer(msg)
