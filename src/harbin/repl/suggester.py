"""Textual Suggester for tab completion (sub-spec 13 §2)."""

from __future__ import annotations

from typing import TYPE_CHECKING

from textual.suggester import Suggester

if TYPE_CHECKING:  # pragma: no cover
    from harbin.context import AppContext

KNOWN_COMMANDS = [
    "help",
    "jobs",
    "logs",
    "cancel",
    "artifacts",
    "sync",
    "schedule",
    "web",
    "tunnel",
    "config",
    "exit",
]


class HarbinSuggester(Suggester):
    """Single-prefix suggester driven by AppContext."""

    def __init__(self, ctx: AppContext):
        super().__init__(case_sensitive=True)
        self._ctx = ctx

    async def get_suggestion(self, value: str) -> str | None:
        if not value:
            return None
        if value.startswith("@"):
            prefix = value[1:]
            names = sorted(f.name for f in await self._ctx.store.list_fleets())
            for n in names:
                if n.startswith(prefix):
                    return "@" + n
            return None
        if value.startswith("/"):
            body = value[1:]
            parts = body.split(" ", 1)
            cmd = parts[0]
            if len(parts) == 1:
                matches = [c for c in KNOWN_COMMANDS if c.startswith(cmd)]
                if matches:
                    return "/" + sorted(matches)[0]
                return None
            if cmd in {"logs", "cancel"}:
                rest = parts[1]
                if not rest:
                    return None
                active = await self._ctx.store.list_active_jobs()
                ids = sorted(j.short_id for j in active)
                for i in ids:
                    if i.startswith(rest):
                        return f"/{cmd} {i}"
                return None
            if cmd in {"sync", "schedule", "artifacts"}:
                rest = parts[1]
                names = sorted(f.name for f in await self._ctx.store.list_fleets())
                for n in names:
                    if n.startswith(rest):
                        return f"/{cmd} {n}"
                return None
            if cmd in {"web", "tunnel"}:
                rest = parts[1]
                for sub in ("start", "stop", "status"):
                    if sub.startswith(rest):
                        return f"/{cmd} {sub}"
                return None
        return None
