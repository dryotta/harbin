"""Tests for ``harbin sample-fleet add`` via the public CLI entrypoint.

Replaces the production SAMPLE_FLEETS URL with a local bare repo so the
test suite never reaches GitHub.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from harbin import cli, samples


def _make_named_bare(tmp_path: Path, name: str) -> Path:
    """Bare remote with a single commit containing ``.harbin/fleet.yaml``
    whose ``name`` equals ``name``."""
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


def test_cli_unknown_subcommand_help(capsys) -> None:
    """``harbin sample-fleet`` (no subcommand) prints help.

    The implementation re-parses with ``--help`` which calls
    ``sys.exit(0)``; tolerate either a direct return of ``2`` or a
    ``SystemExit(0)`` from argparse, since both surface help to the
    operator.
    """
    try:
        rc = cli.main(["sample-fleet"])
    except SystemExit as e:
        rc = e.code
    captured = capsys.readouterr()
    assert rc in (0, 2)
    assert "add" in captured.out or "add" in captured.err


def test_cli_sample_fleet_add_unknown(capsys) -> None:
    """``harbin sample-fleet add not-a-sample`` returns user-error exit code 2."""
    rc = cli.main(["sample-fleet", "add", "not-a-sample"])
    captured = capsys.readouterr()
    assert rc == 2
    assert "error" in captured.err.lower() or "error" in captured.out.lower()


def test_cli_sample_fleet_add_succeeds(tmp_path, monkeypatch, capsys, harbin_home) -> None:
    """End-to-end ``harbin sample-fleet add news`` against a local bare repo."""
    bare = _make_named_bare(tmp_path, "harbin-agent-sample-news")
    monkeypatch.setitem(samples.SAMPLE_FLEETS, "news", str(bare))
    # asyncio.run inside cli.main creates a fresh loop, which is fine
    # since we're at module scope (no outer loop here).
    rc = cli.main(["sample-fleet", "add", "news"])
    captured = capsys.readouterr()
    assert rc == 0, captured.out + captured.err
    # The CLI prints a one-line "registered fleet '<name>' at <path>." +
    # a follow-up "start harbin to use it.".
    assert "registered fleet" in captured.out
    assert "start harbin" in captured.out


def test_cli_sample_fleet_add_idempotent(tmp_path, monkeypatch, capsys, harbin_home) -> None:
    """Running it twice is a no-op, not an error."""
    bare = _make_named_bare(tmp_path, "harbin-agent-sample-news")
    monkeypatch.setitem(samples.SAMPLE_FLEETS, "news", str(bare))
    rc1 = cli.main(["sample-fleet", "add", "news"])
    assert rc1 == 0
    capsys.readouterr()
    rc2 = cli.main(["sample-fleet", "add", "news"])
    captured = capsys.readouterr()
    assert rc2 == 0
    assert "already registered" in captured.out or "no-op" in captured.out


def test_cli_version_flag(capsys) -> None:
    """``--version`` prints the harbin version and exits 0."""
    with pytest.raises(SystemExit) as exc:
        cli.main(["--version"])
    assert exc.value.code == 0
    out = capsys.readouterr().out
    assert out.startswith("harbin ")
