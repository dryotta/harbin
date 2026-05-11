# Implementation notes & clarifications

This file documents decisions made during implementation that were not
explicit in the design docs, or where multiple reasonable interpretations
existed. It is updated as more cases come up.

## 1 · Python 3.14 floor

The design (overview §3.1, project-layout §2) pins Python >= 3.14. We
honor that in `pyproject.toml`. CI users on older Pythons must upgrade.

## 2 · `figlet` header

`overview §6.1` shows a "figlet logo" in the TUI header. Rather than
adding `pyfiglet` as a runtime dependency for a single decorative line,
we ship a small ASCII banner string in `harbin.tui.theme`. Swappable
later if a fuller logo is wanted.

## 3 · `mypy --strict` posture

Sub-spec 01 §2 sets `mypy --strict` on the package. A handful of
Textual integration points (event handlers, dynamic CSS) are inherently
loose; we keep them strict where reasonable and use targeted `# type:
ignore[<rule>]` with explanatory comment where Textual's stubs are
incomplete.

## 4 · Snapshot tests

Sub-spec 05 §4 lists pytest-textual-snapshot baselines. We include the
test scaffolding but do not commit baselines from this environment —
they should be regenerated on the canonical CI Linux runner where
rendering is reproducible. The snapshot lane is Linux-only per §6.

## 5 · `gh copilot suggest` preset

Sub-spec 11 §4 lists `gh copilot suggest` with `["--target", "shell"]`.
The real `gh copilot suggest` flag is `--target`/`-t`; we follow the
exact string from the doc.

## 6 · `keybindings` overrides

Sub-spec 12 §4.1 settled the open question from sub-spec 03: action-symbolic
overrides only. The stable action identifiers are defined in
`harbin.tui.app.ACTIONS` and listed in the keybindings page.

## 7 · `/help` text source

Sub-spec 13 §3.1: inline strings on the command class. We follow that;
no external markdown loaded at runtime.

## 8 · Push-back author

Sub-spec 07 §4 step 3 names `harbin <harbin@localhost>` as the default
author. We pass `-c user.name=harbin -c user.email=harbin@localhost`
to `git commit` so the user's `~/.gitconfig` is not mutated. Verified
end-to-end via `tests/integration/test_dock_manager.py::test_push_back_uses_harbin_identity`.

## 9 · Tunnel URL detection

Sub-spec 14 §2.1 step 5 uses a regex to extract the public URL from
devtunnel stdout. We match `https://[a-z0-9-]+\.[a-z0-9-]+\.devtunnels\.ms/?`
case-insensitively, and store the first match per session.

## 10 · Windows signal handling

Sub-spec 04 §4 documents the Proactor caveat. Our shutdown path uses
`asyncio.get_running_loop().add_signal_handler(...)` only when it's
available (POSIX); on Windows we install synchronous `signal.signal`
handlers for both `SIGINT` and `SIGBREAK`, each scheduling
`AppCore.request_shutdown()` via `loop.call_soon_threadsafe`.

## 11 · `HARBIN_HOME` precedence

`paths.ensure_*` uses `HARBIN_HOME` exclusively when set; platformdirs
is bypassed. Documented per overview §3.3 and project-layout §4.3.

## 12 · Apply-live config wiring

Sub-spec 03 §5.3 calls for live propagation of the apply-live subset
(`ui.log_verbosity`, `timezone`, `scheduler.tick_seconds`,
`agent_runner.{agent_cli,concurrency,kill_grace_seconds}`). We:

* watch `config.yaml` via `FileWatcher` from `AppCore`
* expose `AppCore.apply_live_config(new_cfg)` which calls
  `logging.set_level`, `Scheduler.update_tick/update_timezone`, and
  `AgentRunner.update_runtime_config` in turn
* the Config modal's Save button calls `apply_live_config` after a
  successful pydantic validation + atomic write.

Restart-required fields (theme, web bind, artifact root) are copied
into the in-memory `Config` snapshot but take effect on next launch.

## 13 · Global concurrency cap is resizable in-flight

The original implementation replaced the `asyncio.Semaphore` on resize,
which let the actual concurrent count exceed `global_cap` by up to N
for in-flight jobs. We replaced the semaphore with an inflight counter +
`asyncio.Condition`: in-flight jobs are not pre-empted, but new
acquisitions correctly block until `inflight < cap`. Tested in
`tests/integration/test_runner_concurrency.py`.

## 14 · Log-chunk seq generation is atomic per-job

Concurrent stdout/stderr/system writers used to TOCTOU on `MAX(seq)`.
`Store.append_log_chunks` now takes a per-job `asyncio.Lock` so the
SELECT+INSERT pair is serialized. Tested in
`tests/unit/test_db_concurrency.py`.

## 15 · Orphan job reaping at startup

Sub-spec 02 §4 mentions a startup sweep for log chunks. We extend that
to job status: `Store.reap_orphan_running` flips any
`queued`/`starting`/`running` row to `failed` (with `exit_code=-1`)
when harbin starts. Without this, a kill/crash leaves phantom rows
visible in the monitor.

