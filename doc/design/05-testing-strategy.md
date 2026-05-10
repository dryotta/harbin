# testing-strategy — Sub-spec

> Status: **draft** · scope: pytest layout, core fixtures, fake agent CLI, snapshot tests, CI lanes.
> Parent: [`design-overview.md`](./design-overview.md).

Three lanes, shared fixtures, one fake agent CLI. Tests live in `tests/` mirroring the package layout (overview / [`01-project-layout`](./01-project-layout.md)).

---

## 1 · Lanes

| Lane | Path | Purpose | Target speed |
|---|---|---|---|
| **unit** | `tests/unit/` | Pure-function and single-class tests. No subprocess, no real git. | < 5 s total |
| **integration** | `tests/integration/` | Multi-subsystem flows: cron → scheduler → runner → artifact. Real local git (bare repos in temp dirs), real SQLite, fake agent CLI. | < 60 s total |
| **snapshot** | `tests/snapshot/` | Textual snapshot tests for key screens (overview empty, overview with jobs, JobView, `/config`). | < 30 s total |

Lanes run independently; CI gates each separately (§6).

---

## 2 · Core fixtures (`tests/conftest.py`)

All fixtures are async-aware (`pytest-asyncio`, `asyncio_mode = "auto"`).

| Fixture | Scope | Yields |
|---|---|---|
| `harbin_home(tmp_path)` | function, auto-used | `Path` — sets `HARBIN_HOME=tmp_path/harbin` for the duration of the test |
| `store(harbin_home)` | function | `Store` — aiosqlite-backed, migrations applied to a fresh DB |
| `config(harbin_home)` | function | `Config` — minimal pydantic config with safe defaults |
| `local_fleet(tmp_path)` | function | `LocalFleetSpec` — initialized bare git repo + working clone with `.harbin/fleet.yaml`; provides the URL and dock path needed to register |
| `fake_agent_cli()` | session | `Path` — absolute path to `tests/fixtures/fake_agent_cli.py` |
| `clock()` | function | `FakeClock` — monotonic + wall clocks the scheduler reads through; tests advance it manually |

Auto-using `harbin_home` keeps every test fully isolated and guarantees no test ever touches the user's real config or data dirs.

---

## 3 · Fake agent CLI

Path: `tests/fixtures/fake_agent_cli.py` — shipped as **test data**, not in the harbin package. (This resolves the open question in `plan.md` §open-questions.)

Honors the contract from [`11-agent-cli-invocation`](./11-agent-cli-invocation.md):

| Behavior | How |
|---|---|
| Read prompt | Mode dictated by `HARBIN_AGENT_MODE` env: `stdin` (default), `flag` (reads `--prompt`), `tempfile` (reads `--prompt-file`) |
| Produce artifact | Writes `${HARBIN_ARTIFACT_DIR}/result.txt` containing `f"echoed: {prompt[:80]}\n"` |
| Exit code | `HARBIN_FAKE_EXIT` env, integer, default `0` |
| Duration | `HARBIN_FAKE_DURATION` env, seconds, default `0`. Honors SIGTERM during sleep (writes `cancelled` to stderr and exits 143) |
| Stdout/stderr | Deterministic: emits `START\n` then `END\n` to stdout, exit-reason on stderr if nonzero |

The fake CLI is the most-reused test piece. Adding new options requires updating this doc.

---

## 4 · Snapshot tests

- Driver: `pytest-textual-snapshot`.
- Baselines committed under `tests/snapshot/__snapshots__/`.
- Each test instantiates the Textual `App`, scripts a sequence of keypresses, and asserts the rendered terminal matches the baseline SVG.
- **Update workflow:** `uv run pytest tests/snapshot --snapshot-update`. Diffs are then reviewed as code in the PR.
- Minimum coverage v1:
  - Overview screen: empty state.
  - Overview screen: with 1 queued + 1 running + 1 succeeded job.
  - `JobView` for a running job.
  - `/config → General` page.
  - `/config → Fleets` page with one fleet.

Snapshots are inherently brittle to font / glyph changes; CI pins the rendering size and color depth.

---

## 5 · Integration scenarios (must-cover for v1)

These are the load-bearing tests; missing any of them means the runner / scheduler / fleet plane is undertested.

1. **Adhoc happy path.** Register fleet → `@fleet hello` → fake agent runs → artifact `result.txt` exists → `jobs.status = success`, `exit_code = 0`.
2. **Scheduled happy path.** Register fleet with `schedule.yaml` (cron `* * * * *`) → advance clock → scheduler fires → same assertions.
3. **Hot reload schedule.** Edit `schedule.yaml` while harbin is up → watchdog picks change up < 1 s → next fire uses new cron.
4. **Cancel.** Start a long fake job (`HARBIN_FAKE_DURATION=30`) → `/cancel <id>` → status `cancelled` within `kill_grace_seconds + 1`.
5. **Failure path.** `HARBIN_FAKE_EXIT=2` → status `failed`, `exit_code=2`, stderr in log chunks.
6. **Retention sweep.** Backdate a job's `ended_at` past retention → run sweep → artifact dir removed, log chunks deleted, job `status = archived`.
7. **Push-back.** `artifact_policy.push_back: true` → after success, dock has a new commit with the structured message from overview §4.2; bare repo received the push.
8. **Per-dock serial.** Fire two adhoc jobs against the same fleet → second waits until first finishes (per-dock concurrency 1).

---

## 6 · CI lanes

GitHub Actions workflow at `.github/workflows/ci.yml`. Each job runs on the OS matrix unless noted.

| Job | Command | Matrix | Trigger |
|---|---|---|---|
| `lint` | `uv run ruff check . && uv run ruff format --check . && uv run mypy` | Linux | push, PR |
| `unit` | `uv run pytest tests/unit` | Linux, macOS, Windows | push, PR |
| `integration` | `uv run pytest tests/integration` | Linux, macOS, Windows | PR, main |
| `snapshot` | `uv run pytest tests/snapshot` | Linux only | PR, main |

- Python: 3.14.
- Windows `integration` jobs that exercise `git push` to a local bare repo are allowed-to-fail in v1 and tracked as tech-debt (per overview §meta — patches must be tracked).
- Snapshot lane is Linux-only because terminal rendering on Windows runners is unreliable; users are not affected.

---

## 7 · Conventions

- Test files: `test_<module>.py`, mirroring the package path.
- Async tests use plain `async def`; the `@pytest.mark.asyncio` marker is implicit via `asyncio_mode = "auto"`.
- Avoid `time.sleep`; use the `clock` fixture or `asyncio.sleep(0)` to flush the loop.
- A test that needs a sleep > 100 ms in CI is suspect; reach for `FakeClock` instead.
- Subprocesses spawned by tests must respect `HARBIN_HOME` (the agent runner already passes the env through; tests should not bypass).

---

## 8 · Out of scope (v1)

- Performance benchmarks.
- Property-based tests (welcome later via `hypothesis`; not required for v1).
- Mutation testing.
- Real GitHub-Copilot CLI smoke tests in CI (would require auth secrets).
