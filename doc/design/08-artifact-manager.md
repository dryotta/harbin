# artifact-manager — Sub-spec

> Status: **draft** · scope: artifact directory layout, `HARBIN_ARTIFACT_DIR` semantics, retention sweep, browser integration, push-back coupling.
> Parent: [`design-overview.md`](./design-overview.md).

The Artifact Manager owns the on-disk directory tree under which agent jobs deposit files. It is intentionally small: one directory per job, one env var pointed at it, a daily sweep to enforce retention.

---

## 1 · Directory layout

```
paths.artifact_root/                                # = data_dir/artifacts by default
└── <fleet-name>/
    └── <task-id-or-"adhoc">/
        └── <short-id>/                             # per-job dir; matches jobs.short_id
            ├── job.log                             # append-only stdio capture
            └── …                                   # files the agent writes
```

Invariants:

- **One directory per job.** Created empty by the Artifact Manager **before** the agent subprocess is spawned. This ensures `HARBIN_ARTIFACT_DIR` exists by the time the agent runs.
- **`<task-id-or-"adhoc">`** is the human task id from `schedule.yaml` for scheduled jobs, and the literal string `adhoc` for REPL `@fleet` jobs (overview §4.3).
- **`<short-id>`** is the 6-char hex from `jobs.short_id` (see [`02-state-store`](./02-state-store.md) §2.3). Globally unique within the install, so it's safe to use as a leaf directory name.
- **`job.log`** is owned by the AgentRunner (see [`10-agent-runner`](./10-agent-runner.md) §4); it lives in the artifact dir because it *is* an artifact of the job.

`paths.artifact_root` defaults to `paths.data_dir / "artifacts"` and is overridable via `config.artifacts.root` (absolute path required; relative paths are rejected at validation).

---

## 2 · Job lifecycle hooks

The Artifact Manager exposes a tiny interface to the AgentRunner:

```python
class ArtifactManager:
    async def prepare(self, job: JobRow) -> Path: ...   # called pre-spawn
    async def finalize(self, job: JobRow) -> None: ...  # called post-exit
```

- **`prepare`** computes the path `<root>/<fleet>/<task-or-adhoc>/<short-id>`, creates it (including parents) with `mkdir(parents=True, exist_ok=False)`, stores it in `jobs.artifact_dir`, and returns the path. `exist_ok=False` is intentional: a collision indicates a `short_id` collision, which is a bug.
- **`finalize`** is currently a no-op for status accounting (the runner writes the final `jobs.status` itself). It exists as a hook for v2 features like artifact summarization.

---

## 3 · `HARBIN_ARTIFACT_DIR` semantics

The AgentRunner enriches the subprocess environment (overview §5.2):

```
HARBIN_ARTIFACT_DIR = <absolute path to the per-job dir>
```

Agents are encouraged to write idiomatically:

```python
out = Path(os.environ["HARBIN_ARTIFACT_DIR"]) / "brief.md"
out.write_text(content)
```

- **No quoting / escaping concerns:** the value is a clean absolute path with no shell-special characters in practice (harbin controls the layout). Documented in the agent CLI spec.
- **Outside writes are allowed.** Agents may write to `/tmp`, user-specified paths, or anywhere else they have permission. Harbin only tracks what lands inside `HARBIN_ARTIFACT_DIR`. Anything outside is the agent's business and not subject to retention.
- **Cross-platform:** on Windows the path is back-slash-separated; agents that use `os.path` / `pathlib` handle it without ceremony.

---

## 4 · Retention sweep

A single daily coroutine, named `artifacts.sweep`, runs under the root TaskGroup.

### 4.1 Schedule

- Cron expression from `config.artifacts.sweep_cron`, default `"0 4 * * *"` (04:00 local).
- Driven by croniter on top of the scheduler's tick infrastructure (see [`09-scheduler`](./09-scheduler.md) §2) — same code path as fleet tasks; reuse over re-implementation.
- Manual trigger via `/sweep` is **out of scope for v1**; users restart harbin if they need it immediately.

