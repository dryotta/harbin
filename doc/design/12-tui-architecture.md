# tui-architecture — Sub-spec

> Status: **draft** · scope: Textual app composition, screen stack, widget tree, theme tokens, key-binding registry, `/config` modal.
> Parent: [`design-overview.md`](./design-overview.md).

The TUI is the operator surface for harbin. This doc pins the Textual app shape — what's an `App`, what's a `Screen`, what's a `Widget` — and the cross-cutting concerns (theme, focus, keys, accessibility) that the per-command surfaces in [`13-repl-and-commands`](./13-repl-and-commands.md) rely on.

---

## 1 · App and screen stack

```
HarbinApp(textual.App)
├── OverviewScreen      # default; pushed at startup
├── JobViewScreen       # one focused job (alt+N)
└── ConfigModalScreen   # /config
```

- `HarbinApp` is the single Textual `App` subclass. It owns the asyncio loop with `AppCore` (overview §3).
- Screens are pushed/popped via Textual's screen stack. Only one is visible at a time except for `ConfigModalScreen`, which is a `ModalScreen` overlaying whatever's behind.
- `esc` from `JobViewScreen` or `ConfigModalScreen` returns to the previous screen.

### 1.1 Common chrome

Every non-modal screen renders, in order:

```
┌─ Header (Static) ───────────────────────────────────────────┐
│  figlet logo · command center for AI agents                 │
├─ <screen-body> ─────────────────────────────────────────────┤
│  …                                                          │
├─ CommandLine (Input) ───────────────────────────────────────┤
│  > _                                                        │
├─ StatusBar (Footer) ────────────────────────────────────────┤
│  N jobs · M fleets · active: <name> · alt+0 = overview      │
└─────────────────────────────────────────────────────────────┘
```

Header, CommandLine, and StatusBar are owned by `HarbinApp` and rendered around the active screen's body — *not* re-implemented per screen. The CommandLine is always visible and always focusable.

---

## 2 · Widget tree — OverviewScreen

```
OverviewScreen
└── Vertical
    ├── JobMonitor               # bordered container, title="monitor"
    │   └── VerticalScroll
    │       └── JobRow*          # one per active+recent job (max 50)
    └── Console                  # RichLog, bordered, title="console", flex:1
```

- **JobMonitor** is fixed-height enough to show ~8 rows by default; it grows up to half the screen. Above that, the Console gets the rest.
- **JobRow** layout (single line):
  ```
  [alt+N] ● running  <fleet>·<task-or-adhoc>  #<short-id>  <elapsed>
  ```
  Glyph + color per overview §7. Hovering or focusing a row offers `enter` to push `JobViewScreen` for that job.
- **Console** is append-only (Textual `RichLog`). Per-job stdio is **not** routed here — that lives in the job's `JobViewScreen`. The Console only receives:
  - REPL acknowledgements (`queued #abc123`, `unknown command — did you mean /jobs?`)
  - System events (`fleet 'news' disabled: invalid schedule.yaml`)
  - Fleet warnings (`⚠ dock dirty: harbin-agent-sample-news`)

---

## 3 · Widget tree — JobViewScreen

```
JobViewScreen
└── Vertical
    ├── JobHeader                # static one-liner
    │   "<fleet>·<task-or-adhoc>  #<short-id>  <status>  exit=<code>  elapsed=<dur>"
    └── RichLog                  # live job stdio
```

- Stdio is rendered from the live tail of `job_log_chunks` plus the runner's in-memory ring (the union is materialized into the RichLog).
- Stream coloring: `stdout` = default fg, `stderr` = `activity` color, `system` = `muted` italic.
- `j`/`k` and PageUp/PageDown scroll. `g`/`G` jump to top/bottom. `esc` returns to Overview.

---

## 4 · Key bindings

Bindings are declared on `HarbinApp` and inherited by screens. Per-screen bindings (e.g. `JobViewScreen` scroll keys) live on the screen class.

| Chord | Action | Scope |
|---|---|---|
| `alt+0` | focus OverviewScreen | global |
| `alt+1..9` | focus the N-th job in JobMonitor | global |
| `esc` | pop screen (to Overview) | JobView, ConfigModal |
| `tab` / `shift+tab` | move focus | global |
| `enter` | activate focused row / submit CommandLine | global |
| `ctrl+c` | request shutdown (same as `/exit`) | global |
| `ctrl+l` | clear Console | OverviewScreen |
| `j`/`k`, PageUp/PageDown, `g`/`G` | scroll | JobView |

The CommandLine is **always** the default focus when no other widget is explicitly focused, so typing a `/` or `@` Just Works.

### 4.1 `keybindings:` overrides

`config.yaml.keybindings` is a `dict[str, str]` mapping action name → chord. Action names are stable identifiers used here (e.g. `focus-overview`, `clear-console`, `quit`). Override example:

```yaml
keybindings:
  clear-console: "ctrl+k"
  quit: "ctrl+q"
```

Chords use Textual's binding grammar (`alt+1`, `ctrl+shift+a`). Unknown action names or invalid chords surface as a `UserError` on load (see [`03-configuration`](./03-configuration.md) §4).

