# state-store — Sub-spec

> Status: **draft** · scope: SQLite schema, migrations, log-chunk ring, hot-path queries, retention coupling.
> Parent: [`design-overview.md`](./design-overview.md).

Harbin's durable state lives in a single SQLite file. This doc names the tables, the migration discipline, and the small set of queries that subsystems issue. The schema is small on purpose; complexity belongs in the subsystems, not in the DB.

---

## 1 · Engine and connection

- **Engine:** SQLite via `aiosqlite`. File path: `paths.db_path` (= `paths.data_dir / "harbin.db"`).
- **One process-wide connection,** owned by `harbin.db.store.Store`, accessed only from the event loop. Single-connection keeps writer serialization trivial; the workload is tiny (hundreds of writes/hour).
- **Pragmas applied at open** (in this order):
  ```sql
  PRAGMA journal_mode = WAL;
  PRAGMA synchronous  = NORMAL;
  PRAGMA foreign_keys = ON;
  PRAGMA busy_timeout = 5000;
  ```
- Reads and writes both go through `Store` methods that take/return typed records (small `@dataclass(frozen=True)` rows). No ORM.

---

## 2 · Tables

All timestamps are stored as ISO-8601 strings in UTC (`TEXT`); SQLite has no native timestamp type and TEXT keeps the file inspectable. Conversion happens in `Store`.

### 2.1 `fleets`

```sql
CREATE TABLE fleets (
  id            INTEGER PRIMARY KEY,
  name          TEXT NOT NULL UNIQUE,
  url           TEXT NOT NULL,
  dock_path     TEXT NOT NULL,
  registered_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
);
```

### 2.2 `tasks`

```sql
CREATE TABLE tasks (
  id         INTEGER PRIMARY KEY,
  fleet_id   INTEGER NOT NULL REFERENCES fleets(id) ON DELETE CASCADE,
  task_id    TEXT NOT NULL,
  cron       TEXT NOT NULL,
  prompt     TEXT NOT NULL,
  source_sha TEXT NOT NULL,           -- sha256(cron || '\0' || prompt) for cheap change detection
  UNIQUE(fleet_id, task_id)
);
```

`task_id` is the human ID from `schedule.yaml`; `id` is the surrogate referenced by jobs. `source_sha` lets the schedule hot-reloader skip no-op edits without diffing the prompt body.

### 2.3 `jobs`

```sql
CREATE TABLE jobs (
  id           INTEGER PRIMARY KEY,
  short_id     TEXT NOT NULL UNIQUE,  -- 6-char hex, what the user types in /logs etc.
  fleet_id     INTEGER NOT NULL REFERENCES fleets(id) ON DELETE CASCADE,
  task_pk      INTEGER NULL REFERENCES tasks(id) ON DELETE SET NULL,
  prompt       TEXT NOT NULL,
  source       TEXT NOT NULL CHECK (source IN ('repl','schedule')),
  status       TEXT NOT NULL CHECK (status IN (
                 'queued','starting','running','success','failed','cancelled','archived')),
  exit_code    INTEGER NULL,
  started_at   TEXT NULL,
  ended_at     TEXT NULL,
  artifact_dir TEXT NOT NULL
);
CREATE INDEX jobs_fleet_started ON jobs(fleet_id, started_at DESC);
CREATE INDEX jobs_status        ON jobs(status);
```

`task_pk` is `NULL` for ad-hoc jobs; the `ON DELETE SET NULL` preserves history if a scheduled task is later removed.

### 2.4 `job_log_chunks`

```sql
CREATE TABLE job_log_chunks (
  id     INTEGER PRIMARY KEY,
  job_id INTEGER NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
  seq    INTEGER NOT NULL,
  ts     TEXT NOT NULL,
  stream TEXT NOT NULL CHECK (stream IN ('stdout','stderr','system')),
  text   TEXT NOT NULL,
  UNIQUE(job_id, seq)
);
CREATE INDEX job_log_chunks_job ON job_log_chunks(job_id, seq);
```

Chunk granularity is one line of agent output (or one synthetic `system` line, e.g. "process exited 0"). The 4 MiB cap (overview §5.2) is enforced per `job_id` — see §4.

### 2.5 `schedule_state`

```sql
CREATE TABLE schedule_state (
  task_pk      INTEGER PRIMARY KEY REFERENCES tasks(id) ON DELETE CASCADE,
  last_fire_ts TEXT NOT NULL
);
```

