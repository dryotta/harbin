"""/jobs [--all] — sub-spec 13 §3.2."""

from __future__ import annotations

import datetime as _dt
from typing import TYPE_CHECKING

from harbin.repl.commands._base import Command
from harbin.tui.theme import STATUS_GLYPHS

if TYPE_CHECKING:  # pragma: no cover
    from harbin.context import AppContext


def _parse_iso(s: str | None) -> _dt.datetime | None:
    if not s:
        return None
    try:
        return _dt.datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None


def _elapsed(started: str | None, ended: str | None) -> str:
    s = _parse_iso(started)
    if s is None:
        return "—"
    e = _parse_iso(ended) or _dt.datetime.now(_dt.UTC)
    delta = e - s
    secs = int(delta.total_seconds())
    return f"{secs // 60:02d}:{secs % 60:02d}"


class JobsCommand(Command):
    name = "jobs"
    one_line = "/jobs [--all]            — list active + recent jobs"

    async def execute(self, ctx: AppContext, args: list[str]) -> None:
        show_all = "--all" in args
        rows = (
            await ctx.store.list_all_jobs(limit=200)
            if show_all
            else await ctx.store.list_recent_jobs(limit=50)
        )
        if not rows:
            ctx.console_writer("(no jobs)")
            return
        fleets = {f.id: f.name for f in await ctx.store.list_fleets()}
        # Build a {task_pk: task_id} map once so each row resolves in O(1).
        tasks = {t.id: t.task_id for t in await ctx.store.list_tasks()}
        ctx.console_writer(
            f"{'status':10} {'fleet':28} {'task':14} {'#id':8} {'started':22} {'elapsed':>8}"
        )
        for j in rows:
            glyph = STATUS_GLYPHS.get(j.status, "·")
            fname = fleets.get(j.fleet_id, f"#{j.fleet_id}")
            task_label = tasks.get(j.task_pk, "adhoc") if j.task_pk is not None else "adhoc"
            line = (
                f"{glyph}{j.status:<9} "
                f"{fname[:28]:<28} "
                f"{task_label[:14]:<14} "
                f"#{j.short_id:<7} "
                f"{(j.started_at or '—')[:22]:<22} "
                f"{_elapsed(j.started_at, j.ended_at):>8}"
            )
            ctx.console_writer(line)
