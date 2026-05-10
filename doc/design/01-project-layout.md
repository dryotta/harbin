# project-layout — Sub-spec

> Status: **draft** · scope: source tree, packaging metadata, entry points, dev workflow, on-disk path resolution.
> Parent: [`design-overview.md`](./design-overview.md).

This doc nails down where files live — both in the repo and on the user's machine — so every other sub-spec can refer to a stable address. It is **not** about install distribution (that's [`06-packaging-and-install`](./06-packaging-and-install.md)); it is about laying out the source tree and the runtime filesystem.

---

## 1 · Source tree

```
harbin/
├── pyproject.toml
├── uv.lock
├── README.md
├── LICENSE
├── .gitignore
├── .github/
│   └── workflows/
│       └── ci.yml
├── doc/
│   ├── design/                  # this folder — overview + sub-specs
│   └── remote-access.md         # user guide for devtunnel install (overview §6.4)
├── examples/                    # prose docs for sample fleets (overview §8.3)
├── scripts/                     # dev helpers (e.g. seed_db.py); never imported by package
├── src/
│   └── harbin/
│       ├── __init__.py          # exports __version__ only
│       ├── __main__.py          # `python -m harbin` → cli.main()
│       ├── cli.py               # argparse dispatch: tui (default), serve, sample-fleet
│       ├── app.py               # AppCore: owns event loop, wires subsystems
│       ├── paths.py             # platformdirs resolution + atomic_write
│       ├── logging.py           # app logger setup (see concurrency-and-errors)
│       ├── errors.py            # exception hierarchy (see concurrency-and-errors)
│       ├── config/
│       │   ├── models.py        # pydantic schemas for config.yaml
│       │   ├── loader.py        # YAML → pydantic with friendly errors
│       │   └── watch.py         # watchdog observer + diff
│       ├── db/
│       │   ├── store.py         # aiosqlite connection + queries
│       │   ├── migrate.py       # numbered-script migration runner
│       │   └── migrations/      # NNN_description.sql
│       ├── fleet/
│       │   ├── models.py        # fleet.yaml + schedule.yaml schemas
│       │   ├── dock.py          # DockManager (git + watchdog)
│       │   └── artifacts.py     # ArtifactManager
│       ├── scheduler.py         # Scheduler coroutine
│       ├── runner/
│       │   ├── runner.py        # AgentRunner, job state machine
│       │   └── invocation.py    # agent-cli invocation modes
│       ├── tui/
│       │   ├── app.py           # Textual App subclass
│       │   ├── theme.py         # palette tokens (overview §7) → CSS variables
│       │   ├── screens/
│       │   │   ├── overview.py
│       │   │   ├── job_view.py
│       │   │   └── config_modal.py
│       │   └── widgets/
│       │       ├── job_monitor.py
│       │       ├── job_row.py
│       │       ├── console.py
│       │       ├── command_line.py
│       │       └── status_bar.py
│       ├── repl/
│       │   ├── parser.py        # first-char dispatch
│       │   ├── suggester.py     # Textual Suggester impl
│       │   └── commands/        # one module per slash command
│       ├── web/
│       │   ├── serve.py         # textual-serve binding
│       │   └── tunnels.py       # devtunnel subprocess wrapper
│       └── samples.py           # `harbin sample-fleet add` helper
└── tests/
    ├── conftest.py
    ├── fixtures/
    │   └── fake_agent_cli.py    # see testing-strategy
    ├── unit/
    ├── integration/
    └── snapshot/                # textual-snapshot tests
```

**Conventions:**

- `src/`-layout (not flat). Forces installed-package imports during tests, catching missing-package-data bugs early.
- One concern per module; subsystem-level state hangs off `AppCore` and is passed by reference, never imported as module-level globals.
- `scripts/` is **never imported** by package code; it's a dev-only sandbox excluded from packaging.
- Test files mirror the package layout (`tests/unit/test_<module>.py`).

---

## 2 · `pyproject.toml`

