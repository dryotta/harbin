"""Config modal screen (sub-spec 12 §8).

Minimal v1 implementation: a sidebar of page names and a read-edit pane
that surfaces each pydantic field as a labelled input. Save round-trips
through atomic write + pydantic re-validation.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import yaml
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Grid, Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Button, Input, Label, Static

from harbin.config.models import Config

if TYPE_CHECKING:  # pragma: no cover
    from harbin.context import AppContext

_PAGES: list[tuple[str, str]] = [
    ("general", "General"),
    ("fleets", "Fleets"),
    ("scheduler", "Scheduler"),
    ("agent", "Agent runner"),
    ("artifacts", "Artifacts"),
    ("web", "Web UI"),
    ("tunnels", "Dev Tunnels"),
    ("keybindings", "Keybindings"),
    ("about", "About"),
]


class ConfigModalScreen(ModalScreen):
    BINDINGS = [Binding("escape", "app.pop_screen", "cancel")]
    DEFAULT_CSS = ""

    def __init__(self, ctx: AppContext) -> None:
        super().__init__()
        self._ctx = ctx
        self._page = "general"
        self._content_container: VerticalScroll | None = None

    def compose(self) -> ComposeResult:
        with Grid(id="config-grid"):
            sidebar = Vertical(id="config-sidebar")
            sidebar.styles.width = 22
            yield sidebar
            self._content_container = VerticalScroll(id="config-pane")
            yield self._content_container

    def on_mount(self) -> None:
        sidebar = self.query_one("#config-sidebar", Vertical)
        for key, label in _PAGES:
            btn = Button(label, id=f"page-{key}")
            sidebar.mount(btn)
        self._render_page()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id and event.button.id.startswith("page-"):
            self._page = event.button.id.removeprefix("page-")
            self._render_page()
        elif event.button.id == "save":
            self._save()
        elif event.button.id == "cancel":
            self.app.pop_screen()

    def _render_page(self) -> None:
        assert self._content_container is not None
        self._content_container.remove_children()
        cfg = self._ctx.config
        if self._page == "general":
            self._content_container.mount(Label("Theme:"))
            self._content_container.mount(Input(value=cfg.ui.theme, id="ui.theme"))
            self._content_container.mount(Label("Timezone:"))
            self._content_container.mount(Input(value=cfg.timezone, id="timezone"))
            self._content_container.mount(Label("Log verbosity:"))
            self._content_container.mount(Input(value=cfg.ui.log_verbosity, id="ui.log_verbosity"))
        elif self._page == "fleets":
            self._render_fleets_page()
        elif self._page == "scheduler":
            self._content_container.mount(Label("Tick (seconds, 1–60):"))
            self._content_container.mount(
                Input(value=str(cfg.scheduler.tick_seconds), id="scheduler.tick_seconds")
            )
        elif self._page == "agent":
            self._content_container.mount(Label("Agent CLI command (space-separated):"))
            self._content_container.mount(
                Input(value=" ".join(cfg.agent_runner.agent_cli.command), id="agent.command")
            )
            self._content_container.mount(Label("Mode (stdin/flag/tempfile):"))
            self._content_container.mount(
                Input(value=cfg.agent_runner.agent_cli.mode, id="agent.mode")
            )
            self._content_container.mount(Label("Per-dock concurrency (1–4):"))
            self._content_container.mount(
                Input(value=str(cfg.agent_runner.concurrency.per_dock), id="agent.per_dock")
            )
            self._content_container.mount(Label("Global cap (1–16):"))
            self._content_container.mount(
                Input(value=str(cfg.agent_runner.concurrency.global_cap), id="agent.global_cap")
            )
            self._content_container.mount(Label("Kill grace seconds:"))
            self._content_container.mount(
                Input(value=str(cfg.agent_runner.kill_grace_seconds), id="agent.kill_grace")
            )
        elif self._page == "artifacts":
            self._content_container.mount(Label("Retention (e.g. 30d):"))
            self._content_container.mount(
                Input(value=cfg.artifacts.retention, id="artifacts.retention")
            )
            self._content_container.mount(Label("Sweep cron:"))
            self._content_container.mount(
                Input(value=cfg.artifacts.sweep_cron, id="artifacts.sweep_cron")
            )
        elif self._page == "web":
            self._content_container.mount(Label("Port:"))
            self._content_container.mount(Input(value=str(cfg.web.port), id="web.port"))
            self._content_container.mount(Label("Host:"))
            self._content_container.mount(Input(value=cfg.web.host, id="web.host"))
        elif self._page == "tunnels":
            self._content_container.mount(Label("devtunnel binary path:"))
            self._content_container.mount(
                Input(value=cfg.tunnels.devtunnel_path, id="tunnels.path")
            )
            self._content_container.mount(Label("Tunnel ID (optional):"))
            self._content_container.mount(
                Input(value=cfg.tunnels.tunnel_id or "", id="tunnels.tunnel_id")
            )
        elif self._page == "keybindings":
            self._content_container.mount(
                Label("Keybindings overrides (one per line: action=chord)")
            )
            kb_text = "\n".join(f"{k}={v}" for k, v in cfg.keybindings.items())
            self._content_container.mount(Input(value=kb_text, id="keybindings"))
        elif self._page == "about":
            self._content_container.mount(
                Static(
                    f"harbin · paths:\n"
                    f"  config: {self._ctx.paths.config_dir}\n"
                    f"  data:   {self._ctx.paths.data_dir}\n"
                    f"  logs:   {self._ctx.paths.log_dir}\n"
                )
            )
        if self._page != "about":
            self._content_container.mount(
                Horizontal(
                    Button("Save", id="save"),
                    Button("Cancel", id="cancel"),
                )
            )

    def _render_fleets_page(self) -> None:
        assert self._content_container is not None
        c = self._content_container
        c.mount(Label("Registered fleets:"))
        # Snapshot current dock states
        for state in self._ctx.dock_manager.states.values():
            line = (
                f"{state.row.name}  ·  {state.row.url}  ·  "
                f"{'disabled' if state.disabled else 'active'}"
            )
            c.mount(Static(line))
        c.mount(Label(""))
        c.mount(Label("Add fleet by git URL:"))
        c.mount(Input(placeholder="https://github.com/you/your-fleet", id="fleet.url"))
        c.mount(
            Horizontal(
                Button("Add fleet", id="add-fleet"),
                Button("Close", id="cancel"),
            )
        )

    def _save(self) -> None:
        """Best-effort save: re-validate config and write atomically."""
        try:
            raw = self._collect_current_yaml()
            cfg = Config.model_validate(raw)
        except Exception as e:
            self._ctx.console_writer(f"[error]config invalid: {e}[/error]")
            return
        from harbin.paths import atomic_write_text

        target = self._ctx.paths.config_dir / "config.yaml"
        atomic_write_text(target, yaml.safe_dump(cfg.model_dump(mode="python"), sort_keys=False))
        self._ctx.console_writer("config saved")
        # Propagate the apply-live subset to runner/scheduler/logging.
        try:
            self._ctx.apply_live_config(cfg)
        except Exception as e:  # pragma: no cover - defensive
            self._ctx.console_writer(f"[warn]live apply failed: {e}[/warn]")
        # update in-memory snapshot (status bar, etc.)
        self._ctx.config.__dict__.update(cfg.__dict__)
        self.app.pop_screen()

    def _collect_current_yaml(self) -> dict:
        """Gather all input widgets back into a config dict."""
        cfg = self._ctx.config
        data = cfg.model_dump(mode="python")
        # General
        for wid in self.query(Input):
            if wid.id == "ui.theme":
                data["ui"]["theme"] = wid.value
            elif wid.id == "ui.log_verbosity":
                data["ui"]["log_verbosity"] = wid.value
            elif wid.id == "timezone":
                data["timezone"] = wid.value
            elif wid.id == "scheduler.tick_seconds":
                try:
                    data["scheduler"]["tick_seconds"] = int(wid.value)
                except ValueError:
                    pass
            elif wid.id == "agent.command":
                data["agent_runner"]["agent_cli"]["command"] = wid.value.split() or ["copilot"]
            elif wid.id == "agent.mode":
                data["agent_runner"]["agent_cli"]["mode"] = wid.value
            elif wid.id == "agent.per_dock":
                try:
                    data["agent_runner"]["concurrency"]["per_dock"] = int(wid.value)
                except ValueError:
                    pass
            elif wid.id == "agent.global_cap":
                try:
                    data["agent_runner"]["concurrency"]["global_cap"] = int(wid.value)
                except ValueError:
                    pass
            elif wid.id == "agent.kill_grace":
                try:
                    data["agent_runner"]["kill_grace_seconds"] = int(wid.value)
                except ValueError:
                    pass
            elif wid.id == "artifacts.retention":
                data["artifacts"]["retention"] = wid.value
            elif wid.id == "artifacts.sweep_cron":
                data["artifacts"]["sweep_cron"] = wid.value
            elif wid.id == "web.port":
                try:
                    data["web"]["port"] = int(wid.value)
                except ValueError:
                    pass
            elif wid.id == "web.host":
                data["web"]["host"] = wid.value
            elif wid.id == "tunnels.path":
                data["tunnels"]["devtunnel_path"] = wid.value
            elif wid.id == "tunnels.tunnel_id":
                data["tunnels"]["tunnel_id"] = wid.value or None
            elif wid.id == "keybindings":
                kb: dict[str, str] = {}
                for line in wid.value.splitlines():
                    if "=" in line:
                        k, v = line.split("=", 1)
                        kb[k.strip()] = v.strip()
                data["keybindings"] = kb
        return data
