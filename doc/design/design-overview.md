# harbin — Design Doc

> *A minimalist command center for GitHub Copilot AI agents.*
>
> Scope: full product vision, sized for an AI coding agent to implement in one shot.

This is the single design document. Every subsystem is described at the level needed to agree on shape, vocabulary, and interfaces. Where a detail is **non-obvious or load-bearing**, it is pinned inline here; everything else is left to the implementation. There are no sub-specs.

---

## 1 · Vision

Harbin is a single Python process that lets a developer run, schedule, and observe a small fleet of GitHub-Copilot-style coding agents working in repos they control.

Conceptually: harbin is a **harbor**. Each repo it manages is a **fleet** moored in its own **dock**. Harbin watches the docks, pulls them up to date, fires off agent runs (a **job**) on a schedule or on demand, captures their output as **artifacts**, and surfaces the whole thing through one terminal UI.

The product is intentionally small. It does **one** thing — run repos that are set up to host coding agents — and exposes them as a unified workspace. Anything that does not serve that goal is out of scope.

---

## 2 · Vocabulary

| Term | Meaning |
|---|---|
| **Fleet** | A GitHub repo configured as a harbin target. Contains a `.harbin/` directory with `fleet.yaml` (identity + policy) and optionally `schedule.yaml` (cron tasks). |
| **Dock** | Harbin's local working tree for one fleet — a plain `git clone` under `~/.local/share/harbin/docks/<name>/`. One dock per fleet. |
| **Agent CLI** | The external binary harbin invokes to do agent work (e.g. `copilot`, `gh copilot`, or any equivalent). Configured globally; overridable per fleet. Harbin is agnostic about which one. |
| **Job** | One execution of `(fleet, prompt)`. Has a status, stdio capture, an artifact directory, and a source. |
| **Source** | Where a job originated: `repl` (typed `@fleet PROMPT`) or `schedule` (cron task fired). |
| **Task** | A recurring saved prompt: `(id, cron, prompt)` declared in `schedule.yaml`. When a task fires it enqueues a job with `source=schedule`. |
| **Artifact** | Files a job produces under `~/.local/share/harbin/artifacts/<fleet>/<task-id-or-adhoc>/<job-id>/`, including `job.log`. |
| **CommandLine** | The single-line input at the bottom of the TUI. Accepts ad-hoc prompts as `@fleet PROMPT`. |

---

## 3 · Architecture overview

A **single foreground Python process**. No daemon, no IPC, no separate services. The Textual app and all subsystems share one asyncio event loop.

```
┌─────────────────────  one  harbin  process  ──────────────────────┐
│                                                                    │
│              ┌──────────┐         ┌────────────┐                   │
│              │   TUI    │         │ CommandLine│                   │
│              │ (Textual)│         │  (@fleet)  │                   │
│              └────┬─────┘         └─────┬──────┘                   │
│                   └───────────┬─────────┘                          │
│                               ▼                                    │
│                      ┌──────────────┐                              │
│                      │   AppCore    │                              │
│                      │  (asyncio    │                              │
│                      │   event bus) │                              │
│                      └──┬───────┬───┘                              │
│             ┌───────────┘       └───────────┐                      │
│             ▼                               ▼                      │
│   ┌──────────────────┐            ┌──────────────────┐             │
│   │   Dock Manager   │            │    Scheduler     │             │
│   │  (clone, pull,   │            │  (cron parser,   │             │
│   │   stash;         │            │   in-process,    │             │
│   │   never pushes)  │            │   persisted)     │             │
│   └────────┬─────────┘            └────────┬─────────┘             │
│            ▼                               ▼                       │
│   ┌────────────────────────────────────────────┐                   │
│   │              Agent Runner                  │                   │
│   │  spawns <agent_cli> with prompt on stdin   │                   │
│   │  captures stdio, manages job lifecycle     │                   │
│   └─────────────────┬──────────────────────────┘                   │
│                     ▼                                              │
│   ┌────────────────────────────────────────────┐                   │
│   │      Artifact Manager + State Store        │                   │
│   │   artifacts/<fleet>/<task>/<job>/ + SQLite │                   │
│   └────────────────────────────────────────────┘                   │
└────────────────────────────────────────────────────────────────────┘
```

