# repl-and-commands — Sub-spec

> Status: **draft** · scope: REPL parser, tab completion, per-command spec for every v1 slash command, `@fleet` enqueue path.
> Parent: [`design-overview.md`](./design-overview.md) §6.2.

The CommandLine widget ([`12-tui-architecture`](./12-tui-architecture.md) §6) is harbin's only typed entry surface. This doc fixes the grammar, the completion strategy, and the contract of each v1 slash command.

---

## 1 · Grammar

The parser dispatches on the **first character** of the submitted line:

| Starts with | Parsed as |
|---|---|
| `/` | slash command |
| `@` | at-mention (ad-hoc prompt to a fleet) |
| anything else | `UserError` — `start with '/' or '@'` |

Empty input is silently ignored (no error, no log).

### 1.1 Slash command tokenization

Lines are tokenized via `shlex.split(line, posix=True)`. This gives natural quoting:

```
/logs abc123 -f
/artifacts harbin-agent-sample-news "briefs/2024-04-01.md"
```

Unclosed quotes are a `ParseError` with the column noted. The first token is the command name; the rest are passed to its handler as a list. Each command declares its own argparse-like grammar.

### 1.2 At-mention parsing

```
@<fleet-name> <prompt text…>
```

The fleet name is matched against `^[a-z][a-z0-9-]{0,62}$` (overview §4.1; widened in IMPLEMENTATION_NOTES.md §17 from the original `{1,30}` to fit real-world repo names up to 63 chars). The **rest of the line** — verbatim, including leading/trailing whitespace trimmed — becomes the prompt. No `shlex` involved; prompts often contain quotes and shell metachars and should be left alone.

Validation:

- Unknown fleet → `UserError` with `did you mean …?` (difflib suggestion).
- Empty prompt → `UserError` `prompt is empty`.

On success, the parser calls `AgentRunner.enqueue(fleet, prompt, source="repl")` and prints one Console line: `queued #abc123  <fleet>·adhoc`.

---

## 2 · Suggester (tab completion)

Implemented as a Textual `Suggester` (`harbin.repl.suggester.HarbinSuggester`). It inspects the buffer's current state and returns a single completion proposal (Textual draws it as ghost text).

### 2.1 Sources by context

| Buffer state | Completion source |
|---|---|
| `@` (just the at-sign) or `@<prefix>` | registered fleet names (`Store.list_fleets()`) |
| `/` or `/<prefix>` | slash command names (see §3) |
| `/help <prefix>` | slash command names |
| `/logs <prefix>` / `/cancel <prefix>` | active job `short_id` values |
| `/artifacts <fleet-prefix>` | fleet names |
| `/artifacts <fleet> <path-prefix>` | filesystem entries under the fleet's artifact root |
| `/sync <prefix>` / `/schedule <prefix>` | fleet names |
| `/tunnel <prefix>` | literals `start`, `stop`, `status` |

The Suggester is **single-prefix**: it returns at most one match (alphabetically first). When more than one matches, hitting `tab` cycles through them by re-submitting the buffer with a hint; v1 keeps this simple and shows only the first match.

### 2.2 Unknown command "did you mean …?"

On parse, if the first token of a slash line is not a known command, the parser falls back to `difflib.get_close_matches(cmd, KNOWN, n=1, cutoff=0.6)`. The Console renders:

```
unknown command '/jobss'. did you mean /jobs?
```

When no close match, just `unknown command '/foo'`.

---

## 3 · Slash commands (v1)

Each entry below specifies arguments, validation, output format on success, and the most common failure modes. Commands live in `harbin.repl.commands.<name>` modules implementing a small `Command` protocol (`name`, `aliases`, `argspec`, `async execute(ctx, args)`).

### 3.1 `/help [cmd]`

| Args | Validation | Output |
|---|---|---|
| `cmd` (optional) | If given, must be a known command name | Without arg: one-line summary per known command. With arg: detailed help for that command (including arg spec and one example). |

Errors: unknown `cmd` → `UserError` with did-you-mean.

Help text is loaded from inline strings on the command class. Resolving the open question from `plan.md` §open-questions: **inline strings**, not external markdown files. Keeps localization and discoverability in one place.

### 3.2 `/jobs [--all]`

| Args | Validation | Output |
|---|---|---|
| `--all` flag (optional) | none | Default: a table of `queued|starting|running` jobs plus the last 20 terminal jobs. With `--all`: every non-archived job. |

Output (medium-density table):

```
status     fleet                       task         #id     started               elapsed
●running   harbin-agent-sample-news    morning-brief #a1b2c3 2026-04-01T07:00:01Z  00:12
✓success   harbin-agent-sample-prices  hourly-prices #d4e5f6 2026-04-01T06:00:00Z  00:08
```

### 3.3 `/logs <job-id> [-f] [-n N]`

| Args | Validation | Output |
|---|---|---|
| `<job-id>` | required; must match a known `short_id` | Stream the captured stdio |
| `-f` | flag | Tail mode — re-renders on new chunks until `esc` |
| `-n N` | int, default 200 | Number of recent chunks to render initially |

Errors: unknown job id → `UserError` with did-you-mean against active and recent jobs.

