"""Numbered-script migration runner (sub-spec 02 §3)."""

from __future__ import annotations

import importlib.resources
import re

import aiosqlite

from harbin.logging import get_logger

_MIG_NAME = re.compile(r"^(\d{3,})_.+\.sql$")
_log = get_logger("db.migrate")


async def _current_version(conn: aiosqlite.Connection) -> int:
    try:
        async with conn.execute("SELECT value FROM app_state WHERE key = 'schema_version'") as cur:
            row = await cur.fetchone()
            if row is None:
                return 0
            return int(row[0])
    except aiosqlite.OperationalError:
        # app_state doesn't exist yet → version 0
        return 0


def _discover() -> list[tuple[int, str, str]]:
    pkg = importlib.resources.files(__package__) / "migrations"
    entries: list[tuple[int, str, str]] = []
    for entry in pkg.iterdir():
        m = _MIG_NAME.match(entry.name)
        if not m:
            continue
        version = int(m.group(1))
        entries.append((version, entry.name, entry.read_text(encoding="utf-8")))
    entries.sort(key=lambda x: x[0])
    return entries


async def run(conn: aiosqlite.Connection) -> int:
    """Apply pending migrations; return the resulting schema_version."""
    current = await _current_version(conn)
    max_known = max((v for v, _, _ in _discover()), default=0)
    if current > max_known:
        raise RuntimeError(
            f"DB schema_version {current} is newer than this harbin "
            f"knows ({max_known}). Upgrade harbin."
        )
    for version, name, sql in _discover():
        if version <= current:
            continue
        _log.info("applying migration %s", name)
        try:
            await conn.executescript(sql)
            await conn.execute(
                "INSERT OR REPLACE INTO app_state(key, value) VALUES ('schema_version', ?)",
                (str(version),),
            )
            await conn.commit()
            current = version
        except Exception:
            await conn.rollback()
            _log.exception("migration %s failed", name)
            raise
    return current
