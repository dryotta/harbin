# concurrency-and-errors — Sub-spec

> Status: **draft** · scope: event loop, startup/shutdown sequencing, signal handling, error taxonomy, app logging.
> Parent: [`design-overview.md`](./design-overview.md).

This is the engineering substrate every other subsystem relies on. It is short on purpose: harbin runs on one event loop and uses one logger; anything more elaborate is a smell.

---

## 1 · Event loop model

- **One asyncio loop, owned by `AppCore`.** Textual's `App.run_async` shares the same loop; harbin never creates a second one.
- **No threads** except those internal to dependencies (aiosqlite's worker thread, watchdog's observer thread). These are encapsulated behind awaitable APIs; harbin code never blocks on them.
- **Structured concurrency.** Every subsystem that owns long-running work runs it under an `asyncio.TaskGroup`. Bare `asyncio.create_task` is reserved for fire-and-forget work whose lifetime is bounded by a single REPL command (e.g. tab-completion lookups).
- **Task naming convention.** `task.set_name(f"{subsystem}.{role}[:{detail}]")`. Examples:
  ```
  scheduler.tick
  dock.sync:harbin-agent-sample-news
  runner.job:a1b2c3
  artifacts.sweep
  ```
  Names show up in tracebacks and in the planned `/debug tasks` panel (v2).

---

## 2 · Startup sequence

The order matters: each step depends on the previous one's invariants.

1. Parse CLI args; resolve `HARBIN_HOME` if set; choose subcommand (`tui` default, `serve`, `sample-fleet`).
2. Create state directories via `paths.ensure_*`.
3. Initialize the app logger (file sink + ring buffer; see §6).
4. Open DB; run migrations (`db.migrate.run`). **Fail-fast** on migration error — harbin exits with nonzero and a clear message.
5. Load `config.yaml`. Boot-time validation error → exit (see [`03-configuration`](./03-configuration.md)).
6. Construct subsystems: `Store`, `DockManager`, `ArtifactManager`, `Scheduler`, `AgentRunner`.
7. Enumerate registered fleets from DB; start watchdog observers on each dock's `.harbin/`.
8. Open the root `TaskGroup` and start the long-running coroutines: scheduler tick loop, dock sync loop, artifact sweep timer.
9. Mount the UI: Textual `App.run_async` (TUI), or textual-serve (`harbin serve`).
10. Emit one info log line: `harbin ready · <N> fleets · <M> tasks`.

A failure in steps 1–7 exits before the UI mounts. Failures in 8–9 propagate to the top-level handler, which logs and exits.

---

## 3 · Shutdown sequence

Triggered by SIGINT, SIGTERM (POSIX), Textual's quit binding, or `/exit y`.

1. Mark the app as **draining**: scheduler stops ticking; REPL rejects new `@fleet` invocations with `harbin is shutting down`.
2. Cancel scheduler and dock-sync TaskGroup children.
3. For each running job: SIGTERM → wait up to `agent_runner.kill_grace_seconds` → SIGKILL. (Detail in [`10-agent-runner`](./10-agent-runner.md).)
4. Flush any pending log-chunk writes; commit outstanding DB state.
5. Stop watchdog observers.
6. Close DB connection.
7. Exit: code `0` on normal shutdown, `130` on SIGINT, `143` on SIGTERM, `2` on internal error.

**Hung-shutdown watchdog.** A 30 s `asyncio.wait_for` wraps steps 2–6. If it trips, harbin logs `shutdown watchdog tripped` and `os._exit(2)`. This is the only place harbin uses `_exit`.

---

## 4 · Signal handling

- **POSIX.** `loop.add_signal_handler(SIGINT, …)` and `loop.add_signal_handler(SIGTERM, …)` both schedule the shutdown coroutine.
- **Windows.** `add_signal_handler` is unsupported on the Proactor loop. Harbin uses `signal.signal(SIGINT, …)` for Ctrl-C; SIGTERM is not deliverable to console apps, so Windows users rely on:
  - the in-app `/exit` confirm, or
  - Textual's `Ctrl-C` binding (caught by `signal.signal`), or
  - closing the terminal window (no graceful shutdown — equivalent to SIGKILL).

