"""Tests for the REPL parser & dispatch."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    pass


class _RecordingCtx:
    """Minimal stand-in for AppContext for parser tests."""

    def __init__(self) -> None:
        self.console: list[str] = []
        self.console_writer = self.console.append

        # Minimal store stub
        class _Store:
            async def list_fleets(self):
                return []

            async def get_fleet_by_name(self, name):
                return None

        self.store = _Store()


@pytest.mark.asyncio
async def test_dispatch_unknown_command() -> None:
    from harbin.repl.parser import build_registry, dispatch

    ctx = _RecordingCtx()
    reg = build_registry()
    await dispatch(ctx, "/jobss", reg)  # type: ignore[arg-type]
    assert any("did you mean /jobs" in line for line in ctx.console)


@pytest.mark.asyncio
async def test_dispatch_help() -> None:
    from harbin.repl.parser import build_registry, dispatch

    ctx = _RecordingCtx()
    reg = build_registry()
    await dispatch(ctx, "/help", reg)  # type: ignore[arg-type]
    text = "\n".join(ctx.console)
    assert "/help" in text
    assert "/jobs" in text
    assert "/cancel" in text


@pytest.mark.asyncio
async def test_dispatch_bad_first_char() -> None:
    from harbin.repl.parser import build_registry, dispatch

    ctx = _RecordingCtx()
    reg = build_registry()
    await dispatch(ctx, "hello", reg)  # type: ignore[arg-type]
    assert any("start with" in line for line in ctx.console)


@pytest.mark.asyncio
async def test_dispatch_at_mention_unknown_fleet() -> None:
    from harbin.repl.parser import build_registry, dispatch

    ctx = _RecordingCtx()
    reg = build_registry()
    await dispatch(ctx, "@nonexistent hello", reg)  # type: ignore[arg-type]
    assert any("unknown fleet" in line for line in ctx.console)
