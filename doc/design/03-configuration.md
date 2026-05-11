# configuration — Sub-spec

> Status: **draft** · scope: schemas for `config.yaml`, `fleet.yaml`, `schedule.yaml`, validation, hot reload.
> Parent: [`design-overview.md`](./design-overview.md).

Three YAML files. Each maps 1:1 to a pydantic model so loading is `Model.model_validate(yaml.safe_load(path.read_text()))`. This doc pins every field, default, and the hot-reload behavior. The models themselves live in `harbin.config.models` and `harbin.fleet.models`.

---

## 1 · `config.yaml`

**Location:** `paths.config_dir / "config.yaml"`. Single user-global file.

### 1.1 Shape

```yaml
ui:
  theme: harbor            # str, must match a theme registered in tui/theme.py
  log_verbosity: info      # debug | info | warning | error

timezone: system           # IANA zone name (e.g. "Europe/Berlin") or literal "system"

scheduler:
  tick_seconds: 5          # 1..60

agent_runner:
  agent_cli:               # default for every fleet; per-fleet override in fleet.yaml
    command: ["copilot"]
    mode: stdin            # stdin | flag | tempfile
    placeholder: "${PROMPT}"   # only meaningful when mode != stdin
  concurrency:
    per_dock: 1            # 1..4   default-serial; overridable per task
    global_cap: 4          # 1..16
  kill_grace_seconds: 10   # SIGTERM→SIGKILL grace

artifacts:
  root: null               # null = paths.artifact_root; absolute path allowed
  retention: 30d           # see §1.3
  sweep_cron: "0 4 * * *"  # daily 04:00 local

web:
  port: 8080
  host: "127.0.0.1"
  autostart: false         # also start textual-serve when launching the TUI

tunnels:
  devtunnel_path: "devtunnel"
  tunnel_id: null          # named tunnel id; null = anonymous one-off
  allow_anonymous: false

keybindings: {}            # action -> chord overrides; empty by default
```

### 1.2 Pydantic sketch

```python
class AgentCli(BaseModel):
    command: list[str] = Field(default_factory=lambda: ["copilot"], min_length=1)
    mode: Literal["stdin", "flag", "tempfile"] = "stdin"
    placeholder: str = "${PROMPT}"

class Concurrency(BaseModel):
    per_dock: int = Field(default=1, ge=1, le=4)
    global_cap: int = Field(default=4, ge=1, le=16)

class Config(BaseModel):
    ui: UISettings = UISettings()
    timezone: str = "system"
    scheduler: SchedulerSettings = SchedulerSettings()
    agent_runner: AgentRunnerSettings = AgentRunnerSettings()
    artifacts: ArtifactSettings = ArtifactSettings()
    web: WebSettings = WebSettings()
    tunnels: TunnelSettings = TunnelSettings()
    keybindings: dict[str, str] = Field(default_factory=dict)
```

Every leaf field has a default; a wholly-empty `config.yaml` is valid.

### 1.3 Retention grammar

`<int>(s|m|h|d)`. Examples: `30d`, `12h`, `365d`. Parsed by a tiny validator into `datetime.timedelta`. Literal `never` is **not** supported in v1 (avoids a deletion footgun). The same grammar is reused by `fleet.yaml.artifact_policy.retain`.

### 1.4 Env-var overrides

A small allow-list, applied **after** YAML load and re-validated against the schema:

| Env var | Field |
|---|---|
| `HARBIN_UI_THEME` | `ui.theme` |
| `HARBIN_TIMEZONE` | `timezone` |
| `HARBIN_WEB_PORT` | `web.port` |
| `HARBIN_WEB_HOST` | `web.host` |
| `HARBIN_LOG_VERBOSITY` | `ui.log_verbosity` |

`HARBIN_HOME` is handled separately by `paths` (overview §3.3) and does not flow through config.

### 1.5 First-launch behavior

If `config.yaml` is missing on startup, harbin writes a fully-defaulted file via `paths.atomic_write_text` and logs `wrote default config`. Subsequent edits respect the user's hand-formatting (round-tripping via `ruamel.yaml` is **not** done — the file is overwritten only when `/config → Save` is used).

---

## 2 · `fleet.yaml`