This caveat is mirrored in [`06-packaging-and-install`](./06-packaging-and-install.md) §4.

---

## 5 · Error taxonomy

`harbin.errors` defines the hierarchy. Every error harbin raises (and most it catches at boundaries) is a subclass of `HarbinError`.

```
HarbinError                       (abstract base)
├── UserError                     mistake the user can fix immediately
│   ├── ParseError                bad slash command, bad YAML at user-input boundary
│   └── ValidationError           pydantic-derived; config or YAML schema
├── FleetError                    per-fleet failure that must NOT crash harbin
│   ├── DockError                 git clone/fetch/push problem
│   ├── ScheduleError             cron parse failure post-load
│   └── RunnerError               subprocess spawn/IO failure
└── InternalError                 bug in harbin itself
```

Each instance carries:

```python
class HarbinError(Exception):
    code: str                      # stable identifier, e.g. "fleet.dock.dirty"
    message: str                   # single-line, user-facing
    detail: str | None = None      # multi-line, shown when expanded
```

`code` strings are dotted, lowercase, and stable across releases — they appear in logs and may be referenced from docs.

### 5.1 Surfacing rules

| Class | Where it surfaces |
|---|---|
| `UserError` | One red line in the Console widget. No modal. Logged at WARN. |
| `FleetError` | Warning row on the affected fleet in JobMonitor; full detail in `/logs` and app log at WARN. The fleet continues to run on the next cycle. |
| `InternalError` | Modal with traceback summary plus "press y to copy"; logged at ERROR with full traceback. |
| Unhandled exception | Caught at the TaskGroup boundary; reclassified as `InternalError` with `code = "internal.unhandled"`; surfaced as above. |

The TUI surfaces are spec'd in [`12-tui-architecture`](./12-tui-architecture.md); this doc owns the *taxonomy*, not the *widgets*.

---

## 6 · App logger

One root logger: `harbin`. Subsystems use named children (`harbin.scheduler`, `harbin.runner`, `harbin.dock`, etc.). Initialization happens in `harbin.logging.setup(config)` during startup step 3.

### 6.1 Sinks

- **Rotating file** at `paths.log_dir / "harbin.log"`, `RotatingFileHandler(maxBytes=5*1024*1024, backupCount=5)`.
- **In-process ring buffer** of the last 2000 formatted lines; surfaced by `/debug log` (v2) and dumped to stderr on unhandled crashes.

### 6.2 Format and level

- Level: `config.ui.log_verbosity` (default `info`).
- Format: `%(asctime)s %(levelname)-7s %(name)s %(message)s`, ISO-8601 timestamps in the configured timezone.

### 6.3 What goes where

| Source | Destination |
|---|---|
| harbin internal events (lifecycle, errors, scheduler decisions) | App logger |
| User-issued slash commands and their results | Console widget (TUI) + app logger at DEBUG |
| Agent stdout/stderr | `job_log_chunks` + `<artifact_dir>/job.log` only — **never** the app logger |

The last row is important: a chatty agent must not be able to roll the app log.

### 6.4 Redaction

A small regex list scrubs known secret shapes from formatted lines before they hit either sink:

```
GH_TOKEN_PATTERN  = r"gh[ps]_[A-Za-z0-9]{36,}"
BEARER_PATTERN    = r"Bearer\s+[A-Za-z0-9._\-]+"
```

Matches are replaced with `***REDACTED***`. No PII redaction; file paths and prompts are user-authored and shown verbatim.

---

## 7 · Open questions

- Whether `InternalError` modals should offer a "draft a GitHub issue" link prefilled with code, message, and last 50 log lines. Defer.

## 8 · Out of scope

- Multi-process supervision; harbin is foreground-only (overview §3.2).
- Crash reporting / telemetry to a third party.
- Per-subsystem rate-limiting of log volume (the 5×5 MiB rotation is enough).
