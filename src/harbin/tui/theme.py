"""Theme tokens and CSS for harbin (sub-spec 12 §5)."""

from __future__ import annotations

from collections.abc import Mapping

HARBOR: Mapping[str, str] = {
    "bg": "#0b1620",
    "fg": "#cde2ec",
    "accent": "#5fb6c8",
    "activity": "#f0a857",
    "muted": "#5e7585",
    "error": "#ff7575",
}

DARK: Mapping[str, str] = {
    "bg": "#0a0a0a",
    "fg": "#e4e4e4",
    "accent": "#7aa2f7",
    "activity": "#e0af68",
    "muted": "#565f89",
    "error": "#f7768e",
}

THEMES: dict[str, Mapping[str, str]] = {
    "harbor": HARBOR,
    "dark": DARK,
}


STATUS_GLYPHS: dict[str, str] = {
    "queued": "○",
    "starting": "○",
    "running": "●",
    "success": "✓",
    "failed": "✗",
    "cancelled": "⊘",
    "archived": "·",
    "warning": "⚠",
}


STATUS_TOKEN: dict[str, str] = {
    "queued": "muted",
    "starting": "muted",
    "running": "activity",
    "success": "accent",
    "failed": "error",
    "cancelled": "muted",
    "archived": "muted",
    "warning": "activity",
}


LOGO = r""" _                _     _
| |__   __ _ _ __| |__ (_)_ __
| '_ \ / _` | '__| '_ \| | '_ \
| | | | (_| | |  | |_) | | | | |
|_| |_|\__,_|_|  |_.__/|_|_| |_|
"""


def css_for_theme(name: str) -> str:
    palette = THEMES.get(name, HARBOR)
    return f"""
Screen {{
    background: {palette["bg"]};
    color: {palette["fg"]};
}}

#header {{
    background: {palette["bg"]};
    padding: 0 1;
    height: 7;
    border-bottom: solid {palette["accent"]};
}}

#header-logo {{
    color: {palette["accent"]};
    width: 36;
    height: 5;
    content-align: left top;
}}

#header-tagline {{
    color: {palette["muted"]};
    width: 1fr;
    height: 5;
    content-align: left bottom;
    padding: 0 1;
}}

#overview {{
    height: 1fr;
}}

#monitor {{
    border: round {palette["accent"]};
    border-title-align: left;
    background: {palette["bg"]};
    margin: 0;
    padding: 0 1;
    height: auto;
    min-height: 3;
    max-height: 50%;
}}

#console {{
    border: round {palette["accent"]};
    border-title-align: left;
    background: {palette["bg"]};
    height: 1fr;
    padding: 0 1;
}}

#commandline {{
    background: {palette["bg"]};
    color: {palette["fg"]};
    border: tall {palette["accent"]};
    height: 3;
    padding: 0 1;
}}

#statusbar {{
    background: {palette["bg"]};
    color: {palette["muted"]};
    height: 1;
    padding: 0 1;
}}

JobRow {{
    height: 1;
    color: {palette["fg"]};
}}

JobRow.running   {{ color: {palette["activity"]}; }}
JobRow.success   {{ color: {palette["accent"]}; }}
JobRow.failed    {{ color: {palette["error"]}; }}
JobRow.cancelled {{ color: {palette["muted"]}; }}
JobRow.queued    {{ color: {palette["muted"]}; }}
JobRow.starting  {{ color: {palette["muted"]}; }}
JobRow.warning   {{ color: {palette["activity"]}; }}

JobHeader {{
    color: {palette["fg"]};
    padding: 0 1;
    height: 2;
}}

/* ───────────────────────── /config modal ───────────────────────── */

ConfigModalScreen {{
    align: center middle;
}}

#config-grid {{
    grid-size: 2 1;
    grid-columns: 22 1fr;
    width: 90%;
    height: 80%;
    border: round {palette["accent"]};
    background: {palette["bg"]};
    padding: 0;
}}

#config-sidebar {{
    width: 22;
    background: {palette["bg"]};
    border-right: solid {palette["accent"]};
    padding: 1;
}}

#config-sidebar Button {{
    width: 100%;
    height: 1;
    background: {palette["bg"]};
    color: {palette["fg"]};
    border: none;
    text-style: none;
    margin: 0 0 0 0;
    padding: 0 1;
}}

#config-sidebar Button:hover {{
    background: {palette["accent"]} 20%;
    color: {palette["accent"]};
}}

#config-sidebar Button.-active {{
    background: {palette["accent"]} 30%;
    color: {palette["accent"]};
    text-style: bold;
}}

#config-pane {{
    padding: 1 2;
    background: {palette["bg"]};
}}

#config-pane Label {{
    color: {palette["muted"]};
    margin: 1 0 0 0;
}}

#config-pane Input {{
    background: {palette["bg"]};
    color: {palette["fg"]};
    border: tall {palette["accent"]};
    height: 3;
    margin: 0 0 0 0;
}}

#config-pane Button {{
    background: {palette["bg"]};
    color: {palette["accent"]};
    border: tall {palette["accent"]};
    height: 3;
    margin: 1 1 0 0;
    min-width: 10;
}}

#config-pane Button:hover {{
    background: {palette["accent"]} 20%;
}}

#config-pane Static {{
    color: {palette["fg"]};
    margin: 0;
}}

.dirty {{ color: {palette["activity"]}; }}

.error-line {{ color: {palette["error"]}; }}
.muted      {{ color: {palette["muted"]}; }}
"""