Single source of truth for runtime metadata, dependencies, dev tooling, and entry points. Managed by `uv`.

```toml
[project]
name = "harbin"
description = "A minimalist command center for GitHub Copilot AI agents."
readme = "README.md"
license = { file = "LICENSE" }
requires-python = ">=3.14"
dynamic = ["version"]
dependencies = [
  "textual>=0.80",
  "textual-serve>=1.0",
  "aiosqlite>=0.20",
  "httpx>=0.27",
  "pydantic>=2.7",
  "watchdog>=4",
  "croniter>=2",
  "platformdirs>=4",
  "pyyaml>=6",
]

[project.scripts]
harbin = "harbin.cli:main"

[project.optional-dependencies]
dev = [
  "pytest>=8",
  "pytest-asyncio>=0.23",
  "pytest-textual-snapshot>=1",
  "ruff>=0.6",
  "mypy>=1.10",
]

[build-system]
requires = ["hatchling", "hatch-vcs"]
build-backend = "hatchling.build"

[tool.hatch.version]
source = "vcs"                       # version from git tag

[tool.ruff]
line-length = 100
target-version = "py314"
extend-exclude = ["scripts"]

[tool.ruff.lint]
select = ["E", "F", "W", "I", "B", "UP", "ASYNC", "RUF"]

[tool.mypy]
python_version = "3.14"
strict = true
files = ["src/harbin"]

[tool.pytest.ini_options]
asyncio_mode = "auto"
testpaths = ["tests"]
```

**Notes:**

- `requires-python = ">=3.14"` — pinned per overview §3.1. This is the floor, not a ceiling.
- Versions are **lower bounds only**; `uv.lock` is the reproducible pin set and is committed.
- `hatch-vcs` derives the package version from the latest git tag, so `harbin --version` and `pip show harbin` stay consistent without manual bumps.
- `ruff` does both lint and format (no separate `black`).
- `mypy --strict` on the package; tests are *not* strict-typed.

---

## 3 · Entry points

A **single** console script, `harbin`, dispatches to subcommands:

| Invocation | Behavior |
|---|---|
| `harbin` *(no args)* | Launch the Textual TUI on the local terminal. Default subcommand. |
| `harbin serve [--port P] [--host H]` | Start the textual-serve web UI. See [`14-web-ui-and-tunnels`](./14-web-ui-and-tunnels.md). |
| `harbin sample-fleet add <name>` | Clone a sample fleet and register it. See [`06-packaging-and-install`](./06-packaging-and-install.md). |
| `harbin --version` | Print version (from `hatch-vcs`) and exit 0. |
| `harbin --help` / `harbin <cmd> --help` | Argparse-generated help. |

`python -m harbin` is also supported and dispatches identically via `__main__.py`.

Subcommands are intentionally few. New top-level subcommands require a sub-spec amendment; everything else lives behind the in-app REPL ([`13-repl-and-commands`](./13-repl-and-commands.md)).

---

## 4 · Runtime paths

All on-disk locations are resolved through one module, `harbin.paths`, which wraps `platformdirs.PlatformDirs("harbin", "harbin", roaming=False)`. Subsystems **must not** hard-code paths; they ask `paths` for what they need.

### 4.1 Logical paths

| Logical name | Used for | Created by |
|---|---|---|
| `config_dir` | `config.yaml` | `paths.ensure_config_dir()` on first launch |
| `data_dir` | `harbin.db`, `docks/`, `artifacts/`, `logs/` | `paths.ensure_data_dir()` on startup |
| `cache_dir` | Ephemeral (suggestion caches, etc.) | lazy |
| `dock_root` | `data_dir / "docks"` | DockManager on registration |
| `artifact_root` | `data_dir / "artifacts"` | ArtifactManager on first job |
| `log_dir` | `data_dir / "logs"` | logging setup |
| `db_path` | `data_dir / "harbin.db"` | db.store on startup |

### 4.2 OS resolution (informative)

