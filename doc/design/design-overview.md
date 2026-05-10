# harbin — Design Doc

> *A minimalist command center for GitHub Copilot AI agents.*
>
> Status: **north-star design** · scope: full product vision at high level · audience: contributors

This is a **north-star** design document — every subsystem is described at the level needed to agree on shape, vocabulary, and interfaces. Implementation depth (concrete schemas, CLIs, error taxonomies) lives in per-subsystem sub-specs that this doc will link to as they are written.

---

## Engineering meta-principles

These bind every PR. They are non-negotiable and appear here, in commit-message templates, in the contributor guide, and in CI lint rules where mechanizable.

1. **Never increase debt.** Each change leaves the codebase in equal or better shape than it found it. Refactors that pay down debt are welcome when scoped to the area being touched; unrelated refactors are not.
2. **Proper fix over patch.** Root-cause over workaround. If a patch ships (e.g. blocking a release), it is labeled `tech-debt` and tracked to a follow-up issue before the patch lands.
3. **Zero regression.** Every bug ships with a test that would have caught it. No exceptions, including "trivial" fixes.
4. **Docs reflect shipped code.** The design doc, sub-specs, and READMEs are part of the change, not an afterthought. A PR that changes shipped behavior without updating the docs is incomplete.

---

## 1 · Vision

Harbin is a single Python process that lets a developer run, schedule, and observe a small fleet of GitHub-Copilot-style coding agents working in repos they control.

Conceptually: harbin is a **harbor**. Each repo it manages is a **fleet** moored in its own **dock**. Harbin watches the docks, fires off agent runs (a **job**) on a schedule or on demand, captures their output as **artifacts**, and surfaces the whole thing through one terminal UI (and the same UI served over the web).

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
| **Artifact** | Files a job produces under `~/.local/share/harbin/artifacts/<fleet>/<task-id-or-adhoc>/<job-id>/`. |
| **REPL** | The command-line input buffer at the bottom of the TUI. Accepts slash commands (`/help`) and at-mentions (`@fleet PROMPT`). |
| **Tunnel** | A Microsoft Dev Tunnel created by `devtunnel host`. Harbin offers a lifecycle wrapper but does not embed the tunnel implementation. |

---

## 3 · Architecture overview

A **single foreground Python process**. No daemon, no IPC, no separate services. The Textual app and all subsystems share one asyncio event loop.

