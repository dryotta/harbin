"""Command line widget — wraps Textual ``Input`` with prefix '> '."""

from __future__ import annotations

from textual.suggester import Suggester
from textual.widgets import Input


class CommandLine(Input):
    DEFAULT_CSS = ""

    def __init__(self, suggester: Suggester | None = None) -> None:
        super().__init__(
            placeholder="type /help, /jobs, or @<fleet> <prompt>…",
            suggester=suggester,
            id="commandline",
        )
