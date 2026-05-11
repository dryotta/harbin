"""Tests for harbin.paths."""

from __future__ import annotations

from pathlib import Path

from harbin.paths import atomic_write_text, ensure_all, resolve


def test_resolve_uses_harbin_home(harbin_home: Path) -> None:
    p = resolve()
    assert p.config_dir == harbin_home / "config"
    assert p.data_dir == harbin_home / "data"
    assert p.cache_dir == harbin_home / "cache"
    assert p.log_dir == harbin_home / "logs"


def test_ensure_all_creates_dirs(harbin_home: Path) -> None:
    p = resolve()
    ensure_all(p)
    assert p.config_dir.exists()
    assert p.data_dir.exists()
    assert p.cache_dir.exists()
    assert p.log_dir.exists()


def test_atomic_write_text(tmp_path: Path) -> None:
    target = tmp_path / "x" / "y" / "file.txt"
    atomic_write_text(target, "hello", encoding="utf-8")
    assert target.read_text(encoding="utf-8") == "hello"
    atomic_write_text(target, "replaced")
    assert target.read_text(encoding="utf-8") == "replaced"
    # no leftover tmp file
    assert not (target.with_suffix(target.suffix + ".tmp")).exists()
