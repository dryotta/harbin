"""JobView screen — focused live stdio for a single job (sub-spec 12 §3)."""

from __future__ import annotations

from typing import TYPE_CHECKING

from textual.app import ComposeResult
from textual.binding import Binding
from textual.screen import Screen
from textual.widgets import RichLog, Static

if TYPE_CHECKING:  # pragma: no cover
    from harbin.context import AppContext


class JobViewScreen(Screen):
    BINDINGS = [
        Binding("escape", "app.pop_screen", "back"),
        Binding("g", "scroll_home", "top"),
        Binding("G", "scroll_end", "bottom"),
    ]

    DEFAULT_CSS = ""

    def __init__(self, ctx: AppContext, short_id: str) -> None:
        super().__init__()
        self._ctx = ctx
        self._short_id = short_id
        self._header: Static | None = None
        self._log: RichLog | None = None
        self._last_seq = -1

    def compose(self) -> ComposeResult:
        self._header = Static("loading…")
        yield self._header
        self._log = RichLog(highlight=False, markup=False, wrap=True, id="jobview-log")
        yield self._log

    async def on_mount(self) -> None:
        await self.refresh_log()
        self.set_interval(1.0, self.refresh_log)

    async def refresh_log(self) -> None:
        if self._header is None or self._log is None:
            return
        job = await self._ctx.store.get_job_by_short_id(self._short_id)
        if job is None:
            self._header.update(f"#{self._short_id}: not found")
            return
        fleet = await self._ctx.store.get_fleet(job.fleet_id)
        fname = fleet.name if fleet else f"#{job.fleet_id}"
        self._header.update(
            f"{fname}·#{job.short_id}  {job.status}  "
            f"exit={job.exit_code if job.exit_code is not None else '—'}"
        )
        chunks = await self._ctx.store.tail_log_chunks(job.id, n=2000)
        new = [c for c in chunks if c.seq > self._last_seq]
        if not new:
            return
        for c in new:
            tag = "" if c.stream == "stdout" else f"[{c.stream}] "
            self._log.write(f"{tag}{c.text.rstrip()}")
        self._last_seq = max(c.seq for c in new)

    def action_scroll_home(self) -> None:
        if self._log is not None:
            self._log.scroll_home()

    def action_scroll_end(self) -> None:
        if self._log is not None:
            self._log.scroll_end()
