"""Integration tests for harbin.samples.add_sample.

Replaces the real GitHub URLs with a local bare-repo fixture so the test
suite never touches the network.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from harbin import samples
from harbin.errors import DockError, UserError

pytestmark = pytest.mark.asyncio


def _make_named_bare(tmp_path: Path, name: str) -> Path:
    """Make a bare remote with a single commit containing
    ``.harbin/fleet.yaml`` whose ``name`` is ``name``."""
    bare = tmp_path / f"{name}.git"
    subprocess.run(
        ["git", "init", "--bare", "--initial-branch=main", str(bare)],
        check=True,
        capture_output=True,
    )
    seed = tmp_path / f"{name}-seed"
    subprocess.run(
        ["git", "init", "--initial-branch=main", str(seed)],
        check=True,
        capture_output=True,
    )
    subprocess.run(["git", "-C", str(seed), "config", "user.email", "t@x"], check=True)
    subprocess.run(["git", "-C", str(seed), "config", "user.name", "t"], check=True)
    (seed / ".harbin").mkdir()
    (seed / ".harbin" / "fleet.yaml").write_text(
        f"name: {name}\ndefault_branch: main\nartifact_policy:\n"
        "  retain: 30d\n  push_back: false\n",
        encoding="utf-8",
    )
    subprocess.run(["git", "-C", str(seed), "add", "."], check=True)
    subprocess.run(
        ["git", "-C", str(seed), "commit", "-m", "init"],
        check=True,
        capture_output=True,
    )
    subprocess.run(["git", "-C", str(seed), "remote", "add", "origin", str(bare)], check=True)
    subprocess.run(
        ["git", "-C", str(seed), "push", "-u", "origin", "main"],
        check=True,
        capture_output=True,
    )
    return bare


async def test_add_sample_unknown_name(harbin_paths, store) -> None:
    with pytest.raises(UserError):
        await samples.add_sample("not-a-sample", paths=harbin_paths, store=store)


async def test_add_sample_clones_and_registers(harbin_paths, store, tmp_path, monkeypatch) -> None:
    bare = _make_named_bare(tmp_path, "harbin-agent-sample-news")
    # Patch SAMPLE_FLEETS to use the local bare repo URL instead of the
    # production GitHub URL.
    monkeypatch.setitem(samples.SAMPLE_FLEETS, "news", str(bare))
    msg = await samples.add_sample("news", paths=harbin_paths, store=store)
    assert "registered" in msg
    row = await store.get_fleet_by_name("harbin-agent-sample-news")
    assert row is not None
    # Dock landed under dock_root.
    assert (harbin_paths.dock_root / "harbin-agent-sample-news").exists()


async def test_add_sample_idempotent(harbin_paths, store, tmp_path, monkeypatch) -> None:
    bare = _make_named_bare(tmp_path, "harbin-agent-sample-news")
    monkeypatch.setitem(samples.SAMPLE_FLEETS, "news", str(bare))
    msg1 = await samples.add_sample("news", paths=harbin_paths, store=store)
    assert "registered" in msg1
    msg2 = await samples.add_sample("news", paths=harbin_paths, store=store)
    assert "already registered" in msg2 or "no-op" in msg2


async def test_add_sample_missing_fleet_yaml_fails(
    harbin_paths, store, tmp_path, monkeypatch
) -> None:
    # Make a bare remote that's missing .harbin/fleet.yaml entirely.
    bare = tmp_path / "broken.git"
    subprocess.run(
        ["git", "init", "--bare", "--initial-branch=main", str(bare)],
        check=True,
        capture_output=True,
    )
    seed = tmp_path / "broken-seed"
    subprocess.run(
        ["git", "init", "--initial-branch=main", str(seed)],
        check=True,
        capture_output=True,
    )
    subprocess.run(["git", "-C", str(seed), "config", "user.email", "t@x"], check=True)
    subprocess.run(["git", "-C", str(seed), "config", "user.name", "t"], check=True)
    (seed / "README.md").write_text("hi", encoding="utf-8")
    subprocess.run(["git", "-C", str(seed), "add", "."], check=True)
    subprocess.run(
        ["git", "-C", str(seed), "commit", "-m", "init"],
        check=True,
        capture_output=True,
    )
    subprocess.run(["git", "-C", str(seed), "remote", "add", "origin", str(bare)], check=True)
    subprocess.run(
        ["git", "-C", str(seed), "push", "-u", "origin", "main"],
        check=True,
        capture_output=True,
    )
    monkeypatch.setitem(samples.SAMPLE_FLEETS, "news", str(bare))
    with pytest.raises(DockError):
        await samples.add_sample("news", paths=harbin_paths, store=store)