### 4.2 Algorithm

For each fleet:

1. Read effective retention (`fleet.yaml.artifact_policy.retain`, defaulting to `config.artifacts.retention`). Parsed to a `timedelta`.
2. Query candidate jobs:
   ```sql
   SELECT id, short_id, artifact_dir
   FROM   jobs
   WHERE  fleet_id = :fleet
     AND  status IN ('success','failed','cancelled')
     AND  ended_at < :cutoff;
   ```
3. For each row:
   - `shutil.rmtree(artifact_dir, ignore_errors=False)`. On `FileNotFoundError`, log INFO and continue.
   - Then transactionally:
     ```sql
     DELETE FROM job_log_chunks WHERE job_id = :id;
     UPDATE jobs SET status = 'archived' WHERE id = :id;
     ```
   - The `jobs` row stays — users can still see the history in `/jobs --all`. Only the bytes are reclaimed.

### 4.3 Active job safety

Jobs in `queued`, `starting`, or `running` are **never** swept, even if their `ended_at` (which is NULL) would lexically compare. The status filter is the gate.

### 4.4 Failure handling

- Per-job failures (permission denied, file in use) are logged at WARN and **do not abort the sweep**; the next day picks them up.
- A whole-sweep crash leaves DB state untouched (everything is per-job-transactional) and is reported as `InternalError` on the next tick.

---

## 5 · Browser integration

The TUI exposes artifacts in two places:

- `/artifacts <fleet>` slash command (see [`13-repl-and-commands`](./13-repl-and-commands.md)): opens a tree panel rooted at `<artifact_root>/<fleet>/`.
- Inside `JobViewScreen` (see [`12-tui-architecture`](./12-tui-architecture.md) §3): a sidebar lists the files in the job's artifact dir; pressing `enter` on a file jumps to the artifact tree focused on that file.

The tree uses Textual's `DirectoryTree` widget. File contents preview is **read-only** and limited to:

- Text files (≤ 1 MiB): rendered in a `RichLog` with syntax highlighting via Textual's `Syntax`.
- Larger / binary files: shown as a stub `<binary, N bytes — open in editor>`.

The browser does not provide edit, delete, or rename — those are deliberately not in scope.

---

## 6 · Push-back coupling

Push-back is the AgentRunner's responsibility (see [`07-fleet-and-dock-manager`](./07-fleet-and-dock-manager.md) §4); the Artifact Manager only contributes the path data:

- Pushed files are those the agent wrote **inside the dock** (typically into `briefs/` or similar). Files in `paths.artifact_root` (the default external location) are **not** pushed.
- The Artifact Manager publishes `job.artifact_dir` so the runner's push-back hook knows what to `git add`. When the artifact dir is inside the dock (the push-back case), `git add <relative-path>` works naturally; when it's outside (the default), the hook short-circuits and no commit happens.

Detection rule: an artifact dir is "inside the dock" iff `Path(artifact_dir).is_relative_to(dock_path)`.

---

## 7 · Disk-space considerations

- No active quota enforcement in v1. Retention is the only mechanism.
- If `artifact_root` runs out of space, writes from agents fail noisily; harbin surfaces the resulting `RunnerError` per [`04-concurrency-and-errors`](./04-concurrency-and-errors.md) §5.
- v2 candidate: per-fleet disk budget (`artifact_policy.budget: 5gb`) that triggers oldest-first eviction independent of time-based retention.

---

## 8 · Open questions

- Whether `prepare` should also write a `.harbin-job.json` metadata file in the artifact dir (containing `short_id`, `prompt`, `started_at`). Convenient for offline forensics; deferred until a user asks.
- Whether the browser should let you copy a file out (`y` to copy path to clipboard). Cheap win; deferred to first UX iteration.

## 9 · Out of scope (v1)

- Active disk quotas (§7).
- Cross-fleet artifact deduplication.
- Encrypted artifact storage.
- Streaming / partial-file viewing for huge artifacts.