Implementation note: `/logs <id>` is essentially "push `JobViewScreen` for that job"; `-f` is the default behavior in that screen. The slash command exists for symmetry and for the `-n` knob.

### 3.4 `/cancel <job-id>`

| Args | Validation | Output |
|---|---|---|
| `<job-id>` | required; must be an active job | `cancelling #<id>… ok` on success |

Errors:
- Unknown job → `UserError`.
- Already-terminal job → `UserError` `job is already <status>`.

See [`10-agent-runner`](./10-agent-runner.md) §5 for the cancellation mechanics.

### 3.5 `/artifacts <fleet> [path]`

| Args | Validation | Output |
|---|---|---|
| `<fleet>` | required; must be registered | Opens the artifact browser panel rooted at the fleet's artifact dir |
| `[path]` | optional; relative to the fleet root | Opens the browser focused on that path |

Errors:
- Unknown fleet → `UserError`.
- Path traversal (`..`) → `UserError`.
- Path not found → opens the parent and shows a warning row.

The browser is a TUI panel ([`08-artifact-manager`](./08-artifact-manager.md) §5); `/artifacts` is just a shortcut for opening it.

### 3.6 `/sync [fleet]`

| Args | Validation | Output |
|---|---|---|
| `[fleet]` | optional; if absent → sync all fleets | One Console line per fleet: `synced <fleet>: ff'd 3 commits` or `<fleet>: dirty — skipped` etc. |

Mechanics in [`07-fleet-and-dock-manager`](./07-fleet-and-dock-manager.md) §3. The user-initiated `/sync` runs **immediately** and ignores the periodic timer's next-fire time.

### 3.7 `/schedule [fleet]`

| Args | Validation | Output |
|---|---|---|
| `[fleet]` | optional; if absent → show every fleet | Table of `(task-id, cron, next-fire, last-fire)` |

`next-fire` is computed via `croniter(..., now).get_next(datetime)`; `last-fire` is `schedule_state.last_fire_ts`.

### 3.8 `/tunnel [start|stop|status]`

Specified in [`14-web-ui-and-tunnels`](./14-web-ui-and-tunnels.md) §2.

| Args | Validation | Output |
|---|---|---|
| subcommand (optional, default `status`) | one of `start`, `stop`, `status` | See linked spec |

### 3.9 `/config`

| Args | Validation | Output |
|---|---|---|
| none | none | Pushes `ConfigModalScreen` |

Detailed in [`12-tui-architecture`](./12-tui-architecture.md) §8.

### 3.10 `/exit`

| Args | Validation | Output |
|---|---|---|
| none | none | Mutates the CommandLine into a confirm prompt (see [`12-tui-architecture`](./12-tui-architecture.md) §6). `y` triggers shutdown; anything else cancels. |

The shutdown path is in [`04-concurrency-and-errors`](./04-concurrency-and-errors.md) §3.

---

## 4 · Command registry

```python
KNOWN: dict[str, Command] = {
    "help":      HelpCommand(),
    "jobs":      JobsCommand(),
    "logs":      LogsCommand(),
    "cancel":    CancelCommand(),
    "artifacts": ArtifactsCommand(),
    "sync":      SyncCommand(),
    "schedule":  ScheduleCommand(),
    "tunnel":    TunnelCommand(),
    "config":    ConfigCommand(),
    "exit":      ExitCommand(),
}
```

- The registry is **closed** in v1 — no plugin or extension mechanism. New commands require a sub-spec amendment.
- No aliases in v1 (e.g. `/quit` is not `/exit`). Keeps the surface tiny and `/help` honest.

---

## 5 · Output conventions

- Every successful command emits **at most one** Console line summarizing the outcome. Anything more goes in a panel/modal/JobView.
- Failures emit one red `UserError` line per error encountered, plus optional `detail:` continuation lines indented two spaces.
- Tables use a small renderer (`harbin.repl.table`) that handles column padding; no Rich tables for slash output (kept minimal-style per overview §7).
- Timestamps are rendered in `config.timezone` with ISO-8601 minus the fractional seconds: `2026-04-01T07:00:01Z`.

---

## 6 · Discoverability principles

- The empty state (no fleets registered) prints the two onramps (`/config → Fleets → + Add fleet` or `harbin sample-fleet add`).
- `/help` lists every command; `/help <cmd>` is exhaustive for that command's args.
- `did you mean …?` rescues typos.
- Unknown args inside a known command produce that command's help text appended to the error.

---

## 7 · Open questions

- Whether `/jobs` should accept a fleet filter (`/jobs harbin-agent-sample-news`). Easy to add; defer until users ask.
- Whether `/cancel` should accept multiple ids at once. Same — defer.

## 8 · Out of scope (v1)

- Slash command aliases.
- Plugin / extension registration.
- Mid-line completion popups (we use Textual's ghost-text Suggester only).
- History recall (`up`/`down` arrow in CommandLine); Textual's `Input` does not provide this; v2 candidate.
- Multi-line prompts in `@fleet`; the CommandLine is a single-line `Input`. Multi-line prompts come from `schedule.yaml`.