### 3.1 Stack

| Concern | Choice | Why |
|---|---|---|
| Language / runtime | **Python 3.14** | Modern Python; required for parts of the textual ecosystem. |
| TUI framework | **Textual** | Mature widget set; same toolkit toad uses. Direct ASCII-mockup translation. |
| Async runtime | **asyncio** (single loop) | Lets git ops, scheduler, agent subprocesses, and UI coexist without threads. |
| State store | **SQLite via `aiosqlite`** | Indexed queries (jobs/logs/schedule), single file, async-friendly, zero ops. |
| HTTP / fetching | **httpx** | Async, used by skills that need to fetch. |
| Config validation | **pydantic** | Schema for `config.yaml`, `fleet.yaml`, `schedule.yaml`. |
| Filesystem watching | **watchdog** | Hot-reload of `fleet.yaml` / `schedule.yaml`. |
| Cron parsing | **croniter** | Small, mature. |
| OS paths | **platformdirs** | XDG-style locations cross-platform. |
| Packaging / install | **uv** (`uv tool install harbin`) | Matches the toad model; fast and reproducible. |

### 3.2 Process model

Foreground only. No daemon, no system service. The expected usage pattern is: leave `harbin` running in a tmux/screen pane on the machine that hosts the fleets. The scheduler fires while harbin is up; missed crons are **silently skipped**. This trade-off is intentional — it deletes a large class of complexity (IPC, service install, multi-process coordination) at the cost of one operator habit.

### 3.3 State locations

| Path | Contents |
|---|---|
| `~/.config/harbin/config.yaml` | User preferences (pydantic-validated). |
| `~/.local/share/harbin/harbin.db` | SQLite — fleets, jobs, schedule state, log chunks. |
| `~/.local/share/harbin/docks/<fleet>/` | Cloned fleet repos (working trees). |
| `~/.local/share/harbin/artifacts/<fleet>/<task-id-or-adhoc>/<job-id>/` | Per-job artifact directories (including `job.log`). |
| `~/.cache/harbin/` | Ephemeral / discardable. |

Exact paths resolved via `platformdirs` so macOS and Windows get their native equivalents. Every config/state file harbin writes goes through a `tmp + replace` atomic helper. SQLite handles its own atomicity (WAL); job log files are append-only.

### 3.4 Concurrency & shutdown

- **One asyncio loop**, owned by `AppCore`; Textual's `App.run_async` shares it. No threads except those inside dependencies (aiosqlite worker, watchdog observer) — these are encapsulated behind awaitable APIs. Watchdog callbacks marshal to the loop via `loop.call_soon_threadsafe`.
- **Structured concurrency.** Long-running work runs under an `asyncio.TaskGroup`. Bare `create_task` is reserved for fire-and-forget work bounded by a single user action.
- **Task naming convention:** `task.set_name(f"{subsystem}.{role}[:{detail}]")` — e.g. `scheduler.tick`, `dock.sync:news`, `runner.job:a1b2c3`. Shows up in tracebacks.
- **Shutdown:** mark draining → cancel scheduler/sync tasks → SIGTERM each running job (10 s grace, then SIGKILL) → flush logs → close DB → exit (0 normal, 130 SIGINT, 143 SIGTERM).
- **Hung-shutdown watchdog:** the whole shutdown is wrapped in a 30 s `asyncio.wait_for`; if it trips, harbin logs and calls `os._exit(2)`. This is the only place harbin uses `_exit`.
- **Windows signal caveat:** `SIGTERM` is not deliverable to console apps; users quit via the quit shortcut, Ctrl-C (caught by `signal.signal`), or by closing the terminal (equivalent to SIGKILL).

---

## 4 · Fleet plane

### 4.1 Fleet contract

A repo qualifies as a fleet by containing a `.harbin/` directory. Minimum:

```
my-fleet-repo/
├── .harbin/
│   ├── fleet.yaml          # identity + policy (REQUIRED)
│   └── schedule.yaml       # cron tasks (OPTIONAL — fleets can be on-demand only)
├── .github/
│   ├── copilot-instructions.md   # repo-wide context (standard GH convention)
│   └── agents/                   # per-agent prompts (standard GH convention)
└── skills/                       # OPTIONAL, conventional location for reusable fragments
```

