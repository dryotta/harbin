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


LOGO = r"""
 _              _    _
| |__   __ _ _ _| |__(_)_ _
| '_ \ / _` | '_| '_ \ | ' \
|_||_/_\\__,_|_| |_.__/_|_||_|
"""


def css_for_theme(name: str) -> str:
    palette = THEMES.get(name, HARBOR)
    return f"""
Screen {{
    background: {palette["bg"]};
    color: {palette["fg"]};
}}

#header {{
    color: {palette["accent"]};
    background: {palette["bg"]};
    padding: 0 1;
    height: 4;
    border-bottom: solid {palette["accent"]};
}}

#monitor {{
    border: round {palette["accent"]};
    border-title-align: left;
    background: {palette["bg"]};
    margin: 0;
    padding: 0 1;
    height: 1fr;
    min-height: 6;
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

ConfigSidebar {{
    width: 18;
    background: {palette["bg"]};
    border-right: solid {palette["accent"]};
}}

.dirty {{ color: {palette["activity"]}; }}

.error-line {{ color: {palette["error"]}; }}
.muted      {{ color: {palette["muted"]}; }}
"""