## 16 · Watchdog thread safety

`watchdog.Observer` callbacks run on the observer's own thread.
`config/watch.py` now marshals every `asyncio` interaction (timer
schedule, timer cancellation, callback invocation) through
`loop.call_soon_threadsafe` so the loop's internal scheduler is never
mutated from a foreign thread. Tested in
`tests/unit/test_config_watch.py`.

## 17 · Fleet name regex

The original `^[a-z][a-z0-9-]{1,30}$` was tighter than typical
repository names (the public sample repo
`harbin-agent-sample-price-monitor` is 33 chars). We relaxed to
`^[a-z][a-z0-9-]{0,62}$` (max 63 chars, matching most ecosystems'
package/repo naming).

## 18 · Sample-fleet submodules

Sub-spec 06 §6 declares two sample fleets (`news`, `price-monitor`).
They live as **standalone GitHub repos** and are mirrored here as
submodules under `examples/` so contributors can read them without
leaving the harbin tree. Each contains an offline-runnable Python
agent so the integration test in
`tests/integration/test_sample_fleets.py` exercises the real
fleet → runner → artifact path end-to-end with no network or LLM
required.

Each sample exposes a `HARBIN_AGENT_MODE` env var with two values:

* `offline` — deterministic synthetic output. No network, no LLM,
  reproducible. **This is the canonical test mode**; harbin's
  `tests/conftest.py` pins it via an autouse fixture so the suite
  never tries to shell out to the real `copilot` binary.
* `copilot` — shell out to the GitHub Copilot CLI (`copilot`) on
  stdin and consume its stdout. Falls back to `offline` on any
  failure (missing binary, non-zero exit, timeout, empty/unparseable
  output) so the harbin job always produces a valid artifact.

When `HARBIN_AGENT_MODE` is unset, the agents auto-detect: `copilot`
when the binary is on PATH, otherwise `offline`. Additional knobs
(`HARBIN_COPILOT_BIN`, `HARBIN_COPILOT_TIMEOUT`) are documented in
each sample's README.

The earlier direct OpenAI / Anthropic Messages API integration in
the news sample (and the `HARBIN_PRICE_API_BASE` HTTP shortcut in
the price-monitor sample) were removed: harbin's design intent
(sub-spec 11) is that the **agent CLI** — typically `copilot` — IS
the LLM integration surface. Python-side HTTP shortcuts inside the
samples blurred that boundary.

## 19 · Push-back covers the whole dock tree

The original implementation of ``DockManager.push_back`` only
``git add``-ed the per-job ``artifact_dir`` (and only when that
directory happened to live inside the dock). In practice, samples
following the design (07 §4.1) write the **archive copy** into the
dock tree (e.g. ``briefs/``) while the canonical artifact lives
*outside* the dock under ``paths.artifact_root``. The old logic
therefore skipped push-back entirely for the news sample.

The fix: when ``push_back: true`` and we are on ``default_branch``,
run ``git add -A`` in the dock. If nothing was staged, the call
remains a clean no-op. Otherwise commit with the harbin identity and
push. Regression guards:

* ``tests/integration/test_push_back_broad.py`` — explicit unit-level
  checks that a dock-side file outside ``artifact_dir`` is staged
  and pushed; clean dock is a no-op.
* ``tests/integration/test_push_back_e2e.py`` — runs the news and
  price-monitor samples through the real ``AgentRunner`` and verifies
  the news sample's ``briefs/brief-*.md`` lands on ``origin/main``
  while the price-monitor sample (``push_back: false``) leaves the
  remote ref untouched.

## 20 · ``textual-serve`` API

The original sub-spec 14 §1 step 2 named ``Server(app_target=…)``,
which does not exist in the public ``textual-serve`` API as of the
version pinned in ``pyproject.toml`` (and the upstream README on
PyPI). The constructor takes a single ``command: str``; each
WebSocket connection spawns that command. The implementation uses
``Server(command="harbin", host=…, port=…)`` accordingly. Shared
backend state across browser sessions is provided by the SQLite WAL
mode (sub-spec 02 §1) rather than by sharing an in-memory ``AppCore``.

## 21 · Push-back safety — pre-dirty check

After §19 broadened ``git add -A`` to cover the whole dock tree, the
runner needed a guard against sweeping the operator's own uncommitted
edits into a harbin commit. ``_spawn_and_run`` now snapshots
``git status --porcelain`` immediately before spawning the agent. If
that snapshot was non-empty, the post-success branch skips push-back
and writes a warning to the job log (``push-back skipped: dock had
user-local changes before the job started``).

Regression guard: ``tests/integration/test_push_back_safety.py`` —
modifies a tracked file in the dock before enqueueing the news sample,
runs the agent, and asserts no commit happened locally or on the bare
remote.

If the snapshot itself fails (rare; e.g. permission error) the runner
treats the tree as dirty — fail-safe rather than fail-open.