Harbin **reads `.harbin/` only**. The `.github/` and `skills/` directories follow standard GitHub Copilot conventions and are interpreted by the Agent CLI, not by harbin. This separation is what lets harbin be agent-agnostic: any CLI that follows reasonable repo conventions can run a fleet.

#### `fleet.yaml`

```yaml
name: harbin-agent-sample-news      # human-readable; must be unique in this install
default_branch: main

# Optional: override the global agent CLI for this fleet.
# v1 invocation contract is stdin-only: harbin writes the prompt text (UTF-8)
# to the subprocess's stdin and closes it. The command is launched as-is — no
# argv substitution, no shell.
agent_cli:
  command: ["copilot"]              # argv prefix; required

artifact_policy:
  retain: 30d                       # how long to keep artifacts on disk
```

#### `schedule.yaml`

```yaml
tasks:
  - id: morning-brief
    cron: "0 7 * * *"               # croniter syntax, local timezone
    prompt: |
      Act as the news curator. Generate a morning brief covering LLMs
      and robotics. Save the result as MARKDOWN to
      $HARBIN_ARTIFACT_DIR/brief.md.
  - id: hourly-prices
    cron: "0 * * * *"
    prompt: "Check current prices for the watchlist. Save to prices.json."
```

A task is **a saved prompt plus a cron expression**, nothing more. When it fires, the scheduler enqueues a job equivalent to typing `@fleet <prompt>` in the CommandLine. This is the **one-way-to-do-things** principle: ad-hoc and scheduled invocations share a single code path.

### 4.2 Dock Manager

Harbin's job is to keep each dock as a **clean, up-to-date working tree**. Harbin is read-only with respect to the remote — it **never pushes**. An agent that wants to publish changes does so within the job (e.g. `git push` as one of the steps in its own prompt); harbin treats that as opaque agent behavior.

- **One dock per fleet.** Path: `~/.local/share/harbin/docks/<fleet-name>/`. A plain `git clone --depth=50` (deepen on demand via `git fetch --unshallow`). Always a checked-out working tree of `default_branch` — never bare, never detached.
- **System `git` only.** No `pygit2` / `dulwich`. Auth delegates to the user's credential helper (`gh auth`, SSH agent, Git Credential Manager, `osxkeychain`). Harbin never stores tokens.
- **Pre-job pull.** Before spawning a job's agent subprocess, harbin runs `git fetch --prune origin` and then `git merge --ff-only origin/<default_branch>` if the worktree is clean. If the worktree is dirty before a job (rare — only happens if the previous post-job stash failed), the dock is marked dirty and the job is **skipped** with a warning row; no destructive operation runs.
- **Background sync.** A per-fleet coroutine `dock.sync:<fleet>` at `sync_interval` (default 5m) runs the same `fetch + ff` so docks stay fresh even when no jobs are firing. Sync never resets / rebases / force-updates.
- **Post-job cleanup.** After every job (success, failure, or cancel), harbin runs `git status --porcelain`; if anything is uncommitted/untracked, it runs `git stash push -u -m "harbin <job-id> @<utc-iso>"`. The worktree returns to a clean state for the next job. Stash entries accumulate as the operator's audit trail; harbin never drops them automatically.
- **Hot reload.** `watchdog` observes each dock's `.harbin/fleet.yaml` and `.harbin/schedule.yaml`. Edits are debounced and applied within a few seconds — performance is not a tight constraint here. Running jobs are unaffected (they capture their config at spawn time). A per-fleet validation failure disables **that fleet only**; other fleets keep running until the user fixes the file.

### 4.3 Artifact Manager