```
┌─────────────────────  one  harbin  process  ──────────────────────┐
│                                                                    │
│  ┌──────────┐   ┌──────────┐   ┌──────────┐   ┌──────────────┐    │
│  │   TUI    │   │   REPL   │   │ Web UI   │   │ Dev Tunnels  │    │
│  │ (Textual)│   │ (slash + │   │ (textual-│   │ (external,   │    │
│  │          │   │  @ men.) │   │  serve)  │   │  optional)   │    │
│  └────┬─────┘   └────┬─────┘   └────┬─────┘   └──────┬───────┘    │
│       │              │              │                 │            │
│       └──────────────┴──────┬───────┘                 │            │
│                             ▼                         │            │
│                    ┌──────────────┐                   │            │
│                    │   AppCore    │◄──────────────────┘            │
│                    │  (asyncio    │                                │
│                    │   event bus) │                                │
│                    └──┬───────┬───┘                                │
│             ┌─────────┘       └─────────┐                          │
│             ▼                           ▼                          │
│   ┌──────────────────┐          ┌──────────────────┐               │
│   │   Dock Manager   │          │    Scheduler     │               │
│   │  (git: clone,    │          │  (cron parser,   │               │
│   │   pull, push,    │          │   in-process,    │               │
│   │   worktrees)     │          │   persisted)     │               │
│   └────────┬─────────┘          └────────┬─────────┘               │
│            ▼                             ▼                          │
│   ┌────────────────────────────────────────────┐                   │
│   │              Agent Runner                  │                   │
│   │  spawns <agent_cli> --prompt … --cwd <dock>│                   │
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
| Web UI | **textual-serve** | Same Textual app served over WebSocket. No second frontend to maintain. |
| Async runtime | **asyncio** (single loop) | Lets git ops, scheduler, agent subprocesses, and UI coexist without threads. |
| State store | **SQLite via `aiosqlite`** | Single file, zero ops, async-friendly. |
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
| `~/.local/share/harbin/artifacts/<fleet>/<task-id-or-adhoc>/<job-id>/` | Per-job artifact directories. |
| `~/.cache/harbin/` | Ephemeral / discardable. |

Exact paths resolved via `platformdirs` so macOS and Windows get their native equivalents.

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
agent_cli:
  command: ["copilot"]              # any binary; passed an env var with the prompt
  # See §5.2 for the full invocation contract.

artifact_policy:
  retain: 30d                       # how long to keep artifacts on disk
  push_back: false                  # if true, commit artifact dir contents back to repo
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

A task is **a saved prompt plus a cron expression**, nothing more. When it fires, the scheduler enqueues a job equivalent to typing `@fleet <prompt>` in the REPL. This is the **one-way-to-do-things** principle: ad-hoc and scheduled invocations share a single code path.

### 4.2 Dock Manager

- **One dock per fleet.** Path: `~/.local/share/harbin/docks/<fleet-name>/`. A regular `git clone`.
- **Sync policy:** background `git fetch` every N minutes (default 5m, configurable). If the working tree is clean, fast-forward `<default_branch>`. If dirty, surface a warning on the fleet's monitor row and skip ff — never overwrite local state.
- **Push:** after a successful job that mutated the worktree *and* `fleet.yaml.artifact_policy.push_back: true`, harbin commits the artifact dir contents with a structured message:
  ```
  harbin: <task-id-or-"adhoc"> @<utc-iso>

  job: <job-id>
  prompt: <first 80 chars>…
  ```
  and pushes to `<default_branch>`. Push failures are surfaced as a warning row; the artifacts remain on disk.
- **Auth:** harbin uses the system `git` binary. Authentication is handled by the user's credential helper of choice (`gh auth`, SSH agent, Git Credential Manager). Harbin never stores tokens.
- **Hot reload:** `watchdog` observes each dock's `.harbin/fleet.yaml` and `.harbin/schedule.yaml`. Edits trigger an in-place reload within ~1 s; running jobs are unaffected.

### 4.3 Artifact Manager

- **Root:** `~/.local/share/harbin/artifacts/`
- **Layout:** `<fleet>/<task-id|adhoc>/<job-id>/`. Each job gets a freshly created, empty dir.
- **Env var:** the agent runner sets `HARBIN_ARTIFACT_DIR` so agents can write idiomatically (`open("$HARBIN_ARTIFACT_DIR/brief.md", "w")` works after env-var expansion).
- **Scope:** agents are *allowed* to write outside the artifact dir (e.g. `/tmp`, user-specified paths). Harbin only tracks what lands inside the artifact dir — outside writes are the agent's business.
- **Retention:** default 30 days per fleet, overridable in `fleet.yaml`. A daily background sweep removes expired dirs and prunes the corresponding `job_log_chunks` rows.
- **Browser:** `/artifacts <fleet>` opens a Textual file-tree panel. Clicking a file in a job's pane jumps to the file in the artifact browser.

---

## 5 · Execution plane

### 5.1 Scheduler

- **In-process asyncio loop.** A single `Scheduler` coroutine wakes every 5 s (configurable) and asks each loaded task "are you due since last fire?"
- **Cron parsing:** `croniter` in local time. Timezone is read from `config.yaml` (default: system local).
- **Missed-fire policy:** silent skip. If harbin was off when a cron should have fired, the firing is dropped. v2 candidate: opt-in `catchup: latest` per task.
- **Hot reload:** schedule changes from `watchdog` are picked up within ~1 s. In-flight jobs are unaffected; future fires reflect the new schedule.
- **Persistence:** `schedule_state(task_pk, last_fire_ts)` so a rapid restart does not double-fire.

### 5.2 Agent Runner

This is the most pluggable subsystem.

- **Concurrency:** one job per dock at a time by default — prevents two agents trampling the same worktree. A global cap also applies (default 4). Per-task `concurrency: parallel` is an opt-in escape hatch.
- **Job lifecycle:** `queued → starting → running → (success | failed | cancelled) → archived`.
- **Spawning:** `asyncio.create_subprocess_exec`. Working directory is the dock. Environment is enriched with:
  ```
  HARBIN_ARTIFACT_DIR=<path>
  HARBIN_FLEET=<name>
  HARBIN_TASK_ID=<id-or-"adhoc">
  HARBIN_JOB_ID=<short-hex>
  HARBIN_PROMPT=<the prompt text>
  ```
- **Invocation contract:** the agent CLI is run as `<agent_cli.command> [extra_args…]`. The prompt is conveyed by **stdin** by default. Alternative modes (flag, tempfile) are configurable for CLIs that don't read stdin. The exact flag shape for the default CLI is pinned in a **sub-spec** (`doc/design/agent-cli-invocation.md`, to be written) — not in this doc, so we don't bake in a single tool.
- **Stdio capture:** stdout/stderr piped to (a) an in-memory ring buffer (last 4 MiB per job, surfaces in the job's alt+N pane and `/logs <job>`) and (b) `<artifact_dir>/job.log` on disk.
- **Cancellation:** `/cancel <job>` → SIGTERM → 10 s grace → SIGKILL. Pre-run worktree state recorded; on cancel, optional `git stash` to preserve user investigation.
- **Pluggability:** global `agent_cli` in `config.yaml`; per-fleet override in `fleet.yaml`. Same shape both places.

### 5.3 State store

Single SQLite file (`harbin.db`) via `aiosqlite`. Tables (high level — concrete schema in a sub-spec):

```
fleets         (id, name, url, dock_path, registered_at)
tasks          (id, fleet_id, task_id, cron, prompt, source_sha)
jobs           (id, fleet_id, task_pk_nullable, prompt, source, started_at,
                ended_at, status, exit_code, artifact_dir)
