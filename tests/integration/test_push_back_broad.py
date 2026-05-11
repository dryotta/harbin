"""Unit tests for the broadened ``push_back`` semantics.

After the v1 fix (notes §19), ``DockManager.push_back`` commits ANY
agent-written file inside the dock tree — not just files under the
per-job ``artifact_dir``. These tests pin that behaviour against
``local_fleet``-style bare repos so we don't depend on the GitHub
sample submodules.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from harbin.fleet.dock import DockManager

pytestmark = pytest.mark.asyncio


def _git(*args: str, cwd: Path) -> str:
    return subprocess.run(
        ["git", "-C", str(cwd), *args],
        check=True,
        capture_output=True,
        text=True,
    ).stdout


async def test_push_back_commits_dock_files_outside_artifact_dir(
    harbin_paths, store, local_fleet, tmp_path
) -> None:
    """The agent's archive copy lives in the dock tree but OUTSIDE the
    per-job ``artifact_dir``. Push-back must still pick it up."""
    # Force push_back: true (fixture writes push_back: false).
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

    dock_under_root = harbin_paths.dock_root / "test-fleet"
    shutil.copytree(local_fleet.dock_path, dock_under_root)

    dm = DockManager(store=store, dock_root=harbin_paths.dock_root)
    try:
        state = await dm.register_from_existing_dock(dock_under_root)

        # 1. Agent writes a file IN the dock but NOT under artifact_dir.
        archive_dir = Path(state.row.dock_path) / "briefs"
        archive_dir.mkdir()
        (archive_dir / "brief-2026-01-01.md").write_text("# brief\n", encoding="utf-8")

        # 2. artifact_dir is the harbin per-job dir, outside the dock.
        outside_artifact = tmp_path / "artifacts" / "test-fleet" / "adhoc" / "ab"
        outside_artifact.mkdir(parents=True)
        (outside_artifact / "canonical.md").write_text("# canonical\n", encoding="utf-8")

        # 3. Push back should commit the in-dock file (briefs/...)
        # even though artifact_dir is NOT under the dock.
        warning = await dm.push_back(
            state=state,
            artifact_dir=outside_artifact,
            short_id="ab",
            task_label="morning-brief",
            prompt="generate today's brief",
        )
        assert warning is None, warning

        # Pushed to origin/main and the commit contains the brief.
        log = _git(
            "log", "--pretty=format:%s", "-1", "origin/main", cwd=Path(state.row.dock_path)
        ).strip()
        assert log.startswith("harbin: morning-brief @"), log

        names = (
            _git(
                "show",
                "--name-only",
                "--pretty=format:",
                "HEAD",
                cwd=Path(state.row.dock_path),
            )
            .strip()
            .splitlines()
        )
        assert "briefs/brief-2026-01-01.md" in names, names
    finally:
        await dm.stop()


async def test_push_back_noop_when_dock_clean(harbin_paths, store, local_fleet) -> None:
    """If the agent wrote nothing to the dock, push-back is a no-op
    (no commit, no push, no warning)."""
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
            "enable",
        ],
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "-C", str(local_fleet.dock_path), "push", "origin", "main"],
        check=True,
        capture_output=True,
    )

    dock_under_root = harbin_paths.dock_root / "test-fleet"
    shutil.copytree(local_fleet.dock_path, dock_under_root)

    dm = DockManager(store=store, dock_root=harbin_paths.dock_root)
    try:
        state = await dm.register_from_existing_dock(dock_under_root)
        before = _git("rev-parse", "HEAD", cwd=Path(state.row.dock_path)).strip()

        warning = await dm.push_back(
            state=state,
            artifact_dir=Path("/tmp/nope"),
            short_id="zz",
            task_label="adhoc",
            prompt="hi",
        )
        assert warning is None
        after = _git("rev-parse", "HEAD", cwd=Path(state.row.dock_path)).strip()
        assert before == after, "no-op must not advance HEAD"
    finally:
        await dm.stop()