- **Root:** `~/.local/share/harbin/artifacts/` (overridable via `config.artifacts.root`; absolute path required).
- **Layout:** `<fleet>/<task-id|adhoc>/<job-id>/`. This is **the** authoritative log and artifact tree — see §5.4. The dir is created empty **before** the agent subprocess spawns (so `HARBIN_ARTIFACT_DIR` exists by the time the agent runs) and uses `mkdir(parents=True, exist_ok=False)`; a collision indicates a `short_id` collision (a bug).
- **Env var:** the agent runner sets `HARBIN_ARTIFACT_DIR` so agents can write idiomatically (`Path(os.environ["HARBIN_ARTIFACT_DIR"]) / "brief.md"`).
- **Scope:** agents may also write outside the artifact dir (e.g. `/tmp`, user-specified paths, or inside the dock — those land in the post-job stash). Harbin only tracks what lands inside the artifact dir.
- **Retention:** default 30 days per fleet, overridable in `fleet.yaml`. A daily sweep `rmtree`s expired job dirs and deletes their `job_log_chunks` rows; the `jobs` row stays (status flips to `archived`) so history remains visible. Active jobs are never swept; per-job failures are logged and don't abort the sweep.
- **Browser:** see §6.1 — the artifact tree is a first-class navigation surface in the TUI.

---

## 5 · Execution plane

### 5.1 Scheduler

- **In-process asyncio loop.** A single `Scheduler` coroutine wakes every `tick_seconds` (default 5 s, range 1..60) and asks each loaded task "are you due since last fire?"
- **Cron parsing:** `croniter` in `config.timezone` (default: system local). DB timestamps stored as UTC ISO-8601.
- **Missed-fire policy:** silent skip. If harbin was off when a cron should have fired, the firing is dropped. On startup, missing `last_fire_ts` rows are set to `now` (next fire = next due window after startup).
- **DST behavior.** Spring-forward fires at the impossible local time are **skipped** (croniter returns the next valid occurrence). Fall-back duplicated hours **fire once** (`last_fire_ts` gates the second). Clock jumps are trusted — the scheduler does not detect them.
- **Crash-vs-off semantics.** `schedule_state.last_fire_ts` is written **after** `enqueue_job` returns the inserted `jobs` row. A crash between "decide to fire" and "upsert" causes one re-fire on the next tick — by design, harbin prefers over-fire to under-fire on transient crashes (the off-case is what skip-missed handles).
- **Hot reload.** Schedule diffs from watchdog reconcile per `task_id`: add (insert), remove (delete cascade), unchanged `source_sha = sha256(cron || \0 || prompt)` → no-op, cron change → re-anchor `last_fire_ts = now`, prompt-only change → preserve cadence.

### 5.2 Agent Runner

The most pluggable subsystem.

- **Concurrency.** Per-dock cap default 1 (serial); a per-task `concurrency: parallel` opts out for read-only prompts. Global cap default 4. Per-dock-serial protects the shared mutable worktree from concurrent agents.
- **Job lifecycle:** `queued → starting → running → (success | failed | cancelled) → archived`.
- **Spawning:** `asyncio.create_subprocess_exec` (no shell). `cwd=<dock>`, `start_new_session=True` on POSIX / `CREATE_NEW_PROCESS_GROUP` on Windows — isolates the subprocess from harbin's session and lets cancellation signal the whole process group. Environment inherits the operator's full `os.environ` (so agent secrets like `GH_TOKEN`, `OPENAI_API_KEY` flow through) and is enriched with:
  ```
  HARBIN_ARTIFACT_DIR=<path>      # absolute path to the per-job dir
  HARBIN_FLEET=<name>
  HARBIN_TASK_ID=<id-or-"adhoc">
  HARBIN_JOB_ID=<short-hex>
  HARBIN_PROMPT=<the prompt text>
  ```