`platformdirs` does the work; below is what users actually see, so error messages and docs can be concrete.

| OS | `config_dir` | `data_dir` | `cache_dir` |
|---|---|---|---|
| Linux | `~/.config/harbin/` | `~/.local/share/harbin/` | `~/.cache/harbin/` |
| macOS | `~/Library/Application Support/harbin/` | `~/Library/Application Support/harbin/` | `~/Library/Caches/harbin/` |
| Windows | `%APPDATA%\harbin\harbin\` | `%LOCALAPPDATA%\harbin\harbin\` | `%LOCALAPPDATA%\harbin\harbin\Cache\` |

On macOS, config and data resolve to the same directory by `platformdirs` convention — that is *expected*; subdirectories (`config.yaml` vs. `docks/`) keep the namespaces separate.

### 4.3 Overrides

- `HARBIN_HOME=<path>` overrides **all four** roots to `<path>/config`, `<path>/data`, `<path>/cache`, `<path>/logs`. Used by tests and by users who want a portable install. When set, `platformdirs` is bypassed.
- Individual roots are not overridable; all-or-nothing keeps the model simple.

### 4.4 `pathlib` discipline

- Every path in harbin is a `pathlib.Path`. Raw strings are accepted at module boundaries (CLI args, YAML strings) and immediately converted.
- `str(path)` is only used at the very last hop into a subprocess or a SQL parameter.
- Forward slashes in YAML are fine on Windows; `Path` normalizes them.
- Long-path support on Windows: harbin does not enable the `\\?\` prefix itself; if a fleet's artifact tree exceeds `MAX_PATH`, the user enables long paths via Windows policy. Documented in [`06-packaging-and-install`](./06-packaging-and-install.md).

### 4.5 Atomic-write helper

Every config and state file harbin writes goes through `harbin.paths.atomic_write_text(path, content)`:

```python
def atomic_write_text(path: Path, content: str, *, encoding: str = "utf-8") -> None:
    """Write `content` to `path` atomically. Crash-safe on POSIX and Windows."""
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(content, encoding=encoding)
    tmp.replace(path)            # atomic on POSIX; best-effort atomic on Windows ≥ Vista
```

Binary variant `atomic_write_bytes` mirrors it. The replace is what gives atomicity; never write directly to the destination.

SQLite handles its own atomicity (WAL + fsync). Job log files are append-only and **not** routed through this helper — they have their own crash-safety story in [`10-agent-runner`](./10-agent-runner.md).

---

## 5 · Dev workflow

| Step | Command |
|---|---|
| One-time bootstrap | `uv sync --extra dev` |
| Run harbin from source | `uv run harbin` |
| Run with a clean state dir | `HARBIN_HOME=$(mktemp -d) uv run harbin` |
| Lint | `uv run ruff check .` |
| Format | `uv run ruff format .` |
| Typecheck | `uv run mypy` |
| Test (all lanes) | `uv run pytest` |
| Test (unit only, fast) | `uv run pytest tests/unit` |
| Update snapshot baselines | `uv run pytest --snapshot-update` |
| Build a wheel | `uv build` |

CI runs lint + typecheck + the unit lane on every push, and integration + snapshot on PRs. Concrete CI shape lives in [`05-testing-strategy`](./05-testing-strategy.md).

---

## 6 · Open questions

- **Python 3.14 floor.** Overview §3.1 names 3.14; this doc adopts it. Revisit only if a critical dep lags.
- **uv.lock checked in?** Yes — required for reproducible CI and `uv tool install` from a tagged commit. Confirmed here unless reversed in [`06-packaging-and-install`](./06-packaging-and-install.md).

## 7 · Out of scope

- Distribution / install mechanics → [`06-packaging-and-install`](./06-packaging-and-install.md).
- DB file layout → [`02-state-store`](./02-state-store.md).
- Config schema details → [`03-configuration`](./03-configuration.md).
- App logger setup → [`04-concurrency-and-errors`](./04-concurrency-and-errors.md).
