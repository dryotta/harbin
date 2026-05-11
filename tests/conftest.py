"""Shared test fixtures."""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path

import pytest


# Ensure HARBIN_HOME is isolated for every test.
@pytest.fixture(autouse=True)
def harbin_home(tmp_path, monkeypatch):
    home = tmp_path / "harbin_home"
    home.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("HARBIN_HOME", str(home))
    # Pin the sample agents to offline mode so CI never tries to shell
    # out to the real `copilot` binary (which may or may not be on PATH
    # on the test machine, and would make tests non-hermetic either way).
    # Each sample agent's `_resolve_mode()` honours this env var.
    monkeypatch.setenv("HARBIN_AGENT_MODE", "offline")
    yield home


@pytest.fixture
def fake_agent_cli() -> Path:
    """Absolute path to ``tests/fixtures/fake_agent_cli.py``."""
    return (Path(__file__).parent / "fixtures" / "fake_agent_cli.py").resolve()


@pytest.fixture
async def store(harbin_home):
    from harbin.db.store import Store
    from harbin.paths import ensure_all, resolve

    paths = resolve()
    ensure_all(paths)
    s = await Store.open(paths.db_path)
    yield s
    await s.close()


@pytest.fixture
def harbin_paths(harbin_home):
    from harbin.paths import ensure_all, resolve

    p = resolve()
    ensure_all(p)
    return p


@dataclass
class LocalFleetSpec:
    name: str
    url: str  # bare repo path on local FS
    dock_path: Path


@pytest.fixture
def local_fleet(tmp_path) -> LocalFleetSpec:
    """Create a bare git repo + working clone with a minimal .harbin/."""
    name = "test-fleet"
    bare = tmp_path / "bare.git"
    src = tmp_path / "src"
    subprocess.run(
        ["git", "init", "--bare", "--initial-branch=main", str(bare)],
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "init", "--initial-branch=main", str(src)],
        check=True,
        capture_output=True,
    )
    subprocess.run(["git", "-C", str(src), "config", "user.email", "test@harbin"], check=True)
    subprocess.run(["git", "-C", str(src), "config", "user.name", "test"], check=True)
    harbin_dir = src / ".harbin"
    harbin_dir.mkdir()
    (harbin_dir / "fleet.yaml").write_text(
        f"name: {name}\ndefault_branch: main\nartifact_policy:\n  retain: 30d\n  push_back: false\n",
        encoding="utf-8",
    )
    subprocess.run(["git", "-C", str(src), "add", "."], check=True)
    subprocess.run(["git", "-C", str(src), "commit", "-m", "init"], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(src), "remote", "add", "origin", str(bare)], check=True)
    subprocess.run(
        ["git", "-C", str(src), "push", "-u", "origin", "main"], check=True, capture_output=True
    )
    # Now clone into a fresh dock dir
    dock = tmp_path / "dock"
    subprocess.run(["git", "clone", str(bare), str(dock)], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(dock), "config", "user.email", "harbin@localhost"], check=True)
    subprocess.run(["git", "-C", str(dock), "config", "user.name", "harbin"], check=True)
    # Sanity check
    assert (dock / ".harbin" / "fleet.yaml").exists(), (
        f"dock missing .harbin/fleet.yaml: {list(dock.iterdir())}"
    )
    return LocalFleetSpec(name=name, url=str(bare), dock_path=dock)