- **Invocation contract (stdin-only).** The agent CLI is run as `agent_cli.command` verbatim — no argv substitution, no shell. The prompt text (UTF-8) is written to the subprocess's stdin and the pipe is closed. No `flag` / `tempfile` modes in v1 — every supported agent CLI is expected to read its prompt from stdin (or to read its prompt out of `HARBIN_PROMPT` if it prefers).
- **Captured-at-spawn config.** When a job moves `queued → starting`, the runner snapshots `agent_cli`, `kill_grace_seconds`, and concurrency flags. Mid-run config edits do **not** affect running jobs.
- **Stdio capture.** Each stream is read line-by-line into (a) an in-memory ring buffer (4 MiB per job — backs the JobView when first opened) and (b) `<artifact_dir>/job.log` (append-only, the **authoritative** on-disk log) and (c) `job_log_chunks` in SQLite (the DB tail surface, capped to 4 MiB with oldest-first eviction inside the insert transaction; a `system` chunk records the truncation). `stdout` / `stderr` are tagged separately; `system` is reserved for runner-synthesized lines (`START`, `EXIT code=N`, `SIGNAL <name>`, truncation marker).
- **Agent stdio never enters the app logger** — a chatty agent must not be able to roll harbin's log.
- **Cancellation.** Cancel flips intent; the runner SIGTERMs the process group → waits `kill_grace_seconds` (default 10) → SIGKILLs the group. Queued jobs cancel immediately without a subprocess. Any worktree changes the agent left behind are captured by the standard post-job stash (§4.2) — no special cancel-time logic, the same cleanup runs for every terminal state.
- **No per-job wall-clock timeout in v1.** Operators who need a cap wrap their prompt with `timeout(1)` or equivalent.
- **Exit-code contract.** 0 → `success`; non-zero → `failed`; killed by cancellation → `cancelled` (`exit_code` typically `-15` / `-9` on POSIX). Harbin does not interpret richer exit-code conventions; agents that want them emit a `status.json` artifact.

### 5.3 State store

Single SQLite file (`harbin.db`) via `aiosqlite`. SQLite earns its place: jobs/logs/schedule_state want indexed lookups, the workload is single-writer, the file is operationally trivial (one file, no daemon), and migrations are well-understood. Tables:

```
fleets         (id, name, url, dock_path, registered_at)
tasks          (id, fleet_id, task_id, cron, prompt, source_sha)
jobs           (id, short_id, fleet_id, task_pk_nullable, prompt, source,
                started_at, ended_at, status, exit_code, artifact_dir)
job_log_chunks (job_id, seq, ts, stream, text)   -- bounded ring + on-disk log
schedule_state (task_pk, last_fire_ts)
app_state      (key, value)                       -- schema_version, last_vacuum_at
```

- **One process-wide connection**, owned by a single `Store`, accessed only from the event loop. Single-connection keeps writer serialization trivial; the workload is small (hundreds of writes/hour).
- **Pragmas applied at open:** `journal_mode=WAL`, `synchronous=NORMAL`, `foreign_keys=ON`, `busy_timeout=5000`.
- **Timestamps** are UTC ISO-8601 strings (SQLite has no native timestamp type; TEXT keeps the file inspectable).
- **Migrations** are forward-only, monotonic numbered scripts (`db/migrations/NNN_*.sql`) tracked by `app_state.schema_version`. If a migration is wrong, fix it forward. Harbin **refuses to start** if the DB's `schema_version` is greater than its own known max — the user must upgrade harbin.

### 5.4 Logging — per-fleet, per-task, per-job

Per-job logs are a **first-class feature**: they are the operator's primary audit trail and the main debugging surface for both agent behavior and harbin itself. The artifact tree **is** the log tree — there is no separate logs/ hierarchy:

```
artifacts/<fleet>/<task-id|adhoc>/<job-id>/
  job.log         # append-only, authoritative; every line tagged stdout/stderr/system
  <other agent artifacts…>
```

