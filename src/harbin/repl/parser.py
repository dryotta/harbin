"""REPL parser & dispatcher (sub-spec 13 §1)."""

from __future__ import annotations

import difflib
import re
import shlex
from typing import TYPE_CHECKING

from harbin.errors import UserError
from harbin.fleet.models import FLEET_NAME_RE
from harbin.repl.commands._base import Command

if TYPE_CHECKING:  # pragma: no cover
    from harbin.context import AppContext


def build_registry() -> dict[str, Command]:
    """Construct the closed registry of slash commands."""
    from harbin.repl.commands.artifacts import ArtifactsCommand
    from harbin.repl.commands.cancel import CancelCommand
    from harbin.repl.commands.config import ConfigCommand
    from harbin.repl.commands.exit import ExitCommand
    from harbin.repl.commands.help import HelpCommand
    from harbin.repl.commands.jobs import JobsCommand
    from harbin.repl.commands.logs import LogsCommand
    from harbin.repl.commands.schedule import ScheduleCommand
    from harbin.repl.commands.sync import SyncCommand
    from harbin.repl.commands.tunnel import TunnelCommand
    from harbin.repl.commands.web import WebCommand

    return {
        "help": HelpCommand(),
        "jobs": JobsCommand(),
        "logs": LogsCommand(),
        "cancel": CancelCommand(),
        "artifacts": ArtifactsCommand(),
        "sync": SyncCommand(),
        "schedule": ScheduleCommand(),
        "web": WebCommand(),
        "tunnel": TunnelCommand(),
        "config": ConfigCommand(),
        "exit": ExitCommand(),
    }


_AT_LINE = re.compile(r"^@(\S+)\s*(.*)$", re.DOTALL)


async def dispatch(ctx: AppContext, line: str, registry: dict[str, Command]) -> None:
    """Parse ``line`` and dispatch to the matching command.

    Output (including errors) is routed through ``ctx.console_writer``.
    """
    line = line.strip()
    if not line:
        return

    first = line[0]
    if first == "@":
        await _at_mention(ctx, line)
        return
    if first != "/":
        ctx.console_writer("[error]error: start with '/' or '@'[/error]")
        return

    # slash
    body = line[1:]
    try:
        tokens = shlex.split(body, posix=True)
    except ValueError as e:
        ctx.console_writer(f"[error]parse error: {e}[/error]")
        return
    if not tokens:
        ctx.console_writer("[error]error: empty command[/error]")
        return
    name = tokens[0]
    args = tokens[1:]
    cmd = registry.get(name)
    if cmd is None:
        guess = difflib.get_close_matches(name, list(registry), n=1, cutoff=0.6)
        if guess:
            ctx.console_writer(
                f"[error]unknown command '/{name}'. did you mean /{guess[0]}?[/error]"
            )
        else:
            ctx.console_writer(f"[error]unknown command '/{name}'[/error]")
        return
    try:
        await cmd.execute(ctx, args)
    except UserError as e:
        ctx.console_writer(f"[error]{e.message or e}[/error]")
    except Exception as e:  # pragma: no cover - defensive
        ctx.console_writer(f"[error]internal error in /{name}: {e}[/error]")


async def _at_mention(ctx: AppContext, line: str) -> None:
    m = _AT_LINE.match(line)
    if m is None:
        ctx.console_writer("[error]error: expected '@<fleet> <prompt>'[/error]")
        return
    name = m.group(1)
    prompt = (m.group(2) or "").strip()
    if not FLEET_NAME_RE.fullmatch(name):
        ctx.console_writer(
            f"[error]invalid fleet name '{name}': must match {FLEET_NAME_RE.pattern}[/error]"
        )
        return
    if not prompt:
        ctx.console_writer("[error]prompt is empty[/error]")
        return
    fleet = await ctx.store.get_fleet_by_name(name)
    if fleet is None:
        names = [f.name for f in await ctx.store.list_fleets()]
        guess = difflib.get_close_matches(name, names, n=1, cutoff=0.6)
        if guess:
            ctx.console_writer(f"[error]unknown fleet '{name}'. did you mean {guess[0]}?[/error]")
        else:
            ctx.console_writer(f"[error]unknown fleet '{name}'[/error]")
        return
    row = await ctx.runner.enqueue(fleet=fleet, prompt=prompt, source="repl", task_label="adhoc")
    ctx.console_writer(f"queued #{row.short_id}  {fleet.name}·adhoc")
