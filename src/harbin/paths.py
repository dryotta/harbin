"""On-disk path resolution for harbin.

Subsystems request paths from this module rather than hard-coding
locations. Honors the ``HARBIN_HOME`` env var (overview §3.3 / project-layout §4.3).
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from pathlib import Path

from platformdirs import PlatformDirs

_PLATFORM_DIRS = PlatformDirs("harbin", "harbin", roaming=False)


@dataclass(frozen=True)
class HarbinPaths:
    """Resolved set of harbin directories."""

    config_dir: Path
    data_dir: Path
    cache_dir: Path
    log_dir: Path

    @property
    def dock_root(self) -> Path:
        return self.data_dir / "docks"

    @property
    def artifact_root(self) -> Path:
        return self.data_dir / "artifacts"

    @property
    def db_path(self) -> Path:
        return self.data_dir / "harbin.db"

    @property
    def prompts_dir(self) -> Path:
        return self.cache_dir / "prompts"


def _from_home(home: Path) -> HarbinPaths:
    return HarbinPaths(
        config_dir=home / "config",
        data_dir=home / "data",
        cache_dir=home / "cache",
        log_dir=home / "logs",
    )


def _from_platformdirs() -> HarbinPaths:
    return HarbinPaths(
        config_dir=Path(_PLATFORM_DIRS.user_config_dir),
        data_dir=Path(_PLATFORM_DIRS.user_data_dir),
        cache_dir=Path(_PLATFORM_DIRS.user_cache_dir),
        log_dir=Path(_PLATFORM_DIRS.user_log_dir)
        if hasattr(_PLATFORM_DIRS, "user_log_dir")
        else Path(_PLATFORM_DIRS.user_data_dir) / "logs",
    )


def resolve() -> HarbinPaths:
    """Resolve harbin paths. ``HARBIN_HOME`` overrides every root."""
    override = os.environ.get("HARBIN_HOME")
    if override:
        return _from_home(Path(override).expanduser().resolve())
    return _from_platformdirs()


def ensure_all(paths: HarbinPaths) -> None:
    """Create every needed directory if missing. Idempotent."""
    for p in (paths.config_dir, paths.data_dir, paths.cache_dir, paths.log_dir):
        p.mkdir(parents=True, exist_ok=True)


def ensure_config_dir(paths: HarbinPaths) -> Path:
    paths.config_dir.mkdir(parents=True, exist_ok=True)
    return paths.config_dir


def ensure_data_dir(paths: HarbinPaths) -> Path:
    paths.data_dir.mkdir(parents=True, exist_ok=True)
    return paths.data_dir


def atomic_write_text(path: Path, content: str, *, encoding: str = "utf-8") -> None:
    """Write ``content`` to ``path`` atomically. Crash-safe on POSIX and Windows."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    try:
        tmp.write_text(content, encoding=encoding)
        if sys.platform == "win32" and path.exists():
            # os.replace is atomic on Windows ≥ Vista but errors if destination
            # is locked; fall through to retry once after a tiny pause.
            try:
                os.replace(tmp, path)
            except PermissionError:
                import time

                time.sleep(0.05)
                os.replace(tmp, path)
        else:
            os.replace(tmp, path)
    finally:
        if tmp.exists():
            try:
                tmp.unlink()
            except OSError:
                pass


def atomic_write_bytes(path: Path, content: bytes) -> None:
    """Binary counterpart to :func:`atomic_write_text`."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    try:
        tmp.write_bytes(content)
        os.replace(tmp, path)
    finally:
        if tmp.exists():
            try:
                tmp.unlink()
            except OSError:
                pass
