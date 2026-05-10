# scheduler — Sub-spec

> Status: **draft** · scope: tick loop, croniter usage, missed-fire policy, timezone handling, hot-reload diff, persistence.
> Parent: [`design-overview.md`](./design-overview.md).

The Scheduler decides when to enqueue jobs whose `source = schedule`. It is small: one coroutine, one query per tick, one row written per fire. Everything that makes it *correct* (timezone math, missed-fire policy, hot reload) is pinned here.

---

## 1 · Tick loop

```python
async def tick(self) -> None:
    while not self.draining:
        await self._maybe_fire()
        await asyncio.sleep(self.config.tick_seconds)
```

- **Tick period:** `config.scheduler.tick_seconds`, default **5 s**, range 1..60.
- **Single coroutine** under the root TaskGroup, named `scheduler.tick`.
- **Drain flag** set by shutdown stops the loop; in-flight `_maybe_fire` finishes before exit.

The whole scheduler design assumes the tick is "frequent enough that cron resolution to the second doesn't matter, infrequent enough not to be a hot spinner". 5 s is the sweet spot.

---

## 2 · Decision algorithm

On every tick, for every registered task:

```
1. now_local = current time in config.timezone
2. anchor = schedule_state.last_fire_ts  OR  task.registered_at
3. next  = croniter(task.cron, anchor, tz=config.timezone).get_next(datetime)
4. if next <= now_local:
       enqueue_job(task, source="schedule")
       upsert schedule_state(task.id, now_local)
```

Notes:

- The check is `next <= now_local` — exactly once per due window, deterministic.
- The upsert of `schedule_state` is the source of truth that "this task has fired through this moment". It is written **after** `enqueue_job` returns its DB row (`enqueue_job` inserts the `jobs` row inside a single transaction). A crash between "decide to fire" and "upsert" causes the next tick to re-fire the same window — acceptable, because the job row exists; the cost is one extra fire on a vanishingly rare crash. (We prefer over-fire to under-fire on transient failures; missed-fire skip is a deliberate policy for the *off* case, not the *crash* case.)
- The query in step 3 uses `croniter(..., ret_type=datetime)` and a `pytz`/`zoneinfo` timezone so DST math is honored.

### 2.1 Per-tick query

To avoid N round-trips:

```sql
SELECT t.id, t.cron, t.fleet_id, t.task_id, s.last_fire_ts, t.registered_at
FROM tasks t
LEFT JOIN schedule_state s ON s.task_pk = t.id;
```