job_log_chunks (job_id, seq, ts, stream, text)         -- bounded ring + on-disk log
schedule_state (task_pk, last_fire_ts)
app_state      (key, value)                            -- schema_version, etc.
```

Migrations are monotonic numbered scripts run on startup. A single `schema_version` row tracks where the DB is. There is no rollback — harbin's policy is *forward-only*; if a migration is wrong, fix it forward.

---

## 6 · Interaction plane

### 6.1 TUI layout

```
┌─ Header (Static) ────────────────────────────────────────────┐
│  figlet logo  ·  command center for AI agents                │
├─ JobMonitor (Container, border-title="monitor") ─────────────┤
│  RowList of JobRow widgets, one per active+recent job        │
│  [alt+N] ● running   <fleet>·<task-or-adhoc>   #<job-id>   <elapsed> │
│  (empty state hints `@fleet …` or `/help`)                   │
├─ Console (RichLog, border-title="console") ─ flex:1 ─────────┤
│  Scrolling log of: user REPL input, brief acks, system       │
│  events. Per-job stdio lives in its alt+N pane, not here.    │
├─ CommandLine (Input, prefix="> ") ───────────────────────────┤
│  Slash-command + @fleet completion via Textual Suggester     │
├─ StatusBar (Footer) ─────────────────────────────────────────┤
│  N jobs · M fleets · active: <name> · alt+0 = overview       │
└──────────────────────────────────────────────────────────────┘
```

**Behaviors:**

- `alt+0` returns to the overview screen (above).
- `alt+1..9` focuses **job N** — replaces JobMonitor+Console with a single `JobView` containing the job's live stdio (RichLog) and a header (fleet, prompt, status, exit).
- `esc` from a JobView returns to overview.
- The CommandLine is **always visible** at the bottom regardless of screen.
- `/exit` mutates the prompt into a confirm-line: `quit? N jobs running [y/N]`. `y` quits (and SIGTERMs running jobs); anything else cancels.

### 6.2 REPL — slash commands and `@fleet`

The CommandLine parses on first character:

| Starts with | Parsed as |
|---|---|
| `/` | slash command |
| `@` | at-mention — ad-hoc prompt to a fleet |
| anything else | error: *"start with `/` or `@`"* |

**At-mention:** `@<fleet> <prompt-text>` enqueues a job with `source=repl`. Tab-complete `@<TAB>` to a fleet picker.

**Slash commands (v1):**

```
/help [cmd]              Lookup
/jobs [--all]            List jobs (running + recent)
/logs <job-id> [-f]      Tail a job's captured stdio
/cancel <job-id>         Abort a running job
/artifacts <fleet> […]   Browse artifact dirs
/sync [fleet]            Force git fetch + ff
/schedule [fleet]        Inspect cron + next-fire times
/tunnel [start|stop|status]   Wrap `devtunnel host`
/config                  Open the multi-page settings screen
/exit                    Confirm + quit
```

Unknown slash commands surface a single-line "did you mean …?" via `difflib`. No hidden state.

### 6.3 Configuration UX

`/config` opens a `ModalScreen` with a sidebar of pages and a content pane:

```
┌─ /config ──────────────────────────────────────────────┐
│ ▸ General         │  Theme            [harbor ▼]       │
│   Fleets          │  Timezone         [system]         │
│   Scheduler       │  Log verbosity    [info]           │
│   Agent runner    │                                    │
│   Artifacts       │                                    │
│   Web UI          │                                    │
│   Dev Tunnels     │                                    │
│   Keybindings     │                                    │
│   About           │  [ Save ]  [ Cancel ]              │
└────────────────────────────────────────────────────────┘
```

**Pages:**

- **General** — theme, timezone, log verbosity.
- **Fleets** — CRUD for registered fleets. Per-row drawer: sync interval override, push policy, artifact retention, **Remove fleet**. `+ Add fleet` is a two-step wizard: paste git URL → confirm clone path → done. Fleet management is **menu-only**, intentionally absent from the slash-command surface.
- **Scheduler** — tick rate, missed-fire policy (currently `skip` only).
- **Agent runner** — global `agent_cli` (default for all fleets), default per-dock concurrency, global cap, kill grace period.
- **Artifacts** — root path, default retention, sweep schedule.
- **Web UI** — port, host, auto-start on launch.
- **Dev Tunnels** — `devtunnel` binary path, named tunnel ID, anonymous-allowed (default no).
- **Keybindings** — view + edit overrides.
- **About** — version, paths, link to logs.

Persistence: `~/.config/harbin/config.yaml`, pydantic-validated on save.

### 6.4 Web UI + Dev Tunnels

- **Web UI.** `harbin serve [--port 8080] [--host 127.0.0.1]` runs the *same* Textual app via `textual-serve`. No separate HTML/CSS to maintain; visual style follows the chosen palette automatically.
- **Local auth.** None by default. With `--host 0.0.0.0` the user is recommended (in docs and at startup) to front it with a tunnel rather than expose the port directly.
- **Dev Tunnels wrapper.** `/tunnel start` runs the equivalent of `devtunnel host -p <serve-port> --allow-anonymous false`. Before starting, harbin checks `devtunnel user show`. If the user is not logged in, harbin prints the exact `devtunnel user login -g` command (GitHub OAuth) and exits the wrapper — it does **not** drive an interactive auth flow itself. The tunnel runs as a separate subprocess; its lifetime is **not** coupled to harbin's. `/tunnel status` reports the public URL; `/tunnel stop` terminates the subprocess.
- **Install guide.** A standalone document at `doc/remote-access.md` walks the user through installing `devtunnel` on Windows / macOS / Linux and performing the one-time GitHub auth. The guide is referenced from `/tunnel`'s help text.

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
- **Themes are swappable at runtime** via `/config → General → Theme`.

---

## 8 · Sample fleets

Both sample fleets live as standalone GitHub repos and are *not* vendored into the harbin source tree. The harbin CLI ships a `harbin sample-fleet add news|price-monitor` helper that clones the corresponding repo into a dock and registers it — a smooth first-run path.

### 8.1 `harbin-agent-sample-news` — daily custom news

**URL:** `https://github.com/dryotta/harbin-agent-sample-news`

