"""End-to-end test for the push-back path against the bundled samples.

Verifies that:

* The ``news`` sample's agent — invoked through the real ``AgentRunner`` —
  writes its in-repo archive copy under ``briefs/`` and harbin's
  ``DockManager.push_back`` then commits + pushes that file to the bare
  remote, even though the canonical ``$HARBIN_ARTIFACT_DIR/brief.md``
  lives outside the dock.

This is the regression guard for the design contract in
``doc/design/07-fleet-and-dock-manager.md`` §4.1:

    Only files written inside the dock are pushable. … A fleet that wants
    push-back must instruct its agent to write into the dock — typically a
    `briefs/` or `data/` directory committed to the repo.

The price-monitor sample has ``push_back: false`` and so is exercised in
the opposite direction: nothing must be pushed.
"""

from __future__ import annotations

import asyncio
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from harbin.config.loader import load_fleet
from harbin.config.models import AgentCli, Concurrency
from harbin.fleet.artifacts import ArtifactManager
from harbin.fleet.dock import DockManager
from harbin.runner.runner import AgentRunner

_REPO_ROOT = Path(__file__).resolve().parents[2]
_EXAMPLES = _REPO_ROOT / "examples"
_NEWS = _EXAMPLES / "harbin-agent-sample-news"
_PRICES = _EXAMPLES / "harbin-agent-sample-price-monitor"


def _have_submodules() -> bool:
    return (_NEWS / ".harbin" / "fleet.yaml").exists() and (
        _PRICES / ".harbin" / "fleet.yaml"
    ).exists()


pytestmark = [
    pytest.mark.asyncio,
    pytest.mark.skipif(
        not _have_submodules(),
        reason="sample fleet submodules not initialised (run `git submodule update --init --recursive`)",
    ),
]


def _git(*args: str, cwd: Path) -> str:
    """Run a git command and return stdout."""
    out = subprocess.run(
        ["git", "-C", str(cwd), *args],
        check=True,
        capture_output=True,
        text=True,
    )
    return out.stdout


def _stage_clone_with_remote(source: Path, dest: Path, bare: Path) -> Path:
    """Copy a sample submodule to ``dest`` and wire it up as a real git
    working tree with ``origin`` pointing at a bare repo we control.

    The submodule itself isn't a normal working copy (its `.git` is a
    file, not a directory), so we initialise a fresh repo, seed it with
    the submodule's content, and push it to the bare remote so the dock
    has a real upstream.
    """
    shutil.copytree(source, dest, dirs_exist_ok=False)
    # Remove the submodule's `.git` file/dir so we can initialise our own.
    submod_git = dest / ".git"
    if submod_git.exists():
        if submod_git.is_dir():
            shutil.rmtree(submod_git)
        else:
            submod_git.unlink()
    # Bare remote that simulates GitHub.
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
    """Wait until the runner's post-job work (push-back, finalize) is done.

    The runner pops ``_live[job_id]`` only AFTER ``push_back`` returns, so
    polling ``runner.live_jobs()`` is a deterministic completion barrier.
    """
    end = asyncio.get_event_loop().time() + timeout
    while asyncio.get_event_loop().time() < end:
        if job_id not in runner.live_jobs():
            return
        await asyncio.sleep(0.05)
    raise AssertionError(f"runner still has job {job_id} live after {timeout}s")


