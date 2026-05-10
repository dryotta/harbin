"""REPL command base + registry (sub-spec 13)."""

from __future__ import annotations

import argparse
from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover
    from harbin.context import AppContext


class Command(ABC):
    """Slash-command base class.

    Subclasses provide ``name``, ``help_text``, and either an
    ``argparse.ArgumentParser`` via :meth:`build_parser` or override
    :meth:`execute` directly for argparse-free commands.
    """

    name: str = ""
    aliases: tuple[str, ...] = ()
    one_line: str = ""

    def help_text(self) -> str:
        return self.one_line

    def build_parser(self) -> argparse.ArgumentParser:
        p = argparse.ArgumentParser(prog=f"/{self.name}", add_help=False)
        return p

    def parse(self, args: list[str]) -> argparse.Namespace:
        parser = self.build_parser()
        try:
            return parser.parse_args(args)
        except SystemExit as e:  # argparse exits on error
            raise ValueError(parser.format_usage().strip()) from e

    @abstractmethod
    async def execute(self, ctx: AppContext, args: list[str]) -> None:  # pragma: no cover
        ...
