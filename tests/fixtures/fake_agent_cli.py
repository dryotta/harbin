#!/usr/bin/env python
"""Test-only fake agent CLI (sub-spec 05 §3).

Honors:
  * ``HARBIN_AGENT_MODE`` (``stdin``/``flag``/``tempfile``) — default ``stdin``.
  * ``HARBIN_FAKE_EXIT`` — integer exit code (default 0).
  * ``HARBIN_FAKE_DURATION`` — sleep seconds (default 0).
  * ``HARBIN_ARTIFACT_DIR`` — where to write ``result.txt``.

Writes:
  * Deterministic stdout ``START\nEND\n``.
  * On nonzero exit: error reason to stderr.
  * ``$HARBIN_ARTIFACT_DIR/result.txt`` with first 80 chars of prompt.
"""

from __future__ import annotations

import os
import signal
import sys
import time
from pathlib import Path


def _read_prompt(mode: str) -> str:
    if mode == "stdin":
        return sys.stdin.read()
    if mode == "flag":
        argv = sys.argv[1:]
        try:
            i = argv.index("--prompt")
            return argv[i + 1]
        except (ValueError, IndexError):
            return ""
    if mode == "tempfile":
        argv = sys.argv[1:]
        try:
            i = argv.index("--prompt-file")
            return Path(argv[i + 1]).read_text(encoding="utf-8")
        except (ValueError, IndexError):
            return ""
    return ""


def main() -> int:
    mode = os.environ.get("HARBIN_AGENT_MODE", "stdin")
    duration = float(os.environ.get("HARBIN_FAKE_DURATION", "0") or 0)
    exit_code = int(os.environ.get("HARBIN_FAKE_EXIT", "0") or 0)

    sys.stdout.write("START\n")
    sys.stdout.flush()

    cancelled = False

    def _handle(signum, frame):  # noqa: ARG001
        nonlocal cancelled
        cancelled = True
        sys.stderr.write("cancelled\n")
        sys.stderr.flush()

    if hasattr(signal, "SIGTERM"):
        try:
            signal.signal(signal.SIGTERM, _handle)
        except (OSError, ValueError):
            pass

    end = time.monotonic() + duration
    while time.monotonic() < end and not cancelled:
        time.sleep(0.05)
    if cancelled:
        return 143

    prompt = _read_prompt(mode)
    artifact_dir = os.environ.get("HARBIN_ARTIFACT_DIR")
    if artifact_dir:
        try:
            p = Path(artifact_dir)
            p.mkdir(parents=True, exist_ok=True)
            (p / "result.txt").write_text(
                f"echoed: {prompt[:80]}\n", encoding="utf-8"
            )
        except OSError as e:
            sys.stderr.write(f"artifact write failed: {e}\n")

    if exit_code != 0:
        sys.stderr.write(f"exit code {exit_code}\n")

    sys.stdout.write("END\n")
    sys.stdout.flush()
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
