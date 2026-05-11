"""Tests for harbin.runner.invocation."""

from __future__ import annotations

from pathlib import Path

from harbin.config.models import AgentCli
from harbin.runner.invocation import build


def test_stdin_mode(tmp_path: Path) -> None:
    cli = AgentCli(command=["echo"], mode="stdin")
    inv = build(cli, "hello world", prompts_dir=tmp_path, short_id="abc123")
    assert inv.argv == ["echo"]
    assert inv.stdin_text == "hello world"
    assert inv.cleanup_path is None


def test_flag_mode(tmp_path: Path) -> None:
    cli = AgentCli(
        command=["agent", "--prompt", "${PROMPT}"],
        mode="flag",
        placeholder="${PROMPT}",
    )
    inv = build(cli, "hello", prompts_dir=tmp_path, short_id="abc123")
    assert inv.argv == ["agent", "--prompt", "hello"]
    assert inv.stdin_text is None


def test_tempfile_mode(tmp_path: Path) -> None:
    cli = AgentCli(
        command=["agent", "--prompt-file", "${PROMPT_FILE}"],
        mode="tempfile",
        placeholder="${PROMPT_FILE}",
    )
    inv = build(cli, "abc", prompts_dir=tmp_path, short_id="zz")
    assert inv.argv[0] == "agent"
    assert Path(inv.argv[2]).exists()
    assert Path(inv.argv[2]).read_text(encoding="utf-8") == "abc"
    assert inv.cleanup_path == Path(inv.argv[2])