Returned rows are filtered in Python. With low task counts (overview's working set is tens, not thousands) this is fine.

---

## 3 · Timezone handling

- `config.timezone` is either an IANA zone name or `"system"`. `"system"` resolves at startup to `time.tzname` via `datetime.datetime.now().astimezone().tzinfo`.
- All scheduler math runs in this single zone. Cron expressions are interpreted in it.
- Timestamps in the DB are stored as **UTC** ISO-8601 strings (see [`02-state-store`](./02-state-store.md) §2); the scheduler converts on read/write.

### 3.1 DST / clock-jump edge cases

| Scenario | Behavior |
|---|---|
| Spring forward (e.g. 02:00→03:00). A `0 2 * * *` cron fires when on that day? | **Skipped.** croniter returns the next valid 02:00, which is tomorrow. The user's task is "every 02:00 local"; the impossible local time has no fire. |
| Fall back (e.g. 02:00→01:00 repeated). A `0 1 * * *` cron? | **Fires once.** `last_fire_ts` is written after the first fire; the second 01:00 produces `next > last_fire_ts` only after the duplicated hour ends, so it does not re-fire on the same wall date. |
| System clock jumps forward by hours (NTP correction) | Any tasks with `next ≤ now_local` after the jump fire on the next tick. Missed-fire semantics (§4) apply if the jump skipped over a fire window. |
| System clock jumps backwards | `next > now_local` for a while; no fires until time catches up. |

The scheduler does **not** detect clock jumps explicitly. It always trusts `now_local` and `last_fire_ts`; the comparison is monotonic per task.

---

## 4 · Missed-fire policy

- **Silent skip.** If harbin was off when a cron should have fired, the firing is lost (overview §5.1). `schedule_state.last_fire_ts` is set on startup to `now_local` for any task whose row is missing — meaning "next fire is the next due window after startup".
- One INFO log line at startup: `scheduler: N tasks loaded; missed fires skipped if any`.
- **v2 candidate:** opt-in `catchup: latest` per task. Schema is forward-compatible (add a column with default `'skip'`).

---

## 5 · Hot reload of `schedule.yaml`

When the watchdog fires for a fleet's `schedule.yaml` (see [`07-fleet-and-dock-manager`](./07-fleet-and-dock-manager.md) §5), the scheduler receives a list of validated `TaskSpec` records and reconciles against the `tasks` table for that fleet.

Diff classification per `task_id`:

| Old | New | Effect |
|---|---|---|
| absent | present | **Add.** Insert into `tasks`. No fire yet (cold start: first fire is the next due window after now). |
| present | absent | **Remove.** Delete from `tasks` (cascades to `schedule_state`). In-flight jobs from this task continue to completion. |
| present, `source_sha` unchanged | present, `source_sha` unchanged | **No-op.** No DB write. |
| present | present, `cron` changed | **Re-anchor.** Update `tasks.cron`. Set `schedule_state.last_fire_ts = now_local` so the new cron is evaluated against the present moment. |
| present | present, `prompt` changed | Update `tasks.prompt`. `last_fire_ts` is left intact; the cron cadence is preserved. |

`source_sha` is `sha256(cron || "\0" || prompt)` (per [`02-state-store`](./02-state-store.md) §2.2). Any change there means at least one of cron/prompt differs and triggers re-evaluation.

A fleet that's currently **disabled** (because `fleet.yaml` failed validation) has its schedule changes deferred until validation passes.

---

## 6 · Enqueuing a job

Step 3 of the algorithm (`enqueue_job`) goes through `AgentRunner.enqueue(...)`, which:

1. Generates a 6-char hex `short_id` (unique check via DB).
2. Captures the current `agent_cli` config for the fleet (override-aware).
3. Resolves the `artifact_dir` (per [`08-artifact-manager`](./08-artifact-manager.md) §1).
4. Inserts the `jobs` row with `status='queued'`.
5. Posts the job onto the runner's per-dock queue.

The scheduler does **not** spawn subprocesses; it only enqueues. The runner respects concurrency caps (see [`10-agent-runner`](./10-agent-runner.md) §3).

---

## 7 · Pause and resume

- A draining harbin (shutdown in progress) sets `self.draining = True`; the tick loop exits at the next sleep boundary.
- There is no user-facing "pause scheduler" in v1. To pause, the user removes or comments the cron in `schedule.yaml`; the watchdog handles it.
- `/schedule [fleet]` shows the loaded tasks and their next-fire times computed via `croniter(..., ret_type=datetime).get_next()`.

---

## 8 · Observability

- INFO log line on each fire: `scheduler: fired <fleet>.<task-id> -> job <short-id>`.
- DEBUG log line on each tick when no fires happen: nothing (silent — too noisy at level INFO, redundant at DEBUG).
- The TUI's JobMonitor shows the freshly-enqueued job within one render frame.

---

## 9 · Open questions

- Whether `/schedule` should also let the user run "fire this task now" without waiting. Likely useful; defer until UX feedback shows demand.
- Whether to surface "next fire" on the fleet's monitor row by default, or only in `/schedule`. Default: only in `/schedule`; the monitor row stays minimal.

## 10 · Out of scope (v1)

- `catchup: latest` policy (§4).
- Sub-minute cron expressions; croniter supports them with extended syntax, but the tick period (5 s default) caps useful resolution. Document as "best resolution: 1 minute".
- Inter-task dependencies (run task B after task A); use a single combined task instead.