**Location:** `<dock>/.harbin/fleet.yaml`. Required for a directory to qualify as a fleet (overview §4.1).

```yaml
name: my-fleet           # pattern: ^[a-z][a-z0-9-]{0,62}$; unique across the install
default_branch: main

agent_cli: null          # null = inherit global; or same shape as config.agent_runner.agent_cli

artifact_policy:
  retain: 30d            # same grammar as config.artifacts.retention
  push_back: false       # if true, commit artifact-dir contents back to default_branch

sync_interval: 5m        # null = inherit global default
```

`name` clashes are rejected at registration time; renaming a fleet is a v2 concern (rename on disk + DB row + dock dir).

---

## 3 · `schedule.yaml`

**Location:** `<dock>/.harbin/schedule.yaml`. Optional — a fleet without it is on-demand only.

```yaml
tasks:
  - id: morning-brief          # pattern: ^[a-z][a-z0-9-]{1,40}$; unique per fleet
    cron: "0 7 * * *"          # croniter syntax, local tz
    prompt: |
      …
    concurrency: serial        # serial (default) | parallel
```

Cron expressions are validated by `croniter` at load. An invalid expression makes the **whole file** a fatal load error for that fleet (the fleet is marked disabled — see §4 — and other fleets are unaffected).

---

## 4 · Validation errors

- `pydantic.ValidationError` and YAML parse errors are caught at load.
- Each `error["loc"]` is mapped to a `(line, col)` by re-parsing with `yaml.compose` and walking the node tree to find the matching scalar/mapping.
- Output format (TUI Console widget + app log):
  ```
  config.yaml:12:5  ui.theme: 'midnight' is not a registered theme
  config.yaml:24:3  scheduler.tick_seconds: input is less than 1 (got 0)
  ```
- **Boot-time `config.yaml` errors:** harbin refuses to start; errors print to stderr.
- **Per-fleet errors** (`fleet.yaml` or `schedule.yaml`): the fleet is marked **disabled** in DB, a warning row appears in the JobMonitor, and other fleets continue to operate. The user fixes the file; the watchdog reload picks it up (§5).
- **Env-var override errors:** treated as boot-time `config.yaml` errors — harbin refuses to start.

---

## 5 · Hot reload

### 5.1 Watched paths

- `paths.config_dir / "config.yaml"`
- For each registered dock: `<dock>/.harbin/fleet.yaml` and `<dock>/.harbin/schedule.yaml`

Provider: `watchdog` observers, one per directory, debounced by 250 ms (handles editors that save in multiple steps).

### 5.2 Diff rules

On any change:
1. Reparse → validate. On validation failure, surface error per §4 and **do not** apply.
2. Deep-compare validated dicts against the in-memory state.
3. If identical → emit nothing (no log line, no UI flicker). A "save with no actual change" is silent.
4. Otherwise, apply the diff; each subsystem owns the relevant fields and reacts.

### 5.3 Apply-live vs. restart-required

| Surface | Apply live? |
|---|---|
| `ui.theme`, `ui.log_verbosity` | Live |
| `timezone` | Live (re-anchors scheduler) |
| `scheduler.tick_seconds` | Live |
| `agent_runner.*` (incl. `agent_cli`) | Live for **new** jobs; running jobs keep their captured config |
| `artifacts.retention`, `sweep_cron` | Live |
| `artifacts.root` | **Restart** (moving artifacts is out of scope) |
| `web.port`, `web.host` | Live but only rebinds on next `harbin serve` start |
| `tunnels.*` | Live |
| `keybindings` | Live |
| All of `fleet.yaml` and `schedule.yaml` | Live (see scheduler hot-reload diff) |

Changes that require restart trigger a warning row in the JobMonitor: `config changed; restart harbin to apply: <field>`.

---

## 6 · Open questions

- Whether `keybindings:` accepts symbolic action names (`focus-monitor`) or only chord-to-chord overrides. Settled in [`12-tui-architecture`](./12-tui-architecture.md).
- Per-fleet env-var overrides — deferred. Today, env vars only customize the global config.

## 7 · Out of scope

- Config encryption — nothing sensitive is stored.
- Multi-profile configs (`--profile=work`). One config per `HARBIN_HOME`.
- Migration of config schema across harbin versions — deferred; pre-1.0 changes are documented in the release notes.
