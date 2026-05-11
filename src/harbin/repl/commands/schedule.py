"""/schedule [fleet] — sub-spec 13 §3.7."""

from __future__ import annotations

import argparse
from typing import TYPE_CHECKING

from harbin.errors import UserError
from harbin.repl.commands._base import Command

if TYPE_CHECKING:  # pragma: no cover
    from harbin.context import AppContext


class ScheduleCommand(Command):
    name = "schedule"
    one_line = "/schedule [fleet]        — show cron + next-fire times"

    def build_parser(self) -> argparse.ArgumentParser:
        p = argparse.ArgumentParser(prog="/schedule", add_help=False)
        p.add_argument("fleet", nargs="?", default=None)
        return p

    async def execute(self, ctx: AppContext, args: list[str]) -> None:
        ns = self.parse(args)
        rows = await ctx.scheduler.describe()
        if ns.fleet:
            fleet = await ctx.store.get_fleet_by_name(ns.fleet)
            if fleet is None:
                raise UserError(
                    code="user.unknown_fleet",
                    message=f"unknown fleet '{ns.fleet}'",
                )
            rows = [r for r in rows if r["fleet"] == ns.fleet]
        if not rows:
            ctx.console_writer("(no scheduled tasks)")
            return
        ctx.console_writer(
            f"{'fleet':24} {'task':16} {'cron':16} {'next-fire':24} {'last-fire':24}"
        )
        for r in rows:
            ctx.console_writer(
                f"{str(r['fleet'])[:24]:<24} "
                f"{str(r['task_id'])[:16]:<16} "
                f"{str(r['cron'])[:16]:<16} "
                f"{str(r['next_fire'] or '—')[:24]:<24} "
                f"{str(r['last_fire'] or '—')[:24]:<24}"
            )
