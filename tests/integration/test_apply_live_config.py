"""Tests for the apply-live config propagation path used by the Config modal."""

from __future__ import annotations

from pathlib import Path

import pytest

from harbin.app import AppCore
from harbin.config.loader import load_config

pytestmark = pytest.mark.asyncio


async def test_apply_live_config_propagates_to_subsystems(tmp_path, monkeypatch) -> None:
    """AppCore.apply_live_config should push tick/timezone/log/runner updates."""
    monkeypatch.setenv("HARBIN_HOME", str(tmp_path))
    core = await AppCore.startup()
    try:
        cfg = core.config
        original_tick = cfg.scheduler.tick_seconds
        new_tick = 60 if original_tick != 60 else 30
        # Build a mutated config
        new_cfg = cfg.model_copy(deep=True)
        new_cfg.scheduler.tick_seconds = new_tick
        new_cfg.agent_runner.concurrency.global_cap = 7
        new_cfg.agent_runner.kill_grace_seconds = 17
        new_cfg.ui.log_verbosity = "debug"

        core.apply_live_config(new_cfg)

        assert core.scheduler is not None
        assert core.scheduler._tick_seconds == new_tick  # type: ignore[attr-defined]
        assert core.runner is not None
        # Internal cap should now be the new one.
        assert core.runner._global_cap == 7  # type: ignore[attr-defined]
        assert core.runner._kill_grace == 17  # type: ignore[attr-defined]
        # In-memory snapshot updated.
        assert core.config.scheduler.tick_seconds == new_tick

        # Log level
        import logging

        from harbin.logging import ROOT_NAME

        assert logging.getLogger(ROOT_NAME).level == logging.DEBUG
    finally:
        core.request_shutdown()
        await core.shutdown()


async def test_config_watch_reload_triggers_apply_live(tmp_path, monkeypatch) -> None:
    """Writing config.yaml on disk fires the watcher which re-applies live."""
    import asyncio

    monkeypatch.setenv("HARBIN_HOME", str(tmp_path))
    core = await AppCore.startup()
    try:
        assert core.scheduler is not None
        before = core.scheduler._tick_seconds  # type: ignore[attr-defined]
        cfg_path: Path = core.paths.config_dir / "config.yaml"
        text = cfg_path.read_text(encoding="utf-8")
        new_text = text.replace(f"tick_seconds: {before}", "tick_seconds: 47", 1)
        if "tick_seconds: 47" not in new_text:
            # tick_seconds wasn't present (default elided). Append override.
            new_text += "\nscheduler:\n  tick_seconds: 47\n"
        cfg_path.write_text(new_text, encoding="utf-8")
        # Wait up to ~3 s for debounce + reload.
        for _ in range(60):
            if core.scheduler._tick_seconds == 47:  # type: ignore[attr-defined]
                break
            await asyncio.sleep(0.05)
        assert core.scheduler._tick_seconds == 47  # type: ignore[attr-defined]
        # The reloaded in-memory snapshot should match too.
        # (Reload via load_config to verify the file was valid.)
        reloaded = load_config(cfg_path)
        assert reloaded.scheduler.tick_seconds == 47
    finally:
        core.request_shutdown()
        await core.shutdown()
