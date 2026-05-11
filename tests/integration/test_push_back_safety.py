"""Push-back safety: don't sweep user-local edits into a harbin commit.

If the dock is dirty BEFORE the agent runs (e.g. the operator was mid-
edit on a tracked file), ``git add -A`` after the job would otherwise
include those edits in the harbin push-back commit. The runner captures
``git status --porcelain`` pre-spawn and refuses to push-back when the
tree was already dirty, surfacing a warning instead.

This test runs the news sample with ``push_back: true`` AND a deliberate
pre-existing dirty edit, then asserts:

* the agent still runs successfully (push-back is a post-success
  side-effect, not gating),
* the in-dock archive file is still created (the agent doesn't know or
  care about push-back),
* no harbin commit lands on the local HEAD or the bare remote,
* a warning line referencing the user-local-changes skip appears in the
  job log.
"""

from __future__ import annotations

import asyncio
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from harbin.config.models import AgentCli, Concurrency
from harbin.fleet.artifacts import ArtifactManager
from harbin.fleet.dock import DockManager
from harbin.runner.runner import AgentRunner

_REPO_ROOT = Path(__file__).resolve().parents[2]
_NEWS = _REPO_ROOT / "examples" / "harbin-agent-sample-news"


def _have_submodule() -> bool:
    return (_NEWS / ".harbin" / "fleet.yaml").exists()


pytestmark = [
    pytest.mark.asyncio,
    pytest.mark.skipif(
        not _have_submodule(),
        reason="news sample submodule not initialised",
    ),
]


def _git(*args: str, cwd: Path) -> str:
    return subprocess.run(
        ["git", "-C", str(cwd), *args],
        check=True,
        capture_output=True,
        text=True,
    ).stdout


def _stage_clone_with_remote(source: Path, dest: Path, bare: Path) -> Path:
    shutil.copytree(source, dest, dirs_exist_ok=False)
    submod_git = dest / ".git"
    if submod_git.exists():
        if submod_git.is_dir():
            shutil.rmtree(submod_git)
        else:
            submod_git.unlink()
    subprocess.run(
        ["git", "init", "--bare", "--initial-branch=main", str(bare)],
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "init", "--initial-branch=main", str(dest)],
        check=True,
        capture_output=True,
    )
    subprocess.run(["git", "-C", str(dest), "config", "user.email", "t@x"], check=True)
    subprocess.run(["git", "-C", str(dest), "config", "user.name", "t"], check=True)
    subprocess.run(["git", "-C", str(dest), "add", "-A"], check=True)
    subprocess.run(
        ["git", "-C", str(dest), "commit", "-m", "stage"],
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "-C", str(dest), "remote", "add", "origin", str(bare)],
        check=True,
    )
    subprocess.run(
        ["git", "-C", str(dest), "push", "-u", "origin", "main"],
        check=True,
        capture_output=True,
    )
    return dest


async def _wait_for_status(store, short_id: str, target: set[str], timeout: float = 30.0):
    end = asyncio.get_event_loop().time() + timeout
    while asyncio.get_event_loop().time() < end:
        job = await store.get_job_by_short_id(short_id)
        if job is not None and job.status in target:
            return job
        await asyncio.sleep(0.1)
    raise AssertionError(f"{short_id} never reached {target}")


async def _wait_for_runner_idle(runner, job_id: int, timeout: float = 15.0):
    end = asyncio.get_event_loop().time() + timeout
    while asyncio.get_event_loop().time() < end:
        if job_id not in runner.live_jobs():
            return
        await asyncio.sleep(0.05)
    raise AssertionError(f"runner still has job {job_id} live after {timeout}s")


async def test_push_back_refuses_when_dock_was_dirty(store, harbin_paths, tmp_path) -> None:
    """Pre-existing user edits must not be swept into a harbin commit."""
    dock = harbin_paths.dock_root / "harbin-agent-sample-news"
    bare = tmp_path / "news-remote.git"
    _stage_clone_with_remote(_NEWS, dock, bare)

    # Operator-style "I was editing a tracked file" — modify README.md
    # (a tracked file in the news sample) without committing.
    readme = dock / "README.md"
    assert readme.exists(), "news sample should have a README.md"
    pre_size = readme.stat().st_size
    readme.write_text(
        readme.read_text(encoding="utf-8") + "\n\nWIP edit by the operator.\n",
        encoding="utf-8",
    )
    assert readme.stat().st_size != pre_size  # sanity

    head_before = _git("rev-parse", "main", cwd=dock).strip()
    remote_before = _git("ls-remote", "origin", "main", cwd=dock).split("\t")[0]

    dm = DockManager(store=store, dock_root=harbin_paths.dock_root)
    state = await dm.register_from_existing_dock(dock)
    cli = AgentCli(command=[sys.executable, "agent/run.py"], mode="stdin")
    arts = ArtifactManager(root=harbin_paths.artifact_root, store=store, default_retention="365d")
    runner = AgentRunner(
        store=store,
        artifacts=arts,
        dock_manager=dm,
        agent_cli=cli,
        concurrency=Concurrency(per_dock=1, global_cap=1),
        kill_grace_seconds=2,
        prompts_dir=harbin_paths.prompts_dir,
    )
    try:
        row = await runner.enqueue(
            fleet=state.row,
            prompt="generate today's brief",
            source="repl",
            task_label="morning-brief",
        )
        job = await _wait_for_status(store, row.short_id, {"success", "failed"})
        assert job.status == "success"
        await _wait_for_runner_idle(runner, job.id)

        # 1. The agent's archive copy still lands in the dock.
        assert list((dock / "briefs").glob("brief-*.md"))

        # 2. No harbin commit happened — local HEAD unchanged.
        head_after = _git("rev-parse", "main", cwd=dock).strip()
        assert head_after == head_before, (
            "push-back must NOT advance HEAD when the dock had user-local changes"
        )

        # 3. Bare remote unchanged.
        remote_after = _git("ls-remote", "origin", "main", cwd=dock).split("\t")[0]
        assert remote_after == remote_before

        # 4. The job log records the safety skip.
        log_lines = await store.tail_log_chunks(job.id, n=1000)
        joined = "\n".join(c.text for c in log_lines)
        assert "push-back skipped" in joined and "user-local changes" in joined, joined

        # 5. The operator's edit is still there — harbin must NOT touch
        # working-tree files in any way.
        assert "WIP edit by the operator." in readme.read_text(encoding="utf-8")
    finally:
        await runner.stop()
        await dm.stop()
