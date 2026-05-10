"""/help [cmd] — sub-spec 13 §3.1."""

from __future__ import annotations

import difflib
from typing import TYPE_CHECKING

from harbin.repl.commands._base import Command
from harbin.repl.suggester import KNOWN_COMMANDS

if TYPE_CHECKING:  # pragma: no cover
    from harbin.context import AppContext


_DESC: dict[str, str] = {
    "help": "/help [cmd]              — show help, optionally for one command",
    "jobs": "/jobs [--all]            — list active + recent jobs",
    "logs": "/logs <job-id> [-f] [-n N] — tail a job's captured stdio",
    "cancel": "/cancel <job-id>         — abort a running or queued job",
    "artifacts": "/artifacts <fleet> [path]  — browse a fleet's artifact tree",
    "sync": "/sync [fleet]            — force git fetch + ff",
    "schedule": "/schedule [fleet]        — show cron + next-fire times",
    "tunnel": "/tunnel [start|stop|status] — manage devtunnel host",
    "config": "/config                  — open the multi-page settings screen",
    "exit": "/exit                    — confirm + quit",
}


class HelpCommand(Command):
    name = "help"
    one_line = _DESC["help"]

    def help_text(self) -> str:
        return "/help — list every known slash command.\n/help <cmd> — show that command's usage."

    async def execute(self, ctx: AppContext, args: list[str]) -> None:
        if not args:
            for k in KNOWN_COMMANDS:
                ctx.console_writer(_DESC[k])
            ctx.console_writer("Use '@<fleet> <prompt>' to enqueue an ad-hoc job.")
            return
        target = args[0].lstrip("/")
        if target in _DESC:
            ctx.console_writer(_DESC[target])
            return
        guess = difflib.get_close_matches(target, KNOWN_COMMANDS, n=1, cutoff=0.6)
        if guess:
            ctx.console_writer(
                f"[error]unknown command '{target}'. did you mean /{guess[0]}?[/error]"
            )
        else:
            ctx.console_writer(f"[error]unknown command '{target}'[/error]")
