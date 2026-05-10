-- 001: initial schema. Matches sub-spec 02 §2.

CREATE TABLE IF NOT EXISTS fleets (
  id            INTEGER PRIMARY KEY,
  name          TEXT NOT NULL UNIQUE,
  url           TEXT NOT NULL,
  dock_path     TEXT NOT NULL,
  registered_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
);

CREATE TABLE IF NOT EXISTS tasks (
  id         INTEGER PRIMARY KEY,
  fleet_id   INTEGER NOT NULL REFERENCES fleets(id) ON DELETE CASCADE,
  task_id    TEXT NOT NULL,
  cron       TEXT NOT NULL,
  prompt     TEXT NOT NULL,
  source_sha TEXT NOT NULL,
  registered_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
  UNIQUE(fleet_id, task_id)
);

CREATE TABLE IF NOT EXISTS jobs (
  id           INTEGER PRIMARY KEY,
  short_id     TEXT NOT NULL UNIQUE,
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
CREATE INDEX IF NOT EXISTS jobs_fleet_started ON jobs(fleet_id, started_at DESC);
CREATE INDEX IF NOT EXISTS jobs_status        ON jobs(status);

CREATE TABLE IF NOT EXISTS job_log_chunks (
  id     INTEGER PRIMARY KEY,
  job_id INTEGER NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
  seq    INTEGER NOT NULL,
  ts     TEXT NOT NULL,
  stream TEXT NOT NULL CHECK (stream IN ('stdout','stderr','system')),
  text   TEXT NOT NULL,
  UNIQUE(job_id, seq)
);
CREATE INDEX IF NOT EXISTS job_log_chunks_job ON job_log_chunks(job_id, seq);

CREATE TABLE IF NOT EXISTS schedule_state (
  task_pk      INTEGER PRIMARY KEY REFERENCES tasks(id) ON DELETE CASCADE,
  last_fire_ts TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS app_state (
  key   TEXT PRIMARY KEY,
  value TEXT NOT NULL
);

INSERT OR IGNORE INTO app_state(key, value) VALUES ('schema_version', '1');