async def test_news_sample_push_back_commits_dock_archive(store, harbin_paths, tmp_path) -> None:
    """The news agent writes a ``briefs/brief-YYYY-MM-DD.md`` in the dock.
    With ``push_back: true``, harbin must commit + push that file even
    though the per-job artifact_dir lives outside the dock."""

    # 1. Confirm the sample really declares push_back: true (regression
    # guard if a contributor accidentally flips it).
    fleet_cfg = load_fleet(_NEWS / ".harbin" / "fleet.yaml")
    assert fleet_cfg.artifact_policy.push_back is True

    # 2. Stage the submodule into a real dock with its own bare remote.
    dock = harbin_paths.dock_root / "harbin-agent-sample-news"
    bare = tmp_path / "news-remote.git"
    _stage_clone_with_remote(_NEWS, dock, bare)

    dm = DockManager(store=store, dock_root=harbin_paths.dock_root)
    state = await dm.register_from_existing_dock(dock)

    # Use the running interpreter to invoke agent/run.py — robust against
    # CI environments without a generic ``python`` on PATH.
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
            prompt="generate the daily brief",
            source="repl",
            task_label="morning-brief",
        )
        job = await _wait_for_status(store, row.short_id, {"success", "failed"})
        assert job.status == "success", job
        # Push-back happens after status flips; wait for the live-jobs
        # tracker to drop this id so we know post-success work finished.
        await _wait_for_runner_idle(runner, job.id)

        # 3. Confirm both files landed where the design says they should.
        #
        # Canonical artifact lives outside the dock, under harbin's
        # per-job artifact root.
        brief = arts.root / state.row.name / "morning-brief" / row.short_id / "brief.md"
        assert brief.exists(), brief

        # In-repo archive lives inside the dock.
        in_dock = list((Path(state.row.dock_path) / "briefs").glob("brief-*.md"))
        assert in_dock, "agent did not write briefs/brief-*.md in the dock"

        # 4. The runner's post-job hook should have pushed that archive
        # to the bare remote. The commit message follows the design
        # convention `harbin: <task> @<utc>` (07 §4 step 3).
        log = _git("log", "--pretty=format:%s%n%b", "-1", cwd=Path(state.row.dock_path))
        assert log.startswith("harbin: morning-brief @"), log

        # The bare remote should also have it.
        # `git log --pretty=format:%s -1 origin/main`
        remote_log = _git(
            "log", "--pretty=format:%s", "-1", "origin/main", cwd=Path(state.row.dock_path)
        )
        assert remote_log.startswith("harbin: morning-brief @"), remote_log

        # The author must be harbin@localhost (not the user's gitconfig).
        author = _git(
            "log", "-1", "--pretty=format:%an <%ae>", cwd=Path(state.row.dock_path)
        ).strip()
        assert author == "harbin <harbin@localhost>", author

        # The pushed commit should include the dated archive copy.
        names = _git(
            "show", "--name-only", "--pretty=format:", "HEAD", cwd=Path(state.row.dock_path)
        ).strip()
        assert any(p.startswith("briefs/brief-") for p in names.splitlines()), names
    finally:
        await runner.stop()
        await dm.stop()


async def test_price_monitor_sample_does_not_push_back(store, harbin_paths, tmp_path) -> None:
    """The price-monitor sample sets ``push_back: false``. Even though the
    agent writes ``history/prices-YYYY-MM-DD.jsonl`` into the dock, harbin
    must NOT commit or push anything."""

    fleet_cfg = load_fleet(_PRICES / ".harbin" / "fleet.yaml")
    assert fleet_cfg.artifact_policy.push_back is False

    dock = harbin_paths.dock_root / "harbin-agent-sample-price-monitor"
    bare = tmp_path / "prices-remote.git"
    _stage_clone_with_remote(_PRICES, dock, bare)

    # Capture the bare remote's HEAD before the run.
    head_before = _git("rev-parse", "main", cwd=Path(dock)).strip()

    dm = DockManager(store=store, dock_root=harbin_paths.dock_root)
    state = await dm.register_from_existing_dock(dock)
    cli = AgentCli(command=[sys.executable, "agent/run.py"], mode="stdin")
    arts = ArtifactManager(root=harbin_paths.artifact_root, store=store, default_retention="30d")
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
            prompt="snapshot",
            source="repl",
            task_label="hourly-prices",
        )
        job = await _wait_for_status(store, row.short_id, {"success", "failed"})
        assert job.status == "success"
        await _wait_for_runner_idle(runner, job.id)

        # 1. The agent did write its in-dock history file.
        assert list((Path(state.row.dock_path) / "history").glob("prices-*.jsonl"))

        # 2. But harbin must NOT have committed anything (push_back: false).
        head_after = _git("rev-parse", "main", cwd=Path(state.row.dock_path)).strip()
        assert head_after == head_before, "push_back: false must not advance HEAD"

        # And the bare remote ref must match.
        bare_head = _git("rev-parse", "main", cwd=Path(state.row.dock_path)).strip()
        # subtle: we already checked HEAD; cross-check by inspecting origin
        ls = _git("ls-remote", "origin", "main", cwd=Path(state.row.dock_path)).strip()
        assert ls.startswith(bare_head), ls
    finally:
        await runner.stop()
        await dm.stop()
