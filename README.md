# harbin

> A minimalist command center for GitHub Copilot AI agents.

`harbin` is a single Python process that lets a developer run, schedule,
and observe a small fleet of GitHub-Copilot-style coding agents working
in repos they control.

Conceptually: harbin is a **harbor**. Each repo it manages is a **fleet**
moored in its own **dock**. Harbin watches the docks, fires off agent
runs (a **job**) on a schedule or on demand, captures their output as
**artifacts**, and surfaces the whole thing through one terminal UI
(and the same UI served over the web).

```
┌─ harbin ─────────────────────────────────────────────────────────────┐
│  monitor                                                             │
│    [alt+1] ● running   harbin-agent-sample-news · morning-brief      │
│            #a1b2c3   00:12                                           │
│    [alt+2] ✓ success   harbin-agent-sample-prices · hourly-prices    │
│            #d4e5f6   00:08                                           │
│  console                                                             │
│    welcome to harbin · type /help to begin                           │
│  > @harbin-agent-sample-news draft a brief on robotics this week     │
│  2 jobs · 2 fleets · active: overview · alt+0 = overview             │
└──────────────────────────────────────────────────────────────────────┘
```

---

## Install

```bash
uv tool install harbin
harbin
```

From source:

```bash
git clone https://github.com/dryotta/harbin
cd harbin
uv sync --extra dev
uv run harbin
```

Requirements: Python 3.14+, `git` on PATH. Optional: `devtunnel` (for
remote access — see [`doc/remote-access.md`](doc/remote-access.md)).

---

## Quickstart

```bash
# add a sample fleet
harbin sample-fleet add news

# launch the TUI
harbin

# in the TUI:
> /help                                # browse commands
> /config                              # open the settings modal
> @harbin-agent-sample-news hi         # ad-hoc agent run
> /jobs                                # see what's running
> /logs <short-id> -f                  # tail a job
> /cancel <short-id>                   # stop a job
> /exit                                # confirm + quit
```

Serve the same UI in a browser:

```bash
harbin serve --port 8080
```

For public exposure, prefer a Microsoft Dev Tunnel rather than binding
directly to a public interface. See [`doc/remote-access.md`](doc/remote-access.md).

---

## Concepts

| Term | Meaning |
|---|---|
| **Fleet** | A GitHub repo with a `.harbin/` directory (identity + optional cron). |
| **Dock** | The local working tree harbin keeps for one fleet. |
| **Agent CLI** | The external binary harbin invokes to do agent work (`copilot`, `gh copilot`, …). Pluggable. |
| **Job** | One execution of `(fleet, prompt)`. Has status, stdio capture, an artifact directory. |
| **Task** | A recurring saved prompt declared in a fleet's `schedule.yaml`. |
| **Artifact** | Files a job produces under `~/.local/share/harbin/artifacts/…`. |

See [`doc/design/design-overview.md`](doc/design/design-overview.md) for
the full vocabulary and architecture.

---

## Configure

User-global config lives at `~/.config/harbin/config.yaml` (XDG-style
location resolved per platform). Per-fleet config lives in each fleet's
own `.harbin/fleet.yaml` + optional `.harbin/schedule.yaml`. Edits are
hot-reloaded.

See [`doc/design/03-configuration.md`](doc/design/03-configuration.md)
for the full schema.

---

## Develop

```bash
uv sync --extra dev
uv run pytest                          # all tests
uv run pytest tests/unit               # unit lane
uv run ruff check . && uv run ruff format --check .
uv run mypy
```

Project layout, dev commands, runtime paths: see
[`doc/design/01-project-layout.md`](doc/design/01-project-layout.md).

---

## Platforms

Tier 1: Linux, macOS, Windows 11. Tier 2: Windows 10, WSL2.

Windows users on long artifact trees may need to enable the long-path
policy once — see [`doc/design/06-packaging-and-install.md`](doc/design/06-packaging-and-install.md) §4.1.

---

## License

MIT. See [`LICENSE`](LICENSE).
