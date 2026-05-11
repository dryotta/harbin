"""/config — sub-spec 13 §3.9. Opens the config modal screen."""

from __future__ import annotations

from typing import TYPE_CHECKING

from harbin.repl.commands._base import Command

if TYPE_CHECKING:  # pragma: no cover
    from harbin.context import AppContext


class ConfigCommand(Command):
    name = "config"
    one_line = "/config                  — open the multi-page settings screen"

    async def execute(self, ctx: AppContext, args: list[str]) -> None:
        # The actual modal push happens in the TUI app — for non-TUI usage
        # (e.g. unit tests) we just emit a console line.
        from harbin.tui.app import HarbinApp

        app = HarbinApp._instance  # type: ignore[attr-defined]
        if app is None:
            ctx.console_writer("[muted](config modal requires running TUI)[/muted]")
            return
        from harbin.tui.screens.config_modal import ConfigModalScreen

        app.push_screen(ConfigModalScreen(ctx))
