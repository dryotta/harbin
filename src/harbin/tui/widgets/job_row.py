"""JobRow widget — one row in the JobMonitor."""

from __future__ import annotations

import datetime as _dt
from dataclasses import dataclass

from rich.text import Text
from textual.widgets import Static

from harbin.tui.theme import STATUS_GLYPHS


@dataclass(frozen=True)
class JobRowData:
    short_id: str
    fleet: str
    task_label: str
    status: str
    started_at: str | None
    ended_at: str | None
    slot: int  # the alt+N number (1..9), or 0 if unranked


def _elapsed(started: str | None, ended: str | None) -> str:
    if not started:
        return "—"
    try:
        s = _dt.datetime.fromisoformat(started.replace("Z", "+00:00"))
    except ValueError:
        return "—"
    if ended:
        try:
            e = _dt.datetime.fromisoformat(ended.replace("Z", "+00:00"))
        except ValueError:
            e = _dt.datetime.now(_dt.UTC)
    else:
        e = _dt.datetime.now(_dt.UTC)
    secs = max(0, int((e - s).total_seconds()))
    return f"{secs // 60:02d}:{secs % 60:02d}"


class JobRow(Static):
    """A single line: ``[alt+N] ● status   <fleet>·<task>   #<id>   <elapsed>``."""

    DEFAULT_CSS = ""

    def __init__(self, data: JobRowData):
        super().__init__()
        self._data = data
        self.add_class(data.status)

    def render(self) -> Text:
        d = self._data
        slot = f"[alt+{d.slot}]" if d.slot > 0 else "      "
        glyph = STATUS_GLYPHS.get(d.status, "·")
        line = (
            f"{slot} {glyph} {d.status:<8} "
            f"{d.fleet}·{d.task_label}  #{d.short_id}  {_elapsed(d.started_at, d.ended_at)}"
        )
        return Text(line)

    def update_data(self, data: JobRowData) -> None:
        self.remove_class(self._data.status)
        self.add_class(data.status)
        self._data = data
        self.refresh()
