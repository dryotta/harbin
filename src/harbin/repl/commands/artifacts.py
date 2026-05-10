"""/artifacts <fleet> [path] — sub-spec 13 §3.5."""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import TYPE_CHECKING

from harbin.errors import UserError
from harbin.repl.commands._base import Command

if TYPE_CHECKING:  # pragma: no cover
    from harbin.context import AppContext


class ArtifactsCommand(Command):
    name = "artifacts"
    one_line = "/artifacts <fleet> [path]  — browse a fleet's artifact tree"

    def build_parser(self) -> argparse.ArgumentParser:
        p = argparse.ArgumentParser(prog="/artifacts", add_help=False)
        p.add_argument("fleet")
        p.add_argument("path", nargs="?", default=None)
        return p

    async def execute(self, ctx: AppContext, args: list[str]) -> None:
        ns = self.parse(args)
        fleet = await ctx.store.get_fleet_by_name(ns.fleet)
        if fleet is None:
            raise UserError(
                code="user.unknown_fleet",
                message=f"unknown fleet '{ns.fleet}'",
            )
        root = ctx.artifacts.root / fleet.name
        target = root
        if ns.path:
            if ".." in Path(ns.path).parts:
                raise UserError(code="user.traversal", message="path traversal not allowed")
            target = (root / ns.path).resolve()
            if not str(target).startswith(str(root.resolve())):
                raise UserError(code="user.traversal", message="path escapes fleet root")
        if not target.exists():
            ctx.console_writer(
                f"[muted]no artifacts at {target}[/muted] (path will appear after first job)"
            )
            return
        # Plain listing — the panel is mounted by the TUI; the slash command
        # gives a quick textual listing as well.
        if target.is_file():
            ctx.console_writer(f"file: {target}")
            return
        ctx.console_writer(f"{target}:")
        for entry in sorted(target.iterdir()):
            kind = "/" if entry.is_dir() else ""
            ctx.console_writer(f"  {entry.name}{kind}")