**Demonstrates:** cron-driven task · markdown artifact · push-back-to-repo.

```
.harbin/
  fleet.yaml       # push_back: true; retain: 365d
  schedule.yaml    # 07:00 daily, prompt writes brief-YYYY-MM-DD.md
.github/
  copilot-instructions.md
  agents/news-curator.md
skills/
  source-rules.md  # "prefer official blog over tabloid", etc.
```

**Cadence:** once daily at 07:00 local. **Artifact:** `brief-YYYY-MM-DD.md`. **Push-back:** commits to `briefs/` so the repo accumulates an archive over time.

### 8.2 `harbin-agent-sample-price-monitor` — periodic price check

**URL:** `https://github.com/dryotta/harbin-agent-sample-price-monitor`

**Demonstrates:** hourly cron · JSON artifact · in-prompt tool use · alert surfacing.

```
.harbin/
  fleet.yaml       # push_back: false; retain: 30d
  schedule.yaml    # hourly
  watchlist.yaml   # tickers + thresholds (fleet-local config the prompt reads)
.github/
  copilot-instructions.md
  agents/price-checker.md
```

**Cadence:** hourly. **Artifact:** `prices.json` per run (diffs preserved as `prices-<utc>.json`). **Alert path:** the prompt instructs the agent to write `alert.txt` when a threshold is crossed; the corresponding job's monitor row shows `⚠ 1 alert` so the operator sees it on next visit.

