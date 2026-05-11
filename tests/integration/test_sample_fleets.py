"""End-to-end integration test against the bundled sample-fleet
submodules under ``examples/``.

These tests verify that:
  * Each sample's `.harbin/fleet.yaml` and `.harbin/schedule.yaml` are
    valid and load through the real pydantic schemas.
  * The agent CLI override actually produces the documented artifacts
    when invoked via the harbin runner against a local clone.

The submodule path is checked at the top of the file; if the
submodules have not been initialised yet the entire module is
skipped so a fresh `git clone` (without `--recurse-submodules`)
doesn't fail the suite.
"""

from __future__ import annotations

import asyncio
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from harbin.config.loader import load_fleet, load_schedule
from harbin.config.models import AgentCli, Concurrency
from harbin.fleet.artifacts import ArtifactManager
from harbin.fleet.dock import DockManager
from harbin.runner.runner import AgentRunner

pytestmark = pytest.mark.asyncio


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
        reason="sample fleet submodules not initialised (run `git submodule update --init`)",
    ),
]


def _stage_clone(source: Path, dest: Path) -> Path:
    """Copy ``source`` (a submodule checkout) into ``dest`` and turn it
    into a proper git working copy so harbin treats it as a dock.

    The submodule itself isn't a normal working copy (its `.git` is a
    file, not a directory), so we initialise a fresh repo and seed it
    with the submodule's content.
    """
    shutil.copytree(source, dest, dirs_exist_ok=False)
    # Remove the submodule's `.git` file/dir so we can initialise our own.
    submod_git = dest / ".git"
    if submod_git.exists():
        if submod_git.is_dir():
            shutil.rmtree(submod_git)
        else:
            submod_git.unlink()
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
    return dest


async def _wait_for_status(store, short_id: str, target: set[str], timeout: float = 30.0):
    end = asyncio.get_event_loop().time() + timeout
    while asyncio.get_event_loop().time() < end:
        job = await store.get_job_by_short_id(short_id)
        if job is not None and job.status in target:
            return job
        await asyncio.sleep(0.1)
    raise AssertionError(f"{short_id} never reached {target}")


async def test_news_sample_loads_and_runs(store, harbin_paths, tmp_path) -> None:
    """The news sample's fleet.yaml/schedule.yaml parse, and invoking
    its agent via the harbin runner produces brief.md."""
    # 1. Validate the YAML files using harbin's real loaders.
    fleet_cfg = load_fleet(_NEWS / ".harbin" / "fleet.yaml")
    assert fleet_cfg.name == "harbin-agent-sample-news"
    assert fleet_cfg.artifact_policy.push_back is True
    schedule_cfg = load_schedule(_NEWS / ".harbin" / "schedule.yaml")
    assert any(t.id == "morning-brief" for t in schedule_cfg.tasks)
    assert fleet_cfg.agent_cli is not None
    assert fleet_cfg.agent_cli.command[0] == "python"

    # 2. Run the agent through the harbin runner.
    dock = harbin_paths.dock_root / "harbin-agent-sample-news"
    _stage_clone(_NEWS, dock)
    dm = DockManager(store=store, dock_root=harbin_paths.dock_root)
    state = await dm.register_from_existing_dock(dock)
    # Override the agent_cli to use the real Python on this machine
    # (the fleet.yaml says "python" which may not be on PATH).
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
        brief = arts.root / state.row.name / "morning-brief" / row.short_id / "brief.md"
        assert brief.exists()
        text = brief.read_text(encoding="utf-8")
        assert "News brief" in text
        # The agent also writes an in-repo archive copy in the dock.
        archive = list((Path(state.row.dock_path) / "briefs").glob("brief-*.md"))
        assert archive, "expected at least one brief-*.md in dock/briefs/"
    finally:
        await runner.stop()


async def test_price_monitor_sample_loads_and_runs(store, harbin_paths) -> None:
    """The price-monitor sample's YAMLs parse and its agent writes
    prices.json + a per-day jsonl history line."""
    fleet_cfg = load_fleet(_PRICES / ".harbin" / "fleet.yaml")
    assert fleet_cfg.name == "harbin-agent-sample-price-monitor"
    assert fleet_cfg.artifact_policy.push_back is False
    schedule_cfg = load_schedule(_PRICES / ".harbin" / "schedule.yaml")
    assert any(t.id == "hourly-prices" for t in schedule_cfg.tasks)

    dock = harbin_paths.dock_root / "harbin-agent-sample-price-monitor"
    _stage_clone(_PRICES, dock)
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
            prompt="hourly snapshot",
            source="repl",
            task_label="hourly-prices",
        )
        job = await _wait_for_status(store, row.short_id, {"success", "failed"})
        assert job.status == "success", job
        prices = arts.root / state.row.name / "hourly-prices" / row.short_id / "prices.json"
        assert prices.exists()
        import json

        snap = json.loads(prices.read_text(encoding="utf-8"))
        assert "ts" in snap and "items" in snap
        # At least the BTC/ETH/GME entries should round-trip.
        assert "BTC-USD" in snap["items"]
        # Per-day jsonl history exists in the dock tree.
        history = list((Path(state.row.dock_path) / "history").glob("prices-*.jsonl"))
        assert history, "expected at least one prices-*.jsonl in dock/history/"
    finally:
        await runner.stop()
