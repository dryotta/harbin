"""HarbinApp — Textual ``App`` subclass (sub-spec 12)."""

from __future__ import annotations

from typing import ClassVar

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal
from textual.widgets import Static

from harbin.context import AppContext
from harbin.repl.parser import build_registry, dispatch
from harbin.repl.suggester import HarbinSuggester
from harbin.tui.screens.overview import OverviewView
from harbin.tui.theme import LOGO, css_for_theme
from harbin.tui.widgets.command_line import CommandLine
from harbin.tui.widgets.status_bar import StatusBar


class HarbinApp(App):
    """Single Textual App for harbin."""

    _instance: HarbinApp | None = None  # set on init for in-app /config

    CSS = css_for_theme("harbor")

    BINDINGS = [
        Binding("alt+0", "focus_overview", "overview", show=True),
        Binding("alt+1", "focus_job(1)", "job 1", show=False),
        Binding("alt+2", "focus_job(2)", "job 2", show=False),
        Binding("alt+3", "focus_job(3)", "job 3", show=False),
        Binding("alt+4", "focus_job(4)", "job 4", show=False),
        Binding("alt+5", "focus_job(5)", "job 5", show=False),
        Binding("alt+6", "focus_job(6)", "job 6", show=False),
        Binding("alt+7", "focus_job(7)", "job 7", show=False),
        Binding("alt+8", "focus_job(8)", "job 8", show=False),
        Binding("alt+9", "focus_job(9)", "job 9", show=False),
        Binding("ctrl+l", "clear_console", "clear console"),
    ]

    SCREENS: ClassVar[dict[str, type]] = {}  # type: ignore[assignment]

    def __init__(self, ctx: AppContext) -> None:
        super().__init__()
        self.ctx = ctx
        self._cmd_registry = build_registry()
        self._suggester = HarbinSuggester(ctx)
        self._slot_to_short_id: dict[int, str] = {}
        HarbinApp._instance = self

    # ───────────────────────── compose ──────────────────────────

    def compose(self) -> ComposeResult:
        with Horizontal(id="header"):
            yield Static(LOGO, id="header-logo")
            yield Static(
                "harbin · command center for AI agents",
                id="header-tagline",
            )
        yield OverviewView()
        yield CommandLine(suggester=self._suggester)
        yield StatusBar()

    async def on_mount(self) -> None:
        # CommandLine focus
        try:
            cmd = self.query_one(CommandLine)
            cmd.focus()
        except Exception:
            pass
        # initial monitor refresh
        self.set_interval(1.0, self._refresh_periodic)
        await self._refresh_periodic()
        self._write_console("welcome to harbin · type /help to begin")

    # ─────────────────────── REPL handling ──────────────────────

    async def on_input_submitted(self, event) -> None:  # type: ignore[no-untyped-def]
        if event.input.id != "commandline":
            return
        line = (event.value or "").strip()
        event.input.value = ""
        if not line:
            return
        # echo
        self._write_console(f"> {line}")
        await dispatch(self.ctx, line, self._cmd_registry)

    # ─────────────────────── actions / keys ─────────────────────

    def action_focus_overview(self) -> None:
        # When a modal or JobView is on top, pop back to the default
        # screen (which always contains the OverviewView).
        if len(self.screen_stack) > 1:
            self.pop_screen()

    def action_focus_job(self, slot: int) -> None:
        short_id = self._slot_to_short_id.get(slot)
        if not short_id:
            return
        from harbin.tui.screens.job_view import JobViewScreen

        self.push_screen(JobViewScreen(self.ctx, short_id))

    def action_clear_console(self) -> None:
        try:
            from textual.widgets import RichLog

            log = self.query_one("#console", RichLog)
            log.clear()
        except Exception:
            pass

    # ──────────────────────── periodic refresh ─────────────────

    async def _refresh_periodic(self) -> None:
        from harbin.tui.widgets.job_row import JobRowData

        recent = await self.ctx.store.list_recent_jobs(limit=9)
        active_count = len([j for j in recent if j.status in ("queued", "starting", "running")])
        fleets = await self.ctx.store.list_fleets()
        # build rows
        fleet_names = {f.id: f.name for f in fleets}
        data: list[JobRowData] = []
        self._slot_to_short_id.clear()
        for i, j in enumerate(recent):
            slot = i + 1 if i < 9 else 0
            if slot > 0:
                self._slot_to_short_id[slot] = j.short_id
            data.append(
                JobRowData(
                    short_id=j.short_id,
                    fleet=fleet_names.get(j.fleet_id, f"#{j.fleet_id}"),
                    task_label="adhoc" if j.task_pk is None else "task",
                    status=j.status,
                    started_at=j.started_at,
                    ended_at=j.ended_at,
                    slot=slot,
                )
            )
        # Update the overview view (always present on the default screen).
        try:
            view = self._overview()
            if view is not None:
                view.update_monitor(data)
        except Exception:
            pass
        # status bar
        try:
            sb = self.query_one(StatusBar)
            sb.update_counts(
                n_jobs=active_count,
                n_fleets=len(fleets),
                active=self._active_label(),
            )
        except Exception:
            pass

    def _overview(self) -> OverviewView | None:
        """Locate the persistent OverviewView on the default screen.

        We query the base screen rather than ``screen_stack[-1]`` so a
        pushed modal/JobView doesn't hide the overview from us.
        """
        try:
            base = self.screen_stack[0] if self.screen_stack else self.screen
            matches = base.query(OverviewView)
            for w in matches:
                return w
        except Exception:
            return None
        return None

    def _active_label(self) -> str:
        if len(self.screen_stack) > 1:
            from harbin.tui.screens.job_view import JobViewScreen

            top = self.screen_stack[-1]
            if isinstance(top, JobViewScreen):
                return f"job:{top._short_id}"  # type: ignore[attr-defined]
            return type(top).__name__
        return "overview"

    # ─────────────────────── console writer ─────────────────────

    def _write_console(self, text: str) -> None:
        try:
            view = self._overview()
            if view is not None:
                view.write_console(text)
        except Exception:
            pass
