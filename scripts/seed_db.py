"""Dev helper: seed the harbin DB with a few example rows.

Usage: ``uv run python scripts/seed_db.py``

This is dev-only sandbox code; the package never imports it.
"""

from __future__ import annotations

import asyncio
import sys

from harbin.db.store import Store
from harbin.paths import ensure_all, resolve


async def main() -> int:
    paths = resolve()
    ensure_all(paths)
    store = await Store.open(paths.db_path)
    try:
        fleet = await store.get_fleet_by_name("demo")
        if fleet is None:
            fleet = await store.insert_fleet(
                name="demo", url="https://example.invalid/demo", dock_path="/tmp/demo"
            )
            print(f"inserted fleet {fleet.name} id={fleet.id}")
        else:
            print(f"fleet {fleet.name} already exists id={fleet.id}")
        return 0
    finally:
        await store.close()


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
