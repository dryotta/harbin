"""Artifact Manager (sub-spec 08)."""

from __future__ import annotations

import datetime as _dt
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
        return self._root / fleet_name / task_label / short_id

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
        try:
            if path.exists():
                shutil.rmtree(path, ignore_errors=False)
        except FileNotFoundError:
            _log.info("artifact dir already gone: %s", path)
        except PermissionError as e:
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
