"""Status bar widget."""

from __future__ import annotations

from textual.widgets import Static


class StatusBar(Static):
    """Renders the bottom-of-screen status line."""

    DEFAULT_CSS = ""

    def __init__(self) -> None:
        super().__init__("")
        self._n_jobs = 0
        self._n_fleets = 0
        self._active = "overview"
        self._refresh()

    def update_counts(self, *, n_jobs: int, n_fleets: int, active: str) -> None:
        self._n_jobs = n_jobs
        self._n_fleets = n_fleets
        self._active = active
        self._refresh()

    def _refresh(self) -> None:
        self.update(
            f"{self._n_jobs} jobs · {self._n_fleets} fleets · active: "
            f"{self._active} · alt+0 = overview"
        )
