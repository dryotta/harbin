# fleet-and-dock-manager — Sub-spec

> Status: **draft** · scope: dock filesystem layout, registration, git-based sync, push-back, watchdog hot reload.
> Parent: [`design-overview.md`](./design-overview.md).

The Dock Manager is harbin's bridge between a remote fleet repo (on GitHub or any git host) and a local working tree that agents run inside. One dock per fleet, plain `git clone` underneath — no worktree gymnastics, no git library, just the system `git` binary called through `asyncio.create_subprocess_exec`.

---

## 1 · Dock filesystem layout

```
paths.data_dir/docks/
└── <fleet-name>/             # plain git clone
    ├── .git/
    ├── .harbin/
    │   ├── fleet.yaml
    │   └── schedule.yaml     # optional
    ├── .github/              # standard GH Copilot conventions; harbin does not read
    └── …                     # whatever the fleet repo contains
```

Invariants:

- **One dock per fleet.** `<fleet-name>` matches `fleet.yaml.name` (overview §4.1 pattern).
- **Plain clone.** The default-branch worktree is always checked out; harbin never uses bare clones or detached HEADs.
- **Harbin reads `.harbin/` only.** `.github/` and `skills/` are interpreted by the agent CLI, not by harbin (overview §4.1).

---

## 2 · Registration

Triggered by `/config → Fleets → + Add fleet`, by `harbin sample-fleet add`, or programmatically by tests.

1. Validate the URL syntactically (rough match — `git` does the real validation).
2. Resolve the target dock path: `paths.dock_root / <preliminary-name>`. The **preliminary name** is the URL's basename minus `.git`; the final name comes from `.harbin/fleet.yaml.name`.
3. `git clone --depth=50 <url> <preliminary-path>` (shallow by default; can be deepened with `git fetch --unshallow` if a fleet needs more history).
4. Read `.harbin/fleet.yaml`. Validate (see [`03-configuration`](./03-configuration.md) §2).
5. If `fleet.yaml.name` differs from `<preliminary-name>`, move the directory. Name collisions: error and roll back the clone.
6. Insert the row in `fleets` via `Store`.
7. Start a watchdog observer on `<dock>/.harbin/` (see §5).
8. Schedule the first sync immediately and then per `sync_interval`.

Failures at any step roll back: a partially cloned directory is removed; the DB row is only inserted at step 6.

---

## 3 · Sync algorithm

Each dock has a per-fleet sync coroutine, named `dock.sync:<fleet>`.

Loop (period = `sync_interval`, default `5m`):

```
1. git fetch --prune origin
2. determine worktree state:
   - clean   = `git status --porcelain` is empty
   - on default branch = `git symbolic-ref --short HEAD` == fleet.default_branch
3. if clean AND on default branch:
     git merge --ff-only origin/<default_branch>
   else:
     surface DockWarning (see §3.1) and skip
4. emit DockSyncResult event (success | ff'd | warned | error)
```

Notes:

- `git fetch` runs even when the worktree is dirty — fetching does not touch the worktree.
- Sync **never** force-updates, rebases, resets, or stashes during the periodic loop. Dirty trees are left alone; the operator decides.
- A `DockError` (network failure, auth failure, missing remote) is logged at WARN and surfaced as a fleet warning row; the loop continues.

### 3.1 Dirty-tree handling

When the worktree is dirty or off-branch, the next monitor refresh shows on that fleet's row:

```
⚠ dock dirty — fast-forward skipped
```

The user can run `/sync <fleet>` for a verbose attempt, or open the dock directly. Harbin never modifies user changes implicitly.

### 3.2 `/sync <fleet>` command

A user-initiated sync. Same algorithm as the periodic loop, but emits its result lines to the Console so the operator sees what happened. On dirty state, it prints the offending `git status --short` output.

---

## 4 · Push-back

Triggered only when `fleet.yaml.artifact_policy.push_back: true` and the job ended with `status = success`.