- **What's captured.** Agent stdout/stderr line-by-line, plus runner-synthesized `system` lines (`START`, `EXIT code=N`, `SIGNAL <name>`, cancel reason, post-job stash hash). Timestamps and stream tags live in `job_log_chunks` (SQLite); `job.log` carries the same lines as plain text for grep/tail.
- **Hierarchy navigation.** The TUI exposes the artifact tree as a primary screen (§6.1) with three drill levels: **fleet → task (or `adhoc`) → job**. Each level shows the children sorted by recency and a glyph for the latest terminal status. The leaf opens a log/artifact viewer (live tail for active jobs, static for ended ones; filter by stream tag; jump-to-error highlights `system` and `stderr` lines).
- **Retention.** Logs follow the artifact retention policy (§4.3) — when a job dir is swept, its log chunks are deleted with it. The `jobs` row stays as a history breadcrumb (`status='archived'`).
- **App logger (harbin's own).** Separate from agent logs. Root logger `harbin`; subsystem children (`harbin.scheduler`, …). Sinks: rotating file (`paths.log_dir/harbin.log`, 5×5 MiB) + in-process ring (last 2000 lines, dumped to stderr on unhandled crash). Redaction scrubs `gh[ps]_[A-Za-z0-9]{36,}` and `Bearer\s+\S+` before lines hit either sink. **Agent stdio never enters the app logger** so a chatty agent cannot roll harbin's own log.
- **Error taxonomy.** `HarbinError` → `UserError` (red console line), `FleetError` (warning row on the fleet — never crashes harbin), `InternalError` (modal + ERROR log). Unhandled exceptions are caught at TaskGroup boundaries and reclassified as `InternalError`.

---

## 6 · Interaction plane

### 6.1 TUI layout

```
┌─ Header (Static) ────────────────────────────────────────────┐
│  figlet logo  ·  command center for AI agents                │
├─ ActiveJobs (Container, border-title="active") ──────────────┤
│  [alt+1..9] ● running   <fleet>·<task-or-adhoc>  #<id>  <elapsed>
│  one row per running/starting/queued job; empty state hint   │
├─ RecentJobs (Container, border-title="recent") ──────────────┤
│  ✓ success    <fleet>·<task-or-adhoc>  #<id>  <ended-at>     │
│  last ~20 terminal jobs; read-only; press enter to open log  │
├─ Console (RichLog, border-title="console") ─ flex:1 ─────────┤
│  Scrolling log of: user input, acks, system events.          │
│  Per-job stdio lives in its alt+N pane, not here.            │
├─ CommandLine (Input, prefix="> ") ───────────────────────────┤
│  @fleet PROMPT  — ad-hoc prompts (only grammar accepted)     │
├─ StatusBar (Footer) ─────────────────────────────────────────┤
│  N active · M recent · K fleets · alt+0=overview  alt+L=logs │
└──────────────────────────────────────────────────────────────┘
```

**Behaviors:**

- **ActiveJobs** and **RecentJobs** are visually and structurally separate. Active is always rendered first and never collapses; recent fills the remaining row budget. Active jobs are sorted by `started_at` ascending (oldest first, so a long runner stays put); recent by `ended_at` descending.
- **alt+1..9** focuses the N-th **active** job — it opens a `JobView` (live stdio + status header). Recent jobs are not Alt-N addressable (their IDs change as jobs finish); open them with `enter` on the focused row, or via the log browser.
- **alt+0** returns to the overview screen.
- **alt+L** (or `enter` on an active/recent row) opens the **LogBrowser** — a three-pane drill: fleet → task → job → log/artifact viewer (§5.4).
- **alt+F** opens the **Fleets** modal (CRUD + add). **alt+,** opens **Settings**. **alt+Q** is the quit shortcut (confirm if jobs are running). **esc** pops the current modal/screen back to overview.
- The CommandLine is **always visible** at the bottom regardless of screen.

### 6.2 CommandLine — `@fleet PROMPT`

The CommandLine accepts a single grammar: `@<fleet> <prompt-text>`. Anything else is an error.

- **Fleet name** matched against `^[a-z][a-z0-9-]{0,62}$`. Unknown fleet → `UserError` with a `difflib` did-you-mean suggestion.
- **Prompt** is **the rest of the line, verbatim** — no shlex, no quote handling, no escapes. Prompts often contain quotes and shell metachars; tokenizing them mangles them.
- Submission enqueues a job with `source=repl`. The Console prints one ack line: `queued #<short-id>  <fleet>·adhoc`.
- Tab-complete `@<TAB>` cycles fleet names; the Textual `Suggester` shows ghost text for the first match.
- Empty input is silently ignored.

No slash commands. All discoverable functionality lives in shortcut keys + menus (§6.1, §6.3); operations like cancel, sync, view-schedule are surfaced on the focused row or in the relevant modal, not as typed commands. This keeps the typed surface tiny and the discoverability honest.

### 6.3 Settings — minimal

Settings open as a `ModalScreen` from `alt+,` or the Fleets modal's gear icon. Four pages, no more — anything that doesn't fit here uses defaults:

```
┌─ settings ─────────────────────────────────────────────┐
│ ▸ Fleets          │  (CRUD; the load-bearing page)     │
│   General         │  Theme, timezone                   │
│   Agent runner    │  agent_cli command, kill grace     │
│   About           │  Version, paths, link to logs      │
│                   │  [ Save ]  [ Cancel ]              │
└────────────────────────────────────────────────────────┘
```

- **Fleets** is the centerpiece: registered fleets in rows, per-row drawer with sync interval, retention, `[ Remove fleet ]`. `+ Add fleet` is a two-step wizard (paste git URL → confirm clone path).
- **General** is just theme and timezone. `log_verbosity` follows a sensible default and isn't surfaced.
- **Agent runner** carries the global `agent_cli.command` (the one essential piece; nothing else) and `kill_grace_seconds`. Concurrency caps follow defaults.
- **About** is read-only — version, the four resolved paths, a link that opens `harbin.log`.

Persistence: `~/.config/harbin/config.yaml`, pydantic-validated on save. A wholly-empty `config.yaml` is valid (every leaf has a default); on first launch harbin writes a fully-defaulted file. Boot-time `config.yaml` errors → harbin refuses to start. Per-fleet errors (`fleet.yaml` / `schedule.yaml`) disable that fleet only and surface a warning row.

**Retention grammar.** `<int>(s|m|h|d)` — e.g. `30d`, `12h`, `365d`. Literal `never` is not supported (deletion-footgun).

### 6.4 Web UI + tunnels (deferred)

Out of scope for v1. The architecture stays single-process and TUI-only. A future enhancement may add a `harbin serve` mode (textual-serve over WebSocket) and an external-tunnel wrapper; both are explicitly deferred so the v1 surface stays narrow.

---

## 7 · Visual style

**Palette: harbor** (locked default; alternatives may ship as themes).

| Token | Hex | Use |
|---|---|---|
| `bg`       | `#0b1620` | Background |
| `fg`       | `#cde2ec` | Default text |
| `accent`   | `#5fb6c8` | Frame borders, prompt glyph, structural highlights |
| `activity` | `#f0a857` | Running / attention-worthy states (warm amber) |
| `muted`    | `#5e7585` | Secondary text, timestamps, IDs |
| `error`    | `#ff7575` | Failures |

**Visual language:**

- **Lowercase labels** everywhere (`monitor`, `console`, `2 jobs`).
- **Monospace** throughout; no proportional fonts.
- **Sharp Frame borders** with title labels (`┌─ monitor ─…─┐`).
- **Generous spacing** — vertical rhythm over density.
- **Status conveyed by glyph + color**: `●` running, `○` queued, `✓` success, `✗` failed, `⚠` warning. Color is reinforcement, not the only channel — accessible on color-poor terminals.
- **Themes are swappable at runtime** via Settings → General → Theme.

---

## 8 · Sample fleets

Both sample fleets live as standalone GitHub repos and are *not* vendored into the harbin source tree. The harbin CLI ships a `harbin sample-fleet add news|price-monitor` helper that clones the corresponding repo into a dock and registers it — a smooth first-run path.

### 8.1 `harbin-agent-sample-news` — daily custom news

**URL:** `https://github.com/dryotta/harbin-agent-sample-news`

**Demonstrates:** cron-driven task · markdown artifact · agent-driven `git push`.

```
.harbin/
  fleet.yaml       # retain: 365d
  schedule.yaml    # 07:00 daily, prompt writes brief-YYYY-MM-DD.md
.github/
  copilot-instructions.md
  agents/news-curator.md
skills/
  source-rules.md  # "prefer official blog over tabloid", etc.
```

**Cadence:** once daily at 07:00 local. **Artifact:** `brief-YYYY-MM-DD.md`. The agent's own prompt commits the brief to `briefs/` and runs `git push` as part of the job (harbin never pushes); any uncommitted leftovers are stashed by harbin post-job (§4.2).

### 8.2 `harbin-agent-sample-price-monitor` — periodic price check

**URL:** `https://github.com/dryotta/harbin-agent-sample-price-monitor`

**Demonstrates:** hourly cron · JSON artifact · in-prompt tool use · alert surfacing.

```
.harbin/
  fleet.yaml       # retain: 30d
  schedule.yaml    # hourly
  watchlist.yaml   # tickers + thresholds (fleet-local config the prompt reads)
.github/
  copilot-instructions.md
  agents/price-checker.md
```

**Cadence:** hourly. **Artifact:** `prices.json` per run (diffs preserved as `prices-<utc>.json`). **Alert path:** the prompt instructs the agent to write `alert.txt` when a threshold is crossed; the corresponding job's recent-row shows `⚠ 1 alert` so the operator sees it on next visit.

### 8.3 What the harbin repo contains

A directory `examples/` in this repo documents each sample with prose explaining what feature surface it exercises, plus links to the live repos. No code duplication — the source of truth for each sample is its own repo.

---

## 9 · Distribution & install

- **Primary install:** `uv tool install harbin`. Mirrors toad's distribution model and gives users a fast, reproducible global install.
- **From source:** `git clone <harbin> && uv sync --extra dev && uv run harbin`.
- **Curl-pipe-sh installer:** a tiny script that delegates to `uv tool install harbin` is **post-v1**.
- **Supported platforms:** Linux and macOS (tier 1, full CI), Windows 11 native (tier 1, Windows Terminal recommended), Windows 10 and WSL2 (tier 2 — best-effort). Snapshot tests are Linux-only in CI (Windows terminal rendering on runners is unreliable; users are unaffected).
- **Path handling:** `pathlib.Path` throughout — never raw string concatenation — so the Windows/POSIX split is invisible above the Dock and Artifact managers.
- **Windows long paths.** Artifact trees can exceed `MAX_PATH` (260 chars). Users who hit this enable the long-path policy once via registry; harbin does **not** prepend `\\?\` itself.
- **Versioning.** `hatch-vcs` derives version from the latest git tag. Pre-1.0: breaking changes bump **minor**; post-1.0 bump **major**. The DB refuses to open if its `schema_version` exceeds the binary's known max.

---

## 10 · Out of scope (v1)

These are explicitly **not** in v1. Each is a candidate for a later phase, but mentioning them here prevents scope creep during implementation.

- **Daemon / background mode.** Foreground only. No `systemd` unit, no `launchd` plist, no service install. (See §3.2.)
- **Web UI / remote access.** No `harbin serve`, no embedded WebSocket transport, no tunnel wrapper. The TUI is the only surface in v1.
- **Harbin-driven `git push`.** Harbin never publishes — it only pulls, runs, and stashes. Agents that need to publish do so within the job's own prompt.
- **Missed-fire catchup.** If harbin was off, the cron firing is lost. Opt-in `catchup` policy is a future candidate.
- **Per-job wall-clock timeout.** Agents run until they exit; operators wrap with `timeout(1)` if needed.
- **Multiple agents per fleet.** A fleet's prompt may *role-play* whichever agent it wants, but harbin does not track agent identities. `@fleet/agent` syntax is not in v1.
- **Non-stdin invocation modes.** The agent CLI receives its prompt on stdin and nothing else in v1. Flag- and tempfile-based modes are deferred until a real use case demands them.
- **Conversation continuity.** Each job is one-shot — there is no resume-this-conversation primitive.
- **Multi-user / hosted harbin.** Harbin is single-tenant by design.
- **Plugin / extension system.** No registration mechanism for third-party commands or screens.
- **Active disk quotas / per-fleet budgets.** Retention is the only space-reclamation mechanism.
- **Per-fleet rename.** Editing `fleet.yaml.name` post-registration is rejected with a clear error.

---

## 11 · Dev workflow

| Step | Command |
|---|---|
| Bootstrap | `uv sync --extra dev` |
| Run from source | `uv run harbin` |
| Lint + format check | `uv run ruff check . && uv run ruff format --check .` |
| Typecheck | `uv run mypy` |
| Test (all lanes) | `uv run pytest` |

CI runs lint + typecheck + unit on every push; integration + snapshot on PRs. All four gates must be green.

**Test lanes** mirror the source layout: `tests/unit/` (no subprocess / no real git, < 5 s), `tests/integration/` (real local git in temp dirs, real SQLite, fake agent CLI, < 60 s), `tests/snapshot/` (Textual snapshot tests, Linux only in CI). A `fake_agent_cli.py` fixture reads its prompt from stdin (matching the v1 invocation contract) and honors `HARBIN_FAKE_EXIT` and `HARBIN_FAKE_DURATION` to drive failure / cancel paths.
