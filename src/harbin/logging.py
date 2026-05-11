"""App logging setup for harbin (sub-spec 04 §6)."""

from __future__ import annotations

import collections
import logging
import re
from logging.handlers import RotatingFileHandler
from pathlib import Path

ROOT_NAME = "harbin"

_RING_BUFFER: collections.deque[str] = collections.deque(maxlen=2000)


# Cover every public-facing GitHub token prefix (sub-spec 04 §6):
#   * ``ghp_`` classic PAT
#   * ``ghs_`` server-to-server
#   * ``gho_`` OAuth
#   * ``ghu_`` user-to-server
#   * ``ghr_`` refresh
#   * ``github_pat_*`` fine-grained PAT (different shape)
_GH_TOKEN_PATTERN = re.compile(r"gh[psour]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{22,}")
_BEARER_PATTERN = re.compile(r"Bearer\s+[A-Za-z0-9._\-]+")


class _RedactionFilter(logging.Filter):
    """Scrub secret-shaped substrings from formatted log lines."""

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            msg = record.getMessage()
        except Exception:
            return True
        if "gh" in msg or "github_pat_" in msg or "Bearer" in msg:
            msg = _GH_TOKEN_PATTERN.sub("***REDACTED***", msg)
            msg = _BEARER_PATTERN.sub("Bearer ***REDACTED***", msg)
            record.msg = msg
            record.args = ()
        return True


class _RingBufferHandler(logging.Handler):
    """Keeps the most recent N formatted lines in memory."""

    def emit(self, record: logging.LogRecord) -> None:
        try:
            _RING_BUFFER.append(self.format(record))
        except Exception:  # pragma: no cover - defensive
            self.handleError(record)


def setup(*, log_dir: Path, level: str = "info") -> logging.Logger:
    """Initialize the root harbin logger. Idempotent."""
    log_dir.mkdir(parents=True, exist_ok=True)

    logger = logging.getLogger(ROOT_NAME)
    logger.setLevel(_level_for(level))
    if getattr(logger, "_harbin_configured", False):
        return logger

    fmt = logging.Formatter(
        "%(asctime)s %(levelname)-7s %(name)s %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S%z",
    )

    file_handler = RotatingFileHandler(
        log_dir / "harbin.log",
        maxBytes=5 * 1024 * 1024,
        backupCount=5,
        encoding="utf-8",
    )
    file_handler.setFormatter(fmt)
    file_handler.addFilter(_RedactionFilter())
    logger.addHandler(file_handler)

    ring = _RingBufferHandler()
    ring.setFormatter(fmt)
    ring.addFilter(_RedactionFilter())
    logger.addHandler(ring)

    logger.propagate = False
    logger._harbin_configured = True  # type: ignore[attr-defined]
    return logger


def _level_for(name: str) -> int:
    mapping = {
        "debug": logging.DEBUG,
        "info": logging.INFO,
        "warning": logging.WARNING,
        "error": logging.ERROR,
    }
    return mapping.get(name.lower(), logging.INFO)


def set_level(level: str) -> None:
    logging.getLogger(ROOT_NAME).setLevel(_level_for(level))


def ring_snapshot() -> list[str]:
    """Return a copy of the in-memory ring buffer."""
    return list(_RING_BUFFER)


def get_logger(name: str) -> logging.Logger:
    """Return a named child logger under ``harbin``."""
    if name.startswith(ROOT_NAME):
        return logging.getLogger(name)
    return logging.getLogger(f"{ROOT_NAME}.{name}")
