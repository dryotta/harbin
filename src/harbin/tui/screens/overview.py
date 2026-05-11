"""Overview view — JobMonitor + Console (sub-spec 12 §2).

This is the **default-screen content** of the TUI: the user sees it on
launch and returns to it via ``alt+0`` after closing any modal/sub-
screen. It is a plain ``Container`` (not a ``Screen``) so it composes
directly into ``HarbinApp``'s default screen alongside the header,
command line, and status bar.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from textual.app import ComposeResult
from textual.containers import Container, VerticalScroll
from textual.widgets import RichLog, Static

from harbin.tui.widgets.job_row import JobRow, JobRowData

if TYPE_CHECKING:  # pragma: no cover
    pass


class JobMonitor(Container):
    """Bordered container that lists active+recent jobs."""

    DEFAULT_CSS = ""

    def __init__(self) -> None:
        super().__init__(id="monitor")
        self.border_title = "monitor"
        self._rows: dict[str, JobRow] = {}
        self._empty: Static | None = None

    def compose(self) -> ComposeResult:
        yield VerticalScroll(id="monitor-scroll")

    def on_mount(self) -> None:
        self._show_empty()

    def _show_empty(self) -> None:
        if self._empty is None and not self._rows:
            self._empty = Static(
                "no fleets registered yet.\n"
                "  add one:  /config → Fleets → + Add fleet\n"
                "  or try a sample:  harbin sample-fleet add news",
                classes="muted",
            )
            scroll = self.query_one("#monitor-scroll", VerticalScroll)
            scroll.mount(self._empty)

    def _hide_empty(self) -> None:
        if self._empty is not None:
            self._empty.remove()
            self._empty = None

    def refresh_rows(self, data: list[JobRowData]) -> None:
        if not data:
            for r in self._rows.values():
                r.remove()
            self._rows.clear()
            self._show_empty()
            return
        self._hide_empty()
        scroll = self.query_one("#monitor-scroll", VerticalScroll)
        wanted = {d.short_id for d in data}
        for short_id in list(self._rows):
            if short_id not in wanted:
                self._rows.pop(short_id).remove()
        for d in data:
            existing = self._rows.get(d.short_id)
            if existing is None:
                row = JobRow(d)
                row.add_class(d.status)
                self._rows[d.short_id] = row
                scroll.mount(row)
            else:
                existing.update_data(d)


class OverviewView(Container):
    """The default landing view: monitor + console panes side-stacked."""

    DEFAULT_CSS = ""

    def __init__(self) -> None:
        super().__init__(id="overview")

    def compose(self) -> ComposeResult:
        yield JobMonitor()
        log = RichLog(highlight=False, markup=True, wrap=True, id="console")
        log.border_title = "console"
        yield log

    def write_console(self, text: str) -> None:
        log = self.query_one("#console", RichLog)
        log.write(text)

    def update_monitor(self, data: list[JobRowData]) -> None:
        monitor = self.query_one(JobMonitor)
        monitor.refresh_rows(data)


# Back-compat alias: a few imports still reference ``OverviewScreen``.
# Keeping the name avoids touching every test/import in this PR.
OverviewScreen = OverviewView
