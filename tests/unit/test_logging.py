"""Tests for harbin.logging — setup + redaction + ring buffer."""

from __future__ import annotations

import logging

import pytest

from harbin.logging import (
    ROOT_NAME,
    _RedactionFilter,
    get_logger,
    ring_snapshot,
    set_level,
    setup,
)


def _make_record(msg: str) -> logging.LogRecord:
    return logging.LogRecord(
        name=ROOT_NAME,
        level=logging.INFO,
        pathname=__file__,
        lineno=0,
        msg=msg,
        args=(),
        exc_info=None,
    )


def test_redact_classic_pat() -> None:
    f = _RedactionFilter()
    rec = _make_record("token=ghp_" + "A" * 36 + " trailing")
    assert f.filter(rec)
    assert "ghp_" not in rec.getMessage()
    assert "REDACTED" in rec.getMessage()


def test_redact_fine_grained_pat() -> None:
    f = _RedactionFilter()
    rec = _make_record("auth=github_pat_" + "abcdef0123456789ABCDEF_" + "rest")
    assert f.filter(rec)
    assert "github_pat_" not in rec.getMessage()


@pytest.mark.parametrize("prefix", ["ghs_", "gho_", "ghu_", "ghr_"])
def test_redact_other_prefixes(prefix: str) -> None:
    f = _RedactionFilter()
    rec = _make_record(f"x={prefix}" + "B" * 30 + " end")
    assert f.filter(rec)
    msg = rec.getMessage()
    assert prefix not in msg or "REDACTED" in msg


def test_redact_bearer() -> None:
    f = _RedactionFilter()
    rec = _make_record("Authorization: Bearer eyJabc.def-_123")
    assert f.filter(rec)
    assert "Bearer ***REDACTED***" in rec.getMessage()


def test_no_redaction_when_no_marker() -> None:
    f = _RedactionFilter()
    rec = _make_record("regular log line, nothing sensitive")
    assert f.filter(rec)
    assert rec.getMessage() == "regular log line, nothing sensitive"


def test_setup_is_idempotent(tmp_path) -> None:
    log_dir = tmp_path / "logs"
    logger1 = setup(log_dir=log_dir, level="info")
    n_handlers = len(logger1.handlers)
    logger2 = setup(log_dir=log_dir, level="debug")
    assert logger1 is logger2
    assert len(logger2.handlers) == n_handlers


def test_set_level(tmp_path) -> None:
    setup(log_dir=tmp_path / "logs", level="info")
    set_level("debug")
    assert logging.getLogger(ROOT_NAME).level == logging.DEBUG
    set_level("warning")
    assert logging.getLogger(ROOT_NAME).level == logging.WARNING


def test_ring_buffer_captures_lines(tmp_path) -> None:
    setup(log_dir=tmp_path / "logs", level="debug")
    log = get_logger("test")
    log.info("hello ring")
    snap = ring_snapshot()
    assert any("hello ring" in line for line in snap)


def test_ring_buffer_redacts_tokens(tmp_path) -> None:
    setup(log_dir=tmp_path / "logs", level="debug")
    log = get_logger("test")
    log.info("token ghp_" + "X" * 40 + " was leaked")
    snap = ring_snapshot()
    # The most recent line is the one we just emitted.
    last = snap[-1]
    assert "ghp_" not in last
    assert "REDACTED" in last
