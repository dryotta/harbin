# agent-runner — Sub-spec

> Status: **draft** · scope: job state machine, subprocess spawn, environment, stdio pipeline, cancellation, concurrency gating.
> Parent: [`design-overview.md`](./design-overview.md).

The Agent Runner is harbin's most pluggable subsystem: it spawns an external agent CLI as a subprocess, captures its stdio, and tracks the result as a job. The **invocation contract** (how the prompt reaches the binary, what command shape it takes) is separate — see [`11-agent-cli-invocation`](./11-agent-cli-invocation.md). This doc owns the **lifecycle**.

---

## 1 · Job state machine

```
queued ──► starting ──► running ──┬─► success
                                  ├─► failed
                                  └─► cancelled
                                          │
            ───────────────────────────────┴─► archived  (set by retention sweep)
```

Transitions:

| From → To | Trigger |
|---|---|
| (none) → `queued` | `enqueue_job` writes the row |
| `queued` → `starting` | Runner pops the job from its per-dock queue and begins to spawn |
| `starting` → `running` | Subprocess `pid` is known and stdio handlers are mounted |
| `starting` → `failed` | Spawn fails (binary missing, permission denied) |
| `running` → `success` | Subprocess exits with code 0 |
| `running` → `failed` | Subprocess exits with non-zero code or unexpected signal |
| `running` → `cancelled` | `/cancel` issued and the process was terminated |
| `*` → `archived` | Retention sweep ([`08-artifact-manager`](./08-artifact-manager.md) §4) |

Each transition is an atomic DB write that also sets the relevant timestamps (`started_at` on `starting → running`; `ended_at` on the three terminal transitions). A transition event is emitted on the event bus so the TUI's JobMonitor refreshes.

---

## 2 · Spawn

The runner uses `asyncio.create_subprocess_exec` exclusively — no shell interpolation.

```python
proc = await asyncio.create_subprocess_exec(
    *argv,
    cwd=dock_path,
    env=enriched_env,
    stdin=asyncio.subprocess.PIPE if mode == "stdin" else asyncio.subprocess.DEVNULL,
    stdout=asyncio.subprocess.PIPE,
    stderr=asyncio.subprocess.PIPE,
    start_new_session=True,                    # POSIX; CREATE_NEW_PROCESS_GROUP on Windows
)
```

- **`argv`** is constructed by the invocation module ([`11-agent-cli-invocation`](./11-agent-cli-invocation.md)) and includes the agent binary plus the right placeholder/flag shape.
- **`cwd=dock_path`** — every agent runs at the root of its fleet's dock.
- **`start_new_session`** isolates the subprocess from harbin's session so terminal signals don't bleed through, and lets us send signals to the whole subprocess group on cancellation.

### 2.1 Environment

The runner enriches `os.environ` with (overview §5.2):

```
HARBIN_ARTIFACT_DIR=<path>      # absolute path to per-job dir
HARBIN_FLEET=<fleet-name>
HARBIN_TASK_ID=<task-id-or-"adhoc">
HARBIN_JOB_ID=<short-id>
HARBIN_PROMPT=<the prompt text>
```

Notes:

- `HARBIN_PROMPT` is set even when the prompt is also delivered via stdin/flag/tempfile — agents may inspect it for logging without re-reading stdin.
- The full `os.environ` is inherited (no scrubbing) so agents have access to user-configured tokens (`GH_TOKEN`, `OPENAI_API_KEY`, etc.). Harbin doesn't manage agent secrets.
- The agent CLI invocation module is allowed to set additional vars (e.g. `HARBIN_AGENT_MODE` for the fake CLI in tests); they layer on top.

---

## 3 · Concurrency gating

Two caps, both checked before transitioning a job from `queued` to `starting`:

1. **Per-dock cap.** Default 1 (the default-serial policy in overview §5.2). Overridable per task via `schedule.yaml`:
   ```yaml
   - id: hourly-prices
     cron: "0 * * * *"
     prompt: …
     concurrency: parallel        # opts this task out of the per-dock serial gate
   ```
   `serial` (default) means "this task waits if any other job is running in this dock". `parallel` means "this task may run alongside other jobs in this dock" — useful when the agent never touches the worktree (read-only inspection prompts).
2. **Global cap.** `config.agent_runner.concurrency.global_cap`, default 4. A hard ceiling across the entire process.

The gating queue is implemented per-dock as an `asyncio.Queue`. The runner has one consumer coroutine per dock (`runner.dispatch:<fleet>`) that pulls jobs and respects both caps via a shared semaphore.

### 3.1 Why per-dock-serial by default

The dock is a shared mutable git worktree. Two concurrent agents running `git add` / `git commit` in the same dock are a recipe for race conditions and partial commits. The serial default prevents this; the `concurrency: parallel` escape hatch is for read-only prompts the operator has reasoned about.

---

## 4 · Stdio pipeline

Each job has two concurrent reader tasks, one each for `stdout` and `stderr`:

```
subprocess pipe ──► chunker (line-buffered) ──┬─► in-memory ring (4 MiB cap)
                                              ├─► DB job_log_chunks (with eviction)
                                              └─► <artifact_dir>/job.log (append-only)
```