### 8.3 What the harbin repo contains

A directory `examples/` in this repo documents each sample with prose explaining what feature surface it exercises, plus links to the live repos. No code duplication — the source of truth for each sample is its own repo.

---

## 9 · Distribution & install

- **Primary install:** `uv tool install harbin`. Mirrors toad's distribution model and gives users a fast, reproducible global install.
- **From source:** `git clone <harbin> && uv sync && uv run harbin`.
- **Curl-pipe-sh installer:** a tiny script that delegates to `uv tool install harbin` is **post-v1**. Not a blocker.
- **Supported platforms:** macOS, Linux, and **Windows natively**. Windows Terminal is strongly recommended for full glyph and color fidelity; legacy `conhost.exe` works with degraded rendering (same caveat every Textual app has). WSL2 is supported but not required.
- **Path handling:** `pathlib.Path` throughout — never raw string concatenation — so the Windows/POSIX split is invisible above the Dock and Artifact managers.

---

## 10 · Out of scope (v1)

These are explicitly **not** in v1. Each is a candidate for a later phase, but mentioning them here prevents scope creep during implementation.

- **Daemon / background mode.** Foreground only. No `systemd` unit, no `launchd` plist, no service install. (See §3.2.)
- **Missed-fire catchup.** If harbin was off, the cron firing is lost. Opt-in `catchup` policy is a v2 candidate.
- **Multiple agents per fleet.** A fleet's prompt may *role-play* whichever agent it wants, but harbin does not track agent identities. `@fleet/agent` syntax is **not** in v1.
- **PTY / literal shell panes.** The monitor pane shows agent jobs only. No `/new powershell` or terminal multiplexing.
- **Conversation continuity.** Each job is one-shot — there is no resume-this-conversation primitive.
- **Multi-user / hosted harbin.** Harbin is single-tenant by design.
- **In-app `devtunnel` install.** The user installs `devtunnel` themselves; harbin only wraps the running binary.