One row per task. Written **after** the scheduler successfully enqueues a job, so a crash between "decide to fire" and "row written" simply causes the next tick to re-fire — the right behavior under the skip-missed-fires policy.

### 2.6 `app_state`

```sql
CREATE TABLE app_state (
  key   TEXT PRIMARY KEY,
  value TEXT NOT NULL
);
```

Bootstrapped keys: `schema_version`, `last_vacuum_at`.

---

## 3 · Migrations

- **Layout:** `src/harbin/db/migrations/NNN_description.sql`. `NNN` is zero-padded (`001`, `002`, …).
- **Runner:** at startup, `db.migrate.run(conn)`:
  1. Reads `SELECT value FROM app_state WHERE key = 'schema_version'` (treats missing as `0`).
  2. Walks migration files in sorted order; for each `NNN > current`, applies the file inside one transaction, then `UPSERT`s `schema_version = NNN`.
  3. Stops at the first failure; reports the failing file and exits nonzero. The schema is left at the last successful version.
- **Forward-only.** No down-migrations. If a migration is wrong, ship a new one that fixes it forward. This matches overview §5.3.
- **Backwards safety.** Harbin refuses to start if `schema_version > max known`, with a message asking the user to upgrade harbin.
- The first migration creates every table in §2 and inserts `('schema_version','1')`.

---

## 4 · Log-chunk ring

The authoritative on-disk log is `<artifact_dir>/job.log` (append-only, owned by the runner). `job_log_chunks` is the **DB tail surface** that backs the in-app JobView and `/logs`.

- **Cap:** per-job sum of `LENGTH(text)` ≤ 4 MiB.
- **Eviction:** the runner's chunk-writer transactionally inserts a new chunk and, if the cap would be exceeded, deletes the oldest chunks for that `job_id` until the cap is satisfied. Both happen in the same `BEGIN…COMMIT`.
- **Truncation marker:** when the first eviction occurs for a job, a `stream='system'` chunk is inserted: `…N earlier lines truncated; see job.log`. The TUI surfaces this as a single banner row in the JobView.
- **Startup sweep:** on boot, the store runs the cap query for every job in `('running','starting')` and re-applies eviction. Defensive — handles crashes mid-write.

---

## 5 · Hot-path queries

These three queries dominate runtime traffic. They are written by hand and live in `harbin.db.store`.

```sql
-- Recent jobs for the JobMonitor (most recent 50, prefer active)
SELECT id, short_id, fleet_id, task_pk, prompt, source, status,
       exit_code, started_at, ended_at, artifact_dir
FROM   jobs
WHERE  status IN ('queued','starting','running')
   OR  ended_at > :since
ORDER  BY COALESCE(started_at, ended_at) DESC
LIMIT  50;

-- Log tail for /logs and the JobView
SELECT seq, ts, stream, text
FROM   job_log_chunks
WHERE  job_id = :job_id
ORDER  BY seq DESC
LIMIT  :n;

-- Schedule fire decision
SELECT last_fire_ts FROM schedule_state WHERE task_pk = :task_pk;
```

Everything else (CRUD on fleets/tasks, status transitions, artifact bookkeeping) is low-frequency and uses straightforward parametrized SQL.

---

## 6 · Retention coupling

- The artifact sweep (see [`08-artifact-manager`](./08-artifact-manager.md)) is the trigger.
- For each expired job:
  ```sql
  DELETE FROM job_log_chunks WHERE job_id = :id;
  UPDATE jobs SET status = 'archived' WHERE id = :id;
  ```
  `jobs` rows are kept so the user can still see the history; only the chunks (and the on-disk artifacts) are reclaimed.
- A `VACUUM` runs at startup if `app_state.last_vacuum_at` is older than 30 days; updates the key on success.

---

## 7 · Concurrency notes

- All writes happen from the event loop via the single connection. Subsystems do not open their own connections.
- Long reads (e.g. `/logs -f` tailing) use the same connection but issue `LIMIT`-bounded queries on a timer; there is no async streaming cursor.
- aiosqlite's worker thread is an implementation detail; from harbin's perspective, all DB calls are awaitable and serialized.

---

## 8 · Open questions

- Whether to store a one-line failure summary on `jobs` (`failure_summary TEXT`) so the monitor can render "why did this fail" without joining log chunks. Defer until UX needs it; trivial migration when wanted.

## 9 · Out of scope

- Any ORM layer.
- Multi-tenant tables — harbin is single-user (overview §10).
- Backups beyond "stop harbin and copy the file".