Sequence (in the AgentRunner's post-job hook):

1. `git -C <dock> add <artifact_dir_inside_repo>`.
2. If nothing was staged (the agent wrote nothing to the dock), skip — no commit, no push.
3. `git -C <dock> commit -m <message>` with the structured message:
   ```
   harbin: <task-id-or-"adhoc"> @<utc-iso>

   job: <short-id>
   prompt: <first 80 chars of prompt, single line, ellipsized>
   ```
   - Author/committer: `harbin <harbin@localhost>` unless overridden by repo `.gitconfig`.
   - `--allow-empty` is **not** used — empty diffs do not produce commits.
4. `git -C <dock> push origin <default_branch>`.
5. On any failure: log at WARN, surface a fleet warning row (`push failed: <reason>`), keep the local commit. Operator can retry with `/sync` or push by hand. **Artifacts on disk are never deleted because push failed.**

Pre-conditions checked before step 1:
- The worktree is on `default_branch`. Otherwise: skip with a warning.
- The fetch loop has not detected upstream divergence since the job started; if it has, skip and warn (avoids non-ff push attempts).

### 4.1 Where do artifacts land in the repo?

Only files written **inside the dock** are pushable. Artifacts in `paths.artifact_root` (the default) live outside the dock and are **not** pushed. A fleet that wants push-back must instruct its agent to write into the dock — typically a `briefs/` or `data/` directory committed to the repo. The `harbin-agent-sample-news` fleet (overview §8.1) demonstrates this pattern.

---

## 5 · Watchdog hot reload

For every registered dock:

- Observer watches `<dock>/.harbin/` (recursive=False).
- Events: `Created`, `Modified`, `Moved`, `Deleted` for `fleet.yaml` and `schedule.yaml`.
- Debounce: 250 ms — a series of saves within the window collapses to one reload.

Reload action:

| Change | Effect |
|---|---|
| `fleet.yaml` modified | Re-validate. On error → fleet disabled (config doc §4). On success → diff and apply per [`03-configuration`](./03-configuration.md) §5. |
| `schedule.yaml` modified | Re-validate. On success, ask Scheduler to apply the schedule diff (see [`09-scheduler`](./09-scheduler.md) §5). |
| `fleet.yaml` deleted | Fleet disabled with a `DockError` warning row. Dock is **not** removed (user may be mid-edit). |
| `schedule.yaml` deleted | All scheduled tasks for this fleet are torn down; the fleet becomes on-demand only. |

Reloads never touch running jobs: they capture their config at spawn time.

---

## 6 · Auth posture

- Harbin uses the **system `git` binary** for all operations. No `pygit2`, no `dulwich`, no embedded HTTPS client.
- Authentication delegates to the user's credential helper of choice: `gh auth`, SSH agent, Git Credential Manager on Windows, `osxkeychain` on macOS.
- Harbin **never stores tokens** or invokes credential helpers itself. Failed auth surfaces as a `DockError` with the underlying git stderr.
- For corporate proxies: the user configures `git config http.proxy` (or environment vars); harbin inherits the environment.

---

## 7 · Subprocess hygiene

- Each git call has a hard timeout (default 60 s for fetch/push, 5 s for status/symbolic-ref) wrapped via `asyncio.wait_for`.
- Stderr is captured separately and surfaced in errors (`DockError.detail`).
- Working directory is `<dock>` via `cwd=` argument; `-C <dock>` is used as belt-and-suspenders for diagnostics.
- Environment is passed through unchanged (so the user's `GIT_*` env vars work).

---

## 8 · DB coupling

| Table | Owned by Dock Manager? |
|---|---|
| `fleets` | Yes (insert on registration, update name/url on rename — v2) |
| `tasks` | No — managed by Scheduler from `schedule.yaml` |

The Dock Manager emits high-level events on its TaskGroup channel (`fleet_registered`, `fleet_disabled`, `dock_dirty`, `push_back_failed`); subscribers (Scheduler, TUI) react.

---

## 9 · Open questions

- **Rename a fleet** (change `fleet.yaml.name`): mechanics are non-trivial (dock dir rename + DB update + artifact path migration). Deferred to v2; for v1, edits to `name` are rejected as an error with a clear message.
- **Branch-specific fleets** (operate on a non-default branch): deferred. Today, every dock tracks `default_branch`.

## 10 · Out of scope

- Submodules, LFS — supported only insofar as the user's `git` handles them; harbin doesn't manage them.
- Worktrees (`git worktree add`) — not used in v1; each fleet has its own clone.
- Conflict resolution UI; harbin does not attempt merges other than fast-forwards.
