"""/logs <job-id> [-f] [-n N] — sub-spec 13 §3.3."""

from __future__ import annotations

import argparse
from typing import TYPE_CHECKING

from harbin.errors import UserError
from harbin.repl.commands._base import Command

if TYPE_CHECKING:  # pragma: no cover
    from harbin.context import AppContext


class LogsCommand(Command):
    name = "logs"
    one_line = "/logs <job-id> [-f] [-n N] — tail a job's captured stdio"

    def build_parser(self) -> argparse.ArgumentParser:
        p = argparse.ArgumentParser(prog="/logs", add_help=False)
        p.add_argument("job_id")
        p.add_argument("-f", "--follow", action="store_true")
        p.add_argument("-n", type=int, default=200)
        return p

    async def execute(self, ctx: AppContext, args: list[str]) -> None:
        ns = self.parse(args)
        job = await ctx.store.get_job_by_short_id(ns.job_id)
        if job is None:
            raise UserError(
                code="user.unknown_job",
                message=f"unknown job id '{ns.job_id}'",
            )
        chunks = await ctx.store.tail_log_chunks(job.id, n=ns.n)
        if not chunks:
            ctx.console_writer(f"(no log chunks for #{job.short_id})")
            return
        for c in chunks:
            tag = "" if c.stream == "stdout" else f"[{c.stream}] "
            ctx.console_writer(f"{tag}{c.text.rstrip()}")
        if ns.follow:
            ctx.console_writer(f"[muted](open #{job.short_id} via alt+N for live follow)[/muted]")
