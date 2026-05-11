"""Artifact Manager (sub-spec 08)."""

from __future__ import annotations

import datetime as _dt
import re
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from harbin.config.models import parse_retention
from harbin.fleet.models import FleetConfig
from harbin.logging import get_logger

if TYPE_CHECKING:  # pragma: no cover
    from harbin.db.store import FleetRow, Store

_log = get_logger("artifacts")

# Defensive sanity check: never produce paths with a separator or `..` in any
# of the path components — even if a hand-edited DB sneaks past pydantic.
_SAFE_COMPONENT = re.compile(r"^[A-Za-z0-9._-][A-Za-z0-9._\- ]*$")


def _safe_component(name: str, *, what: str) -> str:
    if not name or "/" in name or "\\" in name or ".." in name or name in {".", ".."}:
        raise ValueError(f"unsafe {what} component: {name!r}")
    if not _SAFE_COMPONENT.match(name):
        raise ValueError(f"unsafe {what} component: {name!r}")
    return name


@dataclass(frozen=True)
class JobLocation:
    """Resolved per-job artifact location."""

    fleet: str
    task_label: str
    short_id: str
    path: Path


class ArtifactManager:
    """Owns the on-disk tree under ``paths.artifact_root``."""

    def __init__(
        self,
        *,
        root: Path,
        store: Store,
        default_retention: str = "30d",
    ) -> None:
        self._root = root
        self._store = store
        self._default_retention = default_retention
        self._root.mkdir(parents=True, exist_ok=True)

    @property
    def root(self) -> Path:
        return self._root

    def location_for(self, *, fleet_name: str, task_label: str, short_id: str) -> Path:
        # Defensive: every component must be safe even when the input
        # bypasses pydantic (e.g. a hand-edited DB row).
        return (
            self._root
            / _safe_component(fleet_name, what="fleet_name")
            / _safe_component(task_label, what="task_label")
            / _safe_component(short_id, what="short_id")
        )

    async def prepare(
        self,
        *,
        fleet_name: str,
        task_label: str,
        short_id: str,
    ) -> Path:
        """Create the per-job dir before the agent subprocess is spawned."""
        path = self.location_for(fleet_name=fleet_name, task_label=task_label, short_id=short_id)
        # exist_ok=False would catch short_id collisions but tests sometimes
        # pre-create; tolerate empty existing dir.
        if path.exists():
            if any(path.iterdir()):
                _log.warning("artifact dir not empty at prepare: %s", path)
        else:
            path.mkdir(parents=True, exist_ok=False)
        return path

    async def finalize(self, job_id: int) -> None:
        """Currently a no-op hook; reserved for v2 features."""
        return None

    async def sweep(
        self,
        *,
        fleets: list[FleetRow],
        fleet_configs: dict[int, FleetConfig | None],
        now: _dt.datetime | None = None,
    ) -> int:
        """Run a retention sweep over ``fleets``. Returns count archived."""
        now = now or _dt.datetime.now(_dt.UTC)
        archived = 0
        for fleet in fleets:
            cfg = fleet_configs.get(fleet.id)
            retention_literal = cfg.artifact_policy.retain if cfg else self._default_retention
            try:
                delta = parse_retention(retention_literal)
            except ValueError:
                _log.warning(
                    "fleet %s: invalid retention '%s'; using default",
                    fleet.name,
                    retention_literal,
                )
                delta = parse_retention(self._default_retention)
            cutoff = now - delta
            candidates = await self._store.list_jobs_for_retention(fleet.id, cutoff)
            for job in candidates:
                self._rmtree_safe(Path(job.artifact_dir))
            if candidates:
                await self._store.archive_jobs([j.id for j in candidates])
                archived += len(candidates)
        return archived

    def _rmtree_safe(self, path: Path) -> None:
        if not path.exists():
            return

        def _onerror(func, p, exc_info) -> None:  # type: ignore[no-untyped-def]
            import os
            import stat

            try:
                os.chmod(p, stat.S_IWRITE)
            except OSError:
                pass
            try:
                func(p)
            except OSError as e:  # pragma: no cover - rare on POSIX
                _log.warning("rmtree onerror could not remove %s: %s", p, e)

        try:
            shutil.rmtree(path, onexc=_onerror)
        except TypeError:  # pragma: no cover - older shutil
            shutil.rmtree(path, onerror=_onerror)
        except FileNotFoundError:
            _log.info("artifact dir already gone: %s", path)
        except PermissionError as e:  # pragma: no cover
            _log.warning("could not remove %s: %s", path, e)
        except OSError as e:
            _log.warning("rmtree failed on %s: %s", path, e)

    @staticmethod
    def is_inside(artifact_dir: Path, dock: Path) -> bool:
        """True iff ``artifact_dir`` is under ``dock`` (resolved)."""
        try:
            return artifact_dir.resolve().is_relative_to(dock.resolve())
        except OSError:
            return False

    def remove_fleet_tree(self, fleet_name: str) -> None:
        """Remove the entire artifact tree for a deleted fleet."""
        try:
            safe = _safe_component(fleet_name, what="fleet_name")
        except ValueError:
            _log.warning("refusing to remove unsafe fleet tree: %r", fleet_name)
            return
        self._rmtree_safe(self._root / safe)