- **Chunker** reads line by line (`asyncio.StreamReader.readline()`). Lines exceeding 8 KiB are split at the boundary; a trailing partial line at EOF is emitted as-is.
- **Ring buffer** is a `collections.deque` of `LogChunk` records on the runner, capped at 4 MiB *total text* per job. Used to back the JobView when it first opens (initial fill comes from the ring; subsequent updates from event stream).
- **DB writer** batches chunks for ~250 ms or until 32 chunks accumulate, whichever first; flushes in one transaction. The 4 MiB cap is enforced inside that transaction (see [`02-state-store`](./02-state-store.md) §4).
- **`job.log`** is an append-only `aiofiles` (or plain `open` in a thread) handle. Each line is written exactly once. The file is closed when the subprocess exits.

### 4.1 Backpressure

- The chunker tasks `await` the DB writer's queue. A slow DB pushes back on reading.
- A subprocess writing faster than the pipeline can drain will eventually block on the OS pipe buffer, which is fine — the agent stalls, harbin doesn't OOM. This is the same behavior `tee` would give and is documented for fleet authors.

### 4.2 Stream attribution

`stdout` and `stderr` are tagged separately in `job_log_chunks.stream`. `system` is reserved for runner-synthesized lines:

- `system: START job <short-id> at <iso>`
- `system: EXIT code=<n>` or `system: SIGNAL <name>`
- `system: …N earlier lines truncated; see job.log` (when the ring evicts)

These are visible in `/logs` and the JobView (muted italic; see [`12-tui-architecture`](./12-tui-architecture.md) §3).

---

## 5 · Cancellation

`/cancel <job>` (see [`13-repl-and-commands`](./13-repl-and-commands.md)) flips the job's intent to "cancel" and the runner reacts.

### 5.1 If `status = queued`

- Mark the job `cancelled` immediately. Remove from the per-dock queue. No subprocess to terminate.

### 5.2 If `status` ∈ {`starting`, `running`}

1. **Pre-flight stash.** Run `git -C <dock> stash push -u -m "harbin cancel <short-id>"`. If nothing to stash, skip silently. The stash preserves any uncommitted work the agent did so the operator can investigate (`git stash list` / `pop`).
   - Stash failure is logged at WARN but does not block cancellation.
2. **SIGTERM the subprocess group** (`os.killpg(pgid, SIGTERM)` on POSIX; `proc.terminate()` on Windows — sends `CTRL_BREAK_EVENT` to the process group thanks to `CREATE_NEW_PROCESS_GROUP`).
3. **Wait** up to `config.agent_runner.kill_grace_seconds` (default 10) for natural exit.
4. **SIGKILL** the group if it's still alive (`os.killpg(pgid, SIGKILL)` / `proc.kill()`).
5. Mark `status='cancelled'`, `exit_code` = whatever the process returned (often `-SIGTERM` on POSIX), `ended_at = now`.

### 5.3 Cancellation during shutdown

Same as `/cancel` but issued automatically for every running job (see [`04-concurrency-and-errors`](./04-concurrency-and-errors.md) §3). The pre-flight stash still runs.

---

## 6 · Post-run hooks

After a job's terminal transition, the runner runs these in order:

1. **Flush stdio buffers** (`job.log` close; final DB chunk flush).
2. **Push-back** (if `status='success'` and `fleet.yaml.artifact_policy.push_back: true`). Mechanics in [`07-fleet-and-dock-manager`](./07-fleet-and-dock-manager.md) §4. Failure is non-fatal.
3. **`ArtifactManager.finalize(job)`** — currently a no-op hook.
4. **Emit event** `job_ended` with the terminal state for the TUI.

---

## 7 · Failure classification

| Observed | `status` | `exit_code` | Notes |
|---|---|---|---|
| Process exits 0 | `success` | `0` | |
| Process exits >0 | `failed` | the exit code | |
| Process killed by SIGTERM via `/cancel` | `cancelled` | `-15` (POSIX) | |
| Process killed by SIGKILL (post-grace) | `cancelled` | `-9` (POSIX) | grace expired |
| Spawn failed (`FileNotFoundError`, etc.) | `failed` | `null` | one `system: …` log chunk has the reason |
| Wait timeout exceeded an outer guard (v2) | `failed` | `null` | not in v1 |

`exit_code` is stored as an integer (or NULL when there was no process). Windows reports unsigned codes; harbin stores the raw `proc.returncode` as-is.

### 7.1 No global timeout in v1

There is no per-job wall-clock timeout. Agents can run for as long as they need. Operators who want a cap can wrap their prompt with explicit instructions or hand the agent a `timeout(1)`-style helper. v2 candidate: `fleet.yaml.timeout: 1h`.

---

## 8 · Captured-at-spawn config

When a job moves from `queued → starting`, the runner captures a **snapshot** of:

- `agent_cli` (resolved fleet override > global default)
- `kill_grace_seconds`
- `concurrency` flags

Changes to these via `/config` mid-run do **not** affect running jobs. New jobs see the new values. This is the apply-live posture in [`03-configuration`](./03-configuration.md) §5.

---

## 9 · Open questions

- Whether to also persist the captured `agent_cli` snapshot on the `jobs` row for forensics (`agent_cli_json` column). Trivial migration; defer until first incident.
- Whether the runner should optionally serialize stderr to a separate file (`job.err`) for easier triage. Useful but not yet required.

## 10 · Out of scope (v1)

- Per-job timeouts (§7.1).
- Streaming agent stdout to the operator's clipboard.
- Conversation continuity / job resumption (overview §10).
- Cgroups / resource isolation (memory, CPU caps).
