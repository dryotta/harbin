"""``harbin sample-fleet add`` helper (sub-spec 06 §6)."""

from __future__ import annotations

import asyncio
import shutil
from pathlib import Path
from typing import TYPE_CHECKING

from harbin.config.loader import load_fleet
from harbin.errors import DockError, UserError
from harbin.logging import get_logger

if TYPE_CHECKING:  # pragma: no cover
    from harbin.db.store import Store
    from harbin.paths import HarbinPaths

_log = get_logger("samples")

SAMPLE_FLEETS: dict[str, str] = {
    "news": "https://github.com/dryotta/harbin-agent-sample-news",
    "price-monitor": "https://github.com/dryotta/harbin-agent-sample-price-monitor",
}


async def add_sample(name: str, *, paths: HarbinPaths, store: Store) -> str:
    """Clone a sample repo into the dock root and register it. Returns a summary."""
    if name not in SAMPLE_FLEETS:
        raise UserError(
            code="user.unknown_sample",
            message=f"unknown sample '{name}'. allowed: {', '.join(SAMPLE_FLEETS)}",
        )
    url = SAMPLE_FLEETS[name]
    paths.dock_root.mkdir(parents=True, exist_ok=True)
    # Use the repo basename as the preliminary path
    prelim_name = Path(url.rstrip("/").rstrip(".git")).name
    prelim_path = paths.dock_root / prelim_name

    if not prelim_path.exists():
        proc = await asyncio.create_subprocess_exec(
            "git",
            "clone",
            "--depth=50",
            url,
            str(prelim_path),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            _, stderr = await asyncio.wait_for(proc.communicate(), timeout=600)
        except TimeoutError:
            proc.kill()
            await proc.wait()
            shutil.rmtree(prelim_path, ignore_errors=True)
            raise DockError(
                code="fleet.dock.clone_timeout",
                message=f"git clone of {url} timed out",
            ) from None
        if proc.returncode != 0:
            shutil.rmtree(prelim_path, ignore_errors=True)
            raise DockError(
                code="fleet.dock.clone_failed",
                message=f"git clone failed: {stderr.decode('utf-8', errors='replace').strip()}",
            )

    fleet_yaml = prelim_path / ".harbin" / "fleet.yaml"
    if not fleet_yaml.exists():
        raise DockError(
            code="fleet.dock.no_fleet_yaml",
            message=f"{url} has no .harbin/fleet.yaml",
        )
    fleet_cfg = load_fleet(fleet_yaml)
    final_path = paths.dock_root / fleet_cfg.name
    if final_path != prelim_path:
        if final_path.exists():
            # already registered? bail to idempotency check
            existing = await store.get_fleet_by_name(fleet_cfg.name)
            if existing is not None:
                return "fleet already registered (no-op)"
            raise DockError(
                code="fleet.dock.name_collision",
                message=f"path {final_path} exists but no DB row",
            )
        prelim_path.rename(final_path)

    existing = await store.get_fleet_by_name(fleet_cfg.name)
    if existing is not None:
        return "fleet already registered (no-op)"
    await store.insert_fleet(name=fleet_cfg.name, url=url, dock_path=str(final_path))
    return f"registered fleet '{fleet_cfg.name}' at {final_path}."