This resolves the configuration-doc open question: **action-symbolic overrides**, not chord-to-chord remapping.

---

## 5 · Theme

The `harbor` palette from overview §7 is implemented as Textual CSS variables, registered by `harbin.tui.theme`.

```python
HARBOR = {
    "bg":       "#0b1620",
    "fg":       "#cde2ec",
    "accent":   "#5fb6c8",
    "activity": "#f0a857",
    "muted":    "#5e7585",
    "error":    "#ff7575",
}
```

Wiring:

- `HarbinApp.CSS` sets `--harbin-bg`, etc., from the active theme.
- All widget CSS references the variables; no hex codes outside `theme.py`.
- `App.register_theme(name, mapping)` is called for every shipped theme.
- Theme swap at runtime: `/config → General → Theme` writes the new name, `harbin.tui.theme.apply(app, name)` rebinds the CSS variables and forces a refresh. No reload required.

### 5.1 Status glyphs

Reinforce, not replace, color (overview §7 — accessibility):

| State | Glyph | Color |
|---|---|---|
| queued | `○` | `muted` |
| running | `●` | `activity` |
| success | `✓` | `accent` |
| failed | `✗` | `error` |
| cancelled | `⊘` | `muted` |
| warning | `⚠` | `activity` |

---

## 6 · CommandLine widget

- A Textual `Input` with prefix `> `.
- Submits on `enter`; calls into the REPL parser (see [`13-repl-and-commands`](./13-repl-and-commands.md)).
- Suggestions provided via Textual's `Suggester` interface — see the REPL doc for source data (fleet names for `@`, command names for `/`, etc.).
- On `/exit`, the widget mutates into a confirm prompt:
  ```
  > quit? 2 jobs running [y/N] _
  ```
  `y` quits; anything else cancels and restores the prompt.

The CommandLine never blocks; even during shutdown it stays interactive until the app exits.

---

## 7 · StatusBar

- Textual `Footer` subclass.
- Renders `<active-jobs> jobs · <fleets> fleets · active: <focused-job-or-"overview"> · alt+0 = overview`.
- Counts come from a small reactive store on `HarbinApp` updated by event handlers (`job_started`, `job_ended`, etc.).
- Color of the active-jobs count switches to `activity` when ≥ 1 is running.

---

## 8 · `/config` ModalScreen

```
ConfigModalScreen (ModalScreen)
└── Grid
    ├── ConfigSidebar         # vertical list of page names
    └── ConfigPane            # currently-selected page widget
```

Pages are individual widget classes (`GeneralPage`, `FleetsPage`, `SchedulerPage`, `AgentRunnerPage`, `ArtifactsPage`, `WebPage`, `TunnelsPage`, `KeybindingsPage`, `AboutPage`). Selecting in the sidebar swaps the content of `ConfigPane`.

### 8.1 Widget choice per field type

| Field type | Widget |
|---|---|
| `str` (free) | `Input` |
| `str` (enum) | `Select` |
| `int` (range) | `Input` with numeric validator |
| `bool` | `Switch` |
| `duration` (e.g. `30d`) | `Input` with regex validator |
| `path` | `Input` + browse button (v2; v1 = `Input` only) |
| `list[str]` | `Input` with comma separator (v1 keep it simple) |

### 8.2 Dirty state and save

- A page tracks a `dirty: bool` reactive flag; the sidebar marks dirty pages with a `*` suffix.
- `[ Save ]` validates the **whole** pydantic Config (not just dirty pages — catches cross-page constraints). On error, surface inline next to the offending field and refuse to close.
- `[ Cancel ]` with any dirty page opens a tiny confirm: `discard changes? [y/N]`.
- `esc` is bound to Cancel.

### 8.3 Fleets page specifics

- One row per registered fleet: name, URL, status (active/disabled), per-row drawer with `sync_interval` override, push policy, retention, and `[ Remove fleet ]`.
- `+ Add fleet` opens a two-step wizard inside the modal:
  1. `Input` for git URL → click `Next`.
  2. Show resolved clone path; click `Confirm`. Then runs the registration flow (see [`07-fleet-and-dock-manager`](./07-fleet-and-dock-manager.md) §2). Progress and errors render inline.

`Remove fleet` is a destructive action: confirm modal lists what will be deleted (dock dir, artifacts older than retention, DB rows; running jobs are first cancelled). Push-back history on the remote is **not** touched.

### 8.4 About page

Read-only: version, paths.* values, links to the log file and the docs.

---

## 9 · Accessibility notes

- Status is conveyed by **glyph + color**, never color alone (§5.1).
- Monospace is assumed; the layout breaks under proportional fonts (acceptable — that's a Textual constraint).
- Focus ring uses the `accent` color underline; visible in all themes.
- No animations beyond the spinner glyph in `running` rows (and even that is a one-char rotation, not a full-redraw animation).

---

## 10 · Out of scope (v1)

- Mouse-first UX (mouse works for click-to-focus, but everything is keyboard-driven).
- Per-user persistent screen layouts.
- Drag-resize between JobMonitor and Console (Textual supports it but adds keybinding/discoverability surface for limited value).
- A dedicated "fleet detail" screen (the Fleets page in `/config` is enough).
