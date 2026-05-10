"""/exit — sub-spec 13 §3.10."""

from __future__ import annotations

from typing import TYPE_CHECKING

from harbin.repl.commands._base import Command

if TYPE_CHECKING:  # pragma: no cover
    from harbin.context import AppContext


class ExitCommand(Command):
    name = "exit"
    one_line = "/exit                    — confirm + quit"

    async def execute(self, ctx: AppContext, args: list[str]) -> None:
        active = await ctx.store.list_active_jobs()
        ctx.console_writer(f"shutting down… ({len(active)} active job(s) will be cancelled)")
        ctx.request_shutdown()
