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
to `git commit` so the user's `~/.gitconfig` is not mutated.

## 9 · Tunnel URL detection

Sub-spec 14 §2.1 step 5 uses a regex to extract the public URL from
devtunnel stdout. We match `https://[a-z0-9-]+\.[a-z0-9-]+\.devtunnels\.ms/?`
case-insensitively, and store the first match per session.

## 10 · Windows signal handling

Sub-spec 04 §4 documents the Proactor caveat. Our shutdown path uses
`asyncio.get_running_loop().add_signal_handler(...)` only when it's
available (POSIX); on Windows we install a synchronous `signal.signal`
handler that schedules `AppCore.request_shutdown()` via
`loop.call_soon_threadsafe`.

## 11 · `HARBIN_HOME` precedence

`paths.ensure_*` uses `HARBIN_HOME` exclusively when set; platformdirs
is bypassed. Documented per overview §3.3 and project-layout §4.3.
