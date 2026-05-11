"""Tests for harbin.config.watch.FileWatcher.

Covers regression for B1 (thread-safe scheduling of watchdog callbacks onto
the asyncio loop) and basic debounce behavior.
"""

from __future__ import annotations

import asyncio

import pytest

from harbin.config.watch import FileWatcher, WatchEvent

pytestmark = pytest.mark.asyncio


async def test_watch_fires_after_debounce(tmp_path) -> None:
    target_dir = tmp_path / "harbin"
    target_dir.mkdir()
    target = target_dir / "fleet.yaml"
    target.write_text("name: t\n", encoding="utf-8")

    received: list[WatchEvent] = []
    done = asyncio.Event()

    def cb(evt: WatchEvent) -> None:
        received.append(evt)
        done.set()

    w = FileWatcher()
    try:
        w.watch(target_dir, {"fleet.yaml"}, cb)
        # Modify the file from the loop thread
        target.write_text("name: t\nupdated: true\n", encoding="utf-8")
        await asyncio.wait_for(done.wait(), timeout=3.0)
        assert received, "expected at least one event"
        assert received[0].path.name == "fleet.yaml"
        assert received[0].kind in {"modified", "created"}
    finally:
        w.stop()


async def test_debounce_collapses_burst(tmp_path) -> None:
    target_dir = tmp_path / "harbin"
    target_dir.mkdir()
    target = target_dir / "fleet.yaml"
    target.write_text("v: 0\n", encoding="utf-8")

    received: list[WatchEvent] = []

    def cb(evt: WatchEvent) -> None:
        received.append(evt)

    w = FileWatcher()
    try:
        w.watch(target_dir, {"fleet.yaml"}, cb)
        for i in range(10):
            target.write_text(f"v: {i}\n", encoding="utf-8")
            await asyncio.sleep(0.02)
        # Let the 250 ms debounce expire.
        await asyncio.sleep(0.5)
        # 10 writes in <250 ms should collapse to 1-2 fires, never 10.
        assert 0 < len(received) <= 2
    finally:
        w.stop()


async def test_unwatch_stops_firing(tmp_path) -> None:
    target_dir = tmp_path / "harbin"
    target_dir.mkdir()
    target = target_dir / "fleet.yaml"
    target.write_text("v: 0\n", encoding="utf-8")
    received: list[WatchEvent] = []

    def cb(evt: WatchEvent) -> None:
        received.append(evt)

    w = FileWatcher()
    try:
        w.watch(target_dir, {"fleet.yaml"}, cb)
        target.write_text("v: 1\n", encoding="utf-8")
        await asyncio.sleep(0.4)
        n_before = len(received)
        w.unwatch(target_dir)
        target.write_text("v: 2\n", encoding="utf-8")
        await asyncio.sleep(0.4)
        # No new events after unwatch.
        assert len(received) == n_before
    finally:
        w.stop()


async def test_watch_ignores_other_files(tmp_path) -> None:
    target_dir = tmp_path / "harbin"
    target_dir.mkdir()
    received: list[WatchEvent] = []
    done = asyncio.Event()

    def cb(evt: WatchEvent) -> None:
        received.append(evt)
        done.set()

    w = FileWatcher()
    try:
        w.watch(target_dir, {"fleet.yaml"}, cb)
        # Touching some other file does nothing.
        (target_dir / "notes.txt").write_text("noise", encoding="utf-8")
        try:
            await asyncio.wait_for(done.wait(), timeout=0.5)
        except TimeoutError:
            pass
        assert received == []
    finally:
        w.stop()
