# packaging-and-install — Sub-spec

> Status: **draft** · scope: end-user install, upgrade, supported platforms, first-run UX, `harbin sample-fleet add`.
> Parent: [`design-overview.md`](./design-overview.md).

This doc owns the end-user experience from `uv tool install harbin` through "I see a useful UI". Internal source layout and dev workflow live in [`01-project-layout`](./01-project-layout.md).

---

## 1 · Primary install

```bash
uv tool install harbin
```

Then `harbin` is on PATH. Upgrades:

```bash
uv tool upgrade harbin
```

Rationale (overview §9): matches toad's distribution model; fast and reproducible; isolates harbin's deps from system Python.

The published artifact is a sdist + wheel on PyPI, built by `hatchling` with the version derived from the latest git tag (`hatch-vcs`).

---

## 2 · From source

```bash
git clone https://github.com/dryotta/harbin
cd harbin
uv sync --extra dev
uv run harbin
```

Suitable for contributors and for users on platforms not yet on PyPI for whatever reason.

---

## 3 · Version policy

- **semver.** `harbin --version` prints the version derived by `hatch-vcs`.
- **Pre-1.0:** breaking changes (config schema, DB schema, command grammar) bump the **minor** version and are listed in release notes.
- **Post-1.0:** breaking changes bump **major**; minors are additive.
- **DB migrations** are forward-only ([`02-state-store`](./02-state-store.md)). A harbin binary refuses to open a DB whose `schema_version` is greater than its own known max, with a message asking the user to upgrade.

---

## 4 · Supported platforms

| OS | Tier | Notes |
|---|---|---|
| Linux | 1 | Tested on Ubuntu 24.04 LTS in CI. |
| macOS | 1 | Tested on macOS 14 in CI. |
| Windows 11 | 1 | Native. Windows Terminal strongly recommended for glyph fidelity. PowerShell or cmd both work. |
| Windows 10 | 2 | Works; signal-handling caveats — see [`04-concurrency-and-errors`](./04-concurrency-and-errors.md) §4. |
| WSL2 | 2 | Works as Linux. |

**Tier 1** = the full CI matrix runs against it. **Tier 2** = supported on best-effort; bugs accepted but not gating.

### 4.1 Windows long paths

Artifact directory trees can exceed `MAX_PATH` (260 chars) on Windows for fleets that generate many nested files. Users who hit this enable the long-path policy once:

```
reg add HKLM\SYSTEM\CurrentControlSet\Control\FileSystem ^
        /v LongPathsEnabled /t REG_DWORD /d 1 /f
```

Documented in the README. Harbin does **not** prepend the `\\?\` extended-path prefix itself.

### 4.2 Terminal expectations

- 256-color minimum; truecolor recommended (so the `harbor` palette renders accurately).
- UTF-8 capable (status glyphs `● ○ ✓ ✗ ⚠`).
- ≥ 80 cols × 24 rows. The layout collapses gracefully below that but is not specifically tested below 60 × 20.

---

## 5 · First-run UX

The first `harbin` invocation produces a friendly, navigable empty state — no surprise modals.

1. `paths.ensure_*` creates `config_dir`, `data_dir`, `cache_dir`, `log_dir` if missing.
2. `config.yaml` is generated with full defaults via `paths.atomic_write_text`. Log line: `wrote default config <path>`.
3. The DB is initialized and migrations run.
4. JobMonitor renders the empty state:
   ```
   no fleets registered yet.
   add one:  /config → Fleets → + Add fleet
   or try a sample:  harbin sample-fleet add news
   ```
5. Console emits: `welcome to harbin · type /help to begin`.

No interactive wizard. The two onramps (add fleet via `/config`, or `harbin sample-fleet add`) are visible from the empty state.

---

## 6 · `harbin sample-fleet add`

Subcommand (not a slash command — it can run before harbin starts, or against a stopped install). Synopsis:

```
harbin sample-fleet add <name>
  where <name> ∈ {news, price-monitor}
```

**Behavior:**

1. Maps `<name>` to a hard-coded git URL (overview §8):
   - `news` → `https://github.com/dryotta/harbin-agent-sample-news`
   - `price-monitor` → `https://github.com/dryotta/harbin-agent-sample-price-monitor`
   - The mapping lives in `harbin/samples.py`.
2. Clones into `paths.dock_root / <fleet-name>` using `git clone` (system git, no auth from harbin).
3. Reads `.harbin/fleet.yaml` to discover the fleet's declared `name`.
4. Inserts a row in `fleets` via `Store` (opens the DB, runs migrations, inserts, closes — same boot path as TUI mode).
5. Prints:
   ```
   registered fleet 'harbin-agent-sample-news' at <dock-path>.
   start harbin to use it.
   ```

**Idempotency:** if the fleet (by name) is already registered, the subcommand prints `fleet already registered (no-op)` and exits 0. If the dock dir exists but the DB has no row, it inserts the row without re-cloning.

**Failure modes:**
- Unknown name → `UserError` with the allowed set printed, exit code 2.
- `git clone` failure (no network, bad ref) → `FleetError`, exit code 3; the partial clone is removed before exit.

---

## 7 · Distribution artifacts

| Channel | Artifact | When |
|---|---|---|
| PyPI | sdist + wheel | On every tagged release |
| GitHub Releases | Source tarball, changelog | On every tagged release |
| Docker image | **Out of scope for v1** | — |
| Native installers (`.msi`, `.pkg`, `.deb`) | **Out of scope for v1** | — |
| Curl-pipe-sh installer | **Out of scope for v1** (overview §9) | — |

A future v1.x release may add a thin curl-pipe-sh wrapper around `uv tool install harbin`. Not a v1 blocker.

---

## 8 · Open questions

- Code-signing on macOS / Windows: not relevant while distribution is via `uv` from PyPI. Re-evaluate if/when native installers ship.

## 9 · Out of scope

- Auto-update / self-update.
- Plugin / extension distribution (no plugin system in v1).
- Multi-version side-by-side installs (use `uv` to manage that if needed).
