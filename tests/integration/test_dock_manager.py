"""Integration tests for harbin.fleet.dock — sync_once and push_back.

These exercise the on-disk git path against a local bare repo so we never
hit the network. They verify the design's two big claims:

  * `sync_once` does a clean ff-only merge on a clean dock and skips
    fast-forward when the tree is dirty.
  * `push_back` writes a commit authored as ``harbin <harbin@localhost>``
    without mutating the user's ~/.gitconfig.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from harbin.fleet.dock import DockManager

pytestmark = pytest.mark.asyncio


def _git(*args: str, cwd: Path) -> str:
    out = subprocess.run(
        ["git", "-C", str(cwd), *args],
        check=True,
        capture_output=True,
        text=True,
    )
    return out.stdout


def _make_bare_with_seed(tmp_path: Path) -> tuple[Path, Path]:
    """Create a bare 'remote' repo with one initial commit including
    a `.harbin/fleet.yaml`. Returns (bare_path, scratch_clone_for_pushing)."""
    bare = tmp_path / "remote.git"
    subprocess.run(
        ["git", "init", "--bare", "--initial-branch=main", str(bare)],
        check=True,
        capture_output=True,
    )
    seed = tmp_path / "seed"
    subprocess.run(
        ["git", "init", "--initial-branch=main", str(seed)],
        check=True,
        capture_output=True,
    )
    subprocess.run(["git", "-C", str(seed), "config", "user.email", "t@x"], check=True)
    subprocess.run(["git", "-C", str(seed), "config", "user.name", "t"], check=True)
    (seed / ".harbin").mkdir()
    (seed / ".harbin" / "fleet.yaml").write_text(
        "name: sync-target\ndefault_branch: main\nartifact_policy:\n"
        "  retain: 30d\n  push_back: true\n",
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
    return bare, seed


async def test_sync_once_clean_ff(harbin_paths, store, tmp_path) -> None:
    bare, seed = _make_bare_with_seed(tmp_path)
    dm = DockManager(store=store, dock_root=harbin_paths.dock_root)
    state = await dm.register_fleet(str(bare))
    try:
        # Add a new commit on the remote via the seed clone.
        (seed / "note.md").write_text("hello\n", encoding="utf-8")
        subprocess.run(["git", "-C", str(seed), "add", "."], check=True)
        subprocess.run(
            ["git", "-C", str(seed), "commit", "-m", "add note"],
            check=True,
            capture_output=True,
        )
        subprocess.run(
            ["git", "-C", str(seed), "push", "origin", "main"],
            check=True,
            capture_output=True,
        )
        # Sync should fast-forward.
        msg = await dm.sync_once(state)
        assert "synced" in msg
        assert (Path(state.row.dock_path) / "note.md").exists()
        assert state.dirty is False
        assert state.last_sync_ok is True
    finally:
        await dm.stop()


async def test_sync_once_dirty_skips_ff(harbin_paths, store, tmp_path) -> None:
    bare, _seed = _make_bare_with_seed(tmp_path)
    dm = DockManager(store=store, dock_root=harbin_paths.dock_root)
    state = await dm.register_fleet(str(bare))
    try:
        # Dirty the working tree
        (Path(state.row.dock_path) / "dirty.txt").write_text("x", encoding="utf-8")
        msg = await dm.sync_once(state)
        assert "dirty" in msg
        assert state.dirty is True
    finally:
        await dm.stop()


async def test_push_back_uses_harbin_identity(harbin_paths, store, tmp_path, local_fleet) -> None:
    """Run push_back end-to-end against the bare remote and confirm the
    commit was authored by harbin <harbin@localhost>, NOT the user's
    ~/.gitconfig.

    Uses the ``local_fleet`` fixture (bare repo + dock with push_back
    explicitly set true).
    """
    # Force push_back: true (the fixture writes push_back: false).
    fleet_yaml = local_fleet.dock_path / ".harbin" / "fleet.yaml"
    fleet_yaml.write_text(
        "name: test-fleet\ndefault_branch: main\nartifact_policy:\n"
        "  retain: 30d\n  push_back: true\n",
        encoding="utf-8",
    )
    subprocess.run(["git", "-C", str(local_fleet.dock_path), "add", "."], check=True)
    subprocess.run(
        [
            "git",
            "-C",
            str(local_fleet.dock_path),
            "-c",
            "user.email=t@x",
            "-c",
            "user.name=t",
            "commit",
            "-m",
            "enable push_back",
        ],
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "-C", str(local_fleet.dock_path), "push", "origin", "main"],
        check=True,
        capture_output=True,
    )

    dm = DockManager(store=store, dock_root=harbin_paths.dock_root)
    try:
        # Move dock_path under dock_root so register_from_existing_dock works.
        dock_under_root = harbin_paths.dock_root / "test-fleet"
        shutil.copytree(local_fleet.dock_path, dock_under_root)
        state = await dm.register_from_existing_dock(dock_under_root)

        # Create an artifact inside the dock (push_back only fires for
        # artifacts that live in the dock tree).
        art_dir = Path(state.row.dock_path) / "artifacts" / "j-abc"
        art_dir.mkdir(parents=True)
        (art_dir / "result.txt").write_text("hello world\n", encoding="utf-8")

        warning = await dm.push_back(
            state=state,
            artifact_dir=art_dir,
            short_id="abc123",
            task_label="adhoc",
            prompt="say hi",
        )
        # None means success.
        assert warning is None, warning
        # The latest commit on origin should be authored by harbin@localhost.
        author = _git(
            "log", "-1", "--pretty=format:%an <%ae>", cwd=Path(state.row.dock_path)
        ).strip()
        assert author == "harbin <harbin@localhost>", author
    finally:
        await dm.stop()


async def test_push_back_no_op_when_disabled(harbin_paths, store, tmp_path, local_fleet) -> None:
    """If push_back is false in fleet.yaml, push_back must do nothing."""
    dm = DockManager(store=store, dock_root=harbin_paths.dock_root)
    try:
        dock_under_root = harbin_paths.dock_root / "test-fleet"
        shutil.copytree(local_fleet.dock_path, dock_under_root)
        state = await dm.register_from_existing_dock(dock_under_root)
        # local_fleet fixture writes push_back: false.
        assert state.fleet_config is not None
        assert state.fleet_config.artifact_policy.push_back is False
        art_dir = Path(state.row.dock_path) / "out"
        art_dir.mkdir()
        (art_dir / "x").write_text("y", encoding="utf-8")
        warning = await dm.push_back(
            state=state,
            artifact_dir=art_dir,
            short_id="zz",
            task_label="adhoc",
            prompt="hi",
        )
        assert warning is None
    finally:
        await dm.stop()


async def test_register_fleet_then_remove_cleans_up(harbin_paths, store, tmp_path) -> None:
    bare, _seed = _make_bare_with_seed(tmp_path)
    dm = DockManager(store=store, dock_root=harbin_paths.dock_root)
    seen: list[tuple[int, str]] = []

    async def _on_remove(fleet_id: int, fleet_name: str) -> None:
        seen.append((fleet_id, fleet_name))

    dm.register_remove_callback(_on_remove)
    state = await dm.register_fleet(str(bare))
    dock_path = Path(state.row.dock_path)
    assert dock_path.exists()
    await dm.remove_fleet(state.row.id)
    # Dock dir gone, fleet absent from DB, callback fired.
    assert not dock_path.exists()
    assert await store.get_fleet(state.row.id) is None
    assert seen and seen[0][1] == state.row.name
    await dm.stop()
