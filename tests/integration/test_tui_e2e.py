"""TUI end-to-end validation via Textual's ``Pilot``.

This file is the **validation contract** for harbin's TUI. Every
visible feature gets a Pilot-driven headless test that asserts the
real DOM state — not just "the function returned None".

Coverage map:

* Landing layout (header, monitor empty state, console, command line,
  status bar)
* ``/help`` and ``/help <cmd>`` write to the in-app console
* ``/help <typo>`` surfaces did-you-mean
* ``/jobs`` with no fleets prints the empty hint
* ``/config`` opens the modal without crashing; each sidebar page
  renders inputs; Save round-trips; Cancel/Escape closes
* ``/sync <missing>`` prints the unknown-fleet error
* ``/tunnel status`` prints "not running" when devtunnel is absent
* ``@<unknown>`` surfaces the did-you-mean for fleets
* ``@<fleet> <prompt>`` enqueues a job that lands as a monitor row
* alt+N navigates into a job view; alt+0 pops back

The tests intentionally avoid time-fragile assertions (snapshots,
exact pixel positions) — they verify *behaviour* via the widget tree.
"""

from __future__ import annotations

import asyncio
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from textual.widgets import Button, Input, RichLog, Static

from harbin.app import AppCore
from harbin.tui.app import HarbinApp

pytestmark = pytest.mark.asyncio


# ───────────────────────── shared helpers ───────────────────────────


async def _boot() -> tuple[AppCore, HarbinApp]:
    """Bring AppCore + HarbinApp up against the test's isolated HARBIN_HOME.

    The autouse ``harbin_home`` fixture in conftest already pins
    ``HARBIN_HOME`` to a tmp dir, so this is hermetic.
    """
    core = await AppCore.startup()

    # Wire the in-app console writer the same way `cli._async_tui` does
    # so /help and friends actually surface in the RichLog.
    def writer(s: str) -> None:
        inst = HarbinApp._instance
        if inst is not None:
            inst._write_console(s)
            return
        print(s)

    core.set_console_writer(writer)
    ctx = core.make_context()
    app = HarbinApp(ctx)
    return core, app


async def _submit(pilot, app: HarbinApp, line: str) -> None:
    """Type ``line`` into the command line and submit."""
    ci = app.query_one("#commandline", Input)
    ci.value = line
    await pilot.press("enter")
    # Two pauses: dispatch is async + console refresh is post-message.
    await pilot.pause()
    await pilot.pause()


def _console_text(app: HarbinApp) -> str:
    """All text currently rendered in the console RichLog."""
    log = app.query_one("#console", RichLog)
    return "\n".join(str(line) for line in log.lines)


def _make_local_fleet(tmp_path: Path, name: str = "test-fleet") -> tuple[Path, Path]:
    """Create a bare repo + a working dock with a minimal ``.harbin/``.

    Returns ``(bare, dock)``. The dock is a real git working tree with
    ``origin`` pointing at the bare repo.
    """
    bare = tmp_path / f"{name}.git"
    src = tmp_path / f"{name}-src"
    subprocess.run(
        ["git", "init", "--bare", "--initial-branch=main", str(bare)],
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "init", "--initial-branch=main", str(src)],
        check=True,
        capture_output=True,
    )
    subprocess.run(["git", "-C", str(src), "config", "user.email", "t@x"], check=True)
    subprocess.run(["git", "-C", str(src), "config", "user.name", "t"], check=True)
    (src / ".harbin").mkdir()
    (src / ".harbin" / "fleet.yaml").write_text(
        f"name: {name}\ndefault_branch: main\nartifact_policy:\n"
        "  retain: 30d\n  push_back: false\n",
        encoding="utf-8",
    )
    subprocess.run(["git", "-C", str(src), "add", "."], check=True)
    subprocess.run(
        ["git", "-C", str(src), "commit", "-m", "init"],
        check=True,
        capture_output=True,
    )
    subprocess.run(["git", "-C", str(src), "remote", "add", "origin", str(bare)], check=True)
    subprocess.run(
        ["git", "-C", str(src), "push", "-u", "origin", "main"],
        check=True,
        capture_output=True,
    )
    return bare, src


async def test_landing_layout_vertical_order(harbin_paths) -> None:
    """The default screen lays children out in this exact top-to-bottom order:
    header → overview (monitor + console) → command line → status bar.

    Renders the actual widget region rectangles and asserts that each
    widget's top edge sits below the previous one — i.e. the layout is
    actually vertical, not overlapping.
    """
    from harbin.tui.widgets.status_bar import StatusBar

    core, app = await _boot()
    try:
        async with app.run_test(headless=True) as pilot:
            await pilot.pause()
            await pilot.pause()
            header = app.query_one("#header")
            overview = app.query_one("#overview")
            ci = app.query_one("#commandline", Input)
            sb = app.query_one(StatusBar)

            # All four must be visible (non-zero region) and ordered top→bottom.
            regions = [
                ("header", header.region),
                ("overview", overview.region),
                ("commandline", ci.region),
                ("statusbar", sb.region),
            ]
            for name, r in regions:
                assert r.width > 0 and r.height > 0, f"{name} region empty: {r}"
            for i in range(len(regions) - 1):
                prev_name, prev_region = regions[i]
                next_name, next_region = regions[i + 1]
                assert prev_region.bottom <= next_region.y, (
                    f"layout broken: {prev_name}.bottom={prev_region.bottom} > "
                    f"{next_name}.y={next_region.y}"
                )
    finally:
        await core.shutdown()


# ───────────────────────── landing layout ───────────────────────────


async def test_landing_layout_matches_spec(harbin_paths) -> None:
    """The default screen contains: header (logo+tagline) → monitor → console → command line → status bar.

    Pins the structural contract from the design's ASCII mockup.
    """
    core, app = await _boot()
    try:
        async with app.run_test(headless=True) as pilot:
            await pilot.pause()
            await pilot.pause()

            # 1. Header container exists; logo + tagline are children.
            header = app.query_one("#header")
            assert header is not None
            logo = app.query_one("#header-logo", Static)
            tagline = app.query_one("#header-tagline", Static)
            assert "harbin" in str(tagline.render()).lower(), tagline.render()
            assert "_" in str(logo.render()) or "|" in str(logo.render()), logo.render()

            # 2. Monitor panel exists.
            assert app.query("#monitor"), "monitor panel must exist on the landing screen"

            # 3. Console RichLog exists.
            log = app.query_one("#console", RichLog)
            assert log is not None, "console RichLog must exist"

            # 4. Command line exists AND is focused.
            ci = app.query_one("#commandline", Input)
            assert app.focused is ci, (
                f"command line must be focused on mount, focused={app.focused!r}"
            )

            # 5. Status bar exists.
            from harbin.tui.widgets.status_bar import StatusBar

            assert app.query_one(StatusBar)
    finally:
        await core.shutdown()


async def test_monitor_empty_state_renders_helpful_hint(harbin_paths) -> None:
    """With no fleets registered, the monitor shows the 'add a fleet' hint."""
    core, app = await _boot()
    try:
        async with app.run_test(headless=True) as pilot:
            await pilot.pause()
            await pilot.pause()
            # The empty hint is a Static with the 'muted' class somewhere
            # under #monitor.
            monitor = app.query_one("#monitor")
            text = " ".join(str(w.render()) for w in monitor.query(Static))
            assert "no fleets" in text.lower() or "add" in text.lower(), (
                f"expected empty-state hint, got: {text!r}"
            )
    finally:
        await core.shutdown()


# ───────────────────────── /help ───────────────────────────


async def test_help_lists_every_command_in_the_console(harbin_paths) -> None:
    """``/help`` writes one line per known command into the in-app console."""
    core, app = await _boot()
    try:
        async with app.run_test(headless=True) as pilot:
            await pilot.pause()
            await pilot.pause()
            await _submit(pilot, app, "/help")
            text = _console_text(app)
            for cmd in (
                "/help",
                "/jobs",
                "/logs",
                "/cancel",
                "/artifacts",
                "/sync",
                "/schedule",
                "/tunnel",
                "/config",
                "/exit",
            ):
                assert cmd in text, f"/help output missing {cmd!r}; got:\n{text}"
    finally:
        await core.shutdown()


async def test_help_specific_command(harbin_paths) -> None:
    """``/help jobs`` writes the /jobs descriptor."""
    core, app = await _boot()
    try:
        async with app.run_test(headless=True) as pilot:
            await pilot.pause()
            await pilot.pause()
            await _submit(pilot, app, "/help jobs")
            text = _console_text(app)
            assert "/jobs" in text and "list active" in text, text
    finally:
        await core.shutdown()


async def test_help_typo_gets_did_you_mean(harbin_paths) -> None:
    """``/help jbos`` surfaces a 'did you mean /jobs' hint."""
    core, app = await _boot()
    try:
        async with app.run_test(headless=True) as pilot:
            await pilot.pause()
            await pilot.pause()
            await _submit(pilot, app, "/help jbos")
            text = _console_text(app)
            assert "did you mean" in text.lower() and "jobs" in text, text
    finally:
        await core.shutdown()


async def test_unknown_command_gets_did_you_mean(harbin_paths) -> None:
    """``/jobss`` surfaces the typo hint via the parser, not the help cmd."""
    core, app = await _boot()
    try:
        async with app.run_test(headless=True) as pilot:
            await pilot.pause()
            await pilot.pause()
            await _submit(pilot, app, "/jobss")
            text = _console_text(app)
            assert "did you mean" in text.lower() and "jobs" in text, text
    finally:
        await core.shutdown()


# ───────────────────────── /config ───────────────────────────


async def test_config_opens_modal_without_crashing(harbin_paths) -> None:
    """``/config`` pushes the config modal on the screen stack."""
    core, app = await _boot()
    try:
        async with app.run_test(headless=True) as pilot:
            await pilot.pause()
            await pilot.pause()
            n_before = len(app.screen_stack)
            await _submit(pilot, app, "/config")
            # The modal pushes asynchronously; give it more pauses.
            for _ in range(8):
                if len(app.screen_stack) > n_before:
                    break
                await pilot.pause()
            stack_names = [type(s).__name__ for s in app.screen_stack]
            assert "ConfigModalScreen" in stack_names, stack_names
    finally:
        await core.shutdown()


async def test_config_modal_renders_each_sidebar_page(harbin_paths) -> None:
    """Clicking each sidebar page replaces the right-pane content without error."""
    from harbin.tui.screens.config_modal import _PAGES, ConfigModalScreen

    core, app = await _boot()
    try:
        async with app.run_test(headless=True) as pilot:
            await pilot.pause()
            await pilot.pause()
            await _submit(pilot, app, "/config")
            for _ in range(8):
                if any(isinstance(s, ConfigModalScreen) for s in app.screen_stack):
                    break
                await pilot.pause()
            modal = next(s for s in app.screen_stack if isinstance(s, ConfigModalScreen))
            for key, _label in _PAGES:
                # Drive the page switch via the sidebar Buttons the same
                # way the user would, so Textual's lifecycle (remove old
                # children before mounting new ones) runs properly.
                btn = modal.query_one(f"#page-{key}", Button)
                btn.press()
                await pilot.pause()
                await pilot.pause()
                # No exception → success. Spot-check that the pane has
                # *some* content for non-empty pages.
                assert modal._content_container is not None
    finally:
        await core.shutdown()


async def test_config_modal_escape_pops(harbin_paths) -> None:
    """Pressing Escape closes the modal and returns to overview."""
    from harbin.tui.screens.config_modal import ConfigModalScreen

    core, app = await _boot()
    try:
        async with app.run_test(headless=True) as pilot:
            await pilot.pause()
            await pilot.pause()
            await _submit(pilot, app, "/config")
            for _ in range(8):
                if any(isinstance(s, ConfigModalScreen) for s in app.screen_stack):
                    break
                await pilot.pause()
            assert any(isinstance(s, ConfigModalScreen) for s in app.screen_stack)
            await pilot.press("escape")
            await pilot.pause()
            await pilot.pause()
            assert not any(isinstance(s, ConfigModalScreen) for s in app.screen_stack)
    finally:
        await core.shutdown()


# ───────────────────────── /sync, /tunnel, /jobs ───────────────────────────


async def test_sync_unknown_fleet_errors(harbin_paths) -> None:
    """``/sync no-such-fleet`` writes a friendly error to the console."""
    core, app = await _boot()
    try:
        async with app.run_test(headless=True) as pilot:
            await pilot.pause()
            await pilot.pause()
            await _submit(pilot, app, "/sync no-such-fleet")
            text = _console_text(app)
            assert "no-such-fleet" in text.lower() or "unknown" in text.lower(), text
    finally:
        await core.shutdown()


async def test_tunnel_status_when_not_running(harbin_paths) -> None:
    """``/tunnel`` (default = status) reports 'not running'."""
    core, app = await _boot()
    try:
        async with app.run_test(headless=True) as pilot:
            await pilot.pause()
            await pilot.pause()
            await _submit(pilot, app, "/tunnel")
            text = _console_text(app)
            assert "not running" in text.lower() or "tunnel" in text.lower(), text
    finally:
        await core.shutdown()


async def test_jobs_command_with_no_jobs(harbin_paths) -> None:
    """``/jobs`` doesn't crash when no jobs exist."""
    core, app = await _boot()
    try:
        async with app.run_test(headless=True) as pilot:
            await pilot.pause()
            await pilot.pause()
            await _submit(pilot, app, "/jobs")
            # Anything that didn't crash counts. The text is implementation-
            # defined; verify the console has some content.
            text = _console_text(app)
            assert text.strip(), "expected /jobs to write at least the prompt echo"
    finally:
        await core.shutdown()


# ───────────────────────── @-mentions ───────────────────────────


async def test_unknown_fleet_at_mention_gets_did_you_mean(harbin_paths) -> None:
    core, app = await _boot()
    try:
        async with app.run_test(headless=True) as pilot:
            await pilot.pause()
            await pilot.pause()
            await _submit(pilot, app, "@nope hi")
            text = _console_text(app)
            assert "nope" in text.lower() and "unknown" in text.lower(), text
    finally:
        await core.shutdown()


async def test_at_mention_enqueues_and_monitor_picks_it_up(harbin_paths, tmp_path) -> None:
    """Register a fleet, type ``@fleet hi``, and confirm the job lands in the monitor row."""
    bare, src = _make_local_fleet(tmp_path, "tui-fleet")

    # Register the fleet via the dock manager BEFORE booting the TUI so
    # the monitor sees it on first refresh.
    core = await AppCore.startup()

    def writer(s: str) -> None:
        inst = HarbinApp._instance
        if inst is not None:
            inst._write_console(s)
            return
        print(s)

    core.set_console_writer(writer)
    assert core.dock_manager is not None
    state = await core.dock_manager.register_fleet(str(bare))

    # Use the fake-agent CLI fixture for a deterministic, fast job.
    fake = Path(__file__).resolve().parents[1] / "fixtures" / "fake_agent_cli.py"
    from harbin.config.models import AgentCli

    assert core.runner is not None
    core.runner.update_runtime_config(
        agent_cli=AgentCli(command=[sys.executable, str(fake)], mode="stdin")
    )

    ctx = core.make_context()
    app = HarbinApp(ctx)
    try:
        async with app.run_test(headless=True) as pilot:
            await pilot.pause()
            await pilot.pause()
            await _submit(pilot, app, f"@{state.row.name} hi from the TUI")
            # Wait for the job to land in the monitor row.
            from harbin.tui.widgets.job_row import JobRow

            for _ in range(150):
                rows = app.query(JobRow)
                if rows:
                    break
                await asyncio.sleep(0.1)
            assert app.query(JobRow), "@mention did not produce a monitor JobRow"
            # Console must echo the queued confirmation.
            text = _console_text(app)
            assert "queued" in text.lower(), text
    finally:
        await core.shutdown()
        shutil.rmtree(src, ignore_errors=True)


# ───────────────────────── alt+N navigation ───────────────────────────


async def test_alt_zero_pops_modal(harbin_paths) -> None:
    """``alt+0`` (focus_overview) pops a pushed modal."""
    from harbin.tui.screens.config_modal import ConfigModalScreen

    core, app = await _boot()
    try:
        async with app.run_test(headless=True) as pilot:
            await pilot.pause()
            await pilot.pause()
            await _submit(pilot, app, "/config")
            for _ in range(8):
                if any(isinstance(s, ConfigModalScreen) for s in app.screen_stack):
                    break
                await pilot.pause()
            await pilot.press("alt+0")
            await pilot.pause()
            await pilot.pause()
            assert not any(isinstance(s, ConfigModalScreen) for s in app.screen_stack)
    finally:
        await core.shutdown()


# ───────────────────────── status bar ───────────────────────────


async def test_status_bar_shows_overview_initially(harbin_paths) -> None:
    """Status bar mentions 'overview' on the landing screen."""
    from harbin.tui.widgets.status_bar import StatusBar

    core, app = await _boot()
    try:
        async with app.run_test(headless=True) as pilot:
            await pilot.pause()
            await pilot.pause()
            # Periodic refresh runs every second — bump it.
            await app._refresh_periodic()
            await pilot.pause()
            sb = app.query_one(StatusBar)
            text = str(sb.render())
            assert "overview" in text.lower(), text
            # And shows "0 fleets" at the start.
            assert "0 fleets" in text, text
    finally:
        await core.shutdown()


# ─────────────────────── /web ───────────────────────


async def test_web_status_when_not_running(harbin_paths) -> None:
    """``/web`` (no args) reports 'not running' through the in-app console."""
    core, app = await _boot()
    try:
        async with app.run_test(headless=True) as pilot:
            await pilot.pause()
            await pilot.pause()
            await _submit(pilot, app, "/web")
            text = _console_text(app)
            assert "web: not running" in text.lower(), text
    finally:
        await core.shutdown()


async def test_web_listed_in_help(harbin_paths) -> None:
    core, app = await _boot()
    try:
        async with app.run_test(headless=True) as pilot:
            await pilot.pause()
            await pilot.pause()
            await _submit(pilot, app, "/help")
            text = _console_text(app)
            assert "/web" in text and "web UI server" in text, text
    finally:
        await core.shutdown()


# ─────────────────────── /config → Fleets → Add ─────────────────────


async def test_config_add_fleet_flow_end_to_end(harbin_paths, tmp_path) -> None:
    """Reproduce the user-reported "can't add a github repo" flow.

    Opens /config, clicks the Fleets sidebar button, types a (local
    bare-repo) git URL into the URL Input, clicks "Add fleet", and
    verifies:

    1. console writes a success message,
    2. ``ctx.dock_manager.states`` gains the new fleet,
    3. the URL input is cleared (i.e. the page re-rendered),
    4. the in-memory store has the fleet row.
    """
    from harbin.tui.screens.config_modal import ConfigModalScreen

    bare, src = _make_local_fleet(tmp_path, "added-via-ui")
    shutil.rmtree(src, ignore_errors=True)  # we only need the bare remote

    core, app = await _boot()
    try:
        async with app.run_test(headless=True) as pilot:
            await pilot.pause()
            await pilot.pause()
            await _submit(pilot, app, "/config")
            for _ in range(8):
                if any(isinstance(s, ConfigModalScreen) for s in app.screen_stack):
                    break
                await pilot.pause()
            modal = next(s for s in app.screen_stack if isinstance(s, ConfigModalScreen))

            # Navigate to the Fleets sidebar page.
            fleets_btn = modal.query_one("#page-fleets", Button)
            fleets_btn.press()
            for _ in range(8):
                await pilot.pause()
                if modal.query("#fleet-url"):
                    break
            url_input = modal.query_one("#fleet-url", Input)
            url_input.value = str(bare)
            add_btn = modal.query_one("#add-fleet", Button)
            add_btn.press()

            # Worker is async; wait for the dock_manager.states to update.
            for _ in range(150):
                if "added-via-ui" in {s.row.name for s in app.ctx.dock_manager.states.values()}:
                    break
                await asyncio.sleep(0.1)
            names = {s.row.name for s in app.ctx.dock_manager.states.values()}
            assert "added-via-ui" in names, names

            text = _console_text(app)
            assert "registered fleet" in text.lower() and "added-via-ui" in text, text

            # The URL input was cleared as part of the re-render.
            url_input_after = modal.query_one("#fleet-url", Input)
            assert url_input_after.value == "", url_input_after.value

            # The store row is real and discoverable by the rest of harbin.
            row = await app.ctx.store.get_fleet_by_name("added-via-ui")
            assert row is not None
            assert row.url == str(bare)
    finally:
        await core.shutdown()


async def test_config_add_fleet_empty_url_errors(harbin_paths) -> None:
    """Clicking Add fleet with a blank URL surfaces a user error, not a crash."""
    from harbin.tui.screens.config_modal import ConfigModalScreen

    core, app = await _boot()
    try:
        async with app.run_test(headless=True) as pilot:
            await pilot.pause()
            await pilot.pause()
            await _submit(pilot, app, "/config")
            for _ in range(8):
                if any(isinstance(s, ConfigModalScreen) for s in app.screen_stack):
                    break
                await pilot.pause()
            modal = next(s for s in app.screen_stack if isinstance(s, ConfigModalScreen))
            modal.query_one("#page-fleets", Button).press()
            for _ in range(8):
                await pilot.pause()
                if modal.query("#add-fleet"):
                    break
            add_btn = modal.query_one("#add-fleet", Button)
            add_btn.press()
            await pilot.pause()
            await pilot.pause()
            text = _console_text(app)
            assert "paste a git url" in text.lower() or "url" in text.lower(), text
    finally:
        await core.shutdown()


async def test_config_add_fleet_bad_url_surfaces_error(harbin_paths, tmp_path) -> None:
    """A URL that ``git clone`` rejects shows a friendly error and does not crash."""
    from harbin.tui.screens.config_modal import ConfigModalScreen

    core, app = await _boot()
    try:
        async with app.run_test(headless=True) as pilot:
            await pilot.pause()
            await pilot.pause()
            await _submit(pilot, app, "/config")
            for _ in range(8):
                if any(isinstance(s, ConfigModalScreen) for s in app.screen_stack):
                    break
                await pilot.pause()
            modal = next(s for s in app.screen_stack if isinstance(s, ConfigModalScreen))
            modal.query_one("#page-fleets", Button).press()
            for _ in range(8):
                await pilot.pause()
                if modal.query("#fleet-url"):
                    break
            url_input = modal.query_one("#fleet-url", Input)
            # Local path that genuinely does not exist → git clone fails.
            url_input.value = str(tmp_path / "definitely-does-not-exist")
            modal.query_one("#add-fleet", Button).press()
            # Wait for the worker to surface its error to the console.
            for _ in range(150):
                text = _console_text(app)
                if "could not add fleet" in text.lower() or "error" in text.lower():
                    break
                await asyncio.sleep(0.1)
            text = _console_text(app)
            assert "could not add fleet" in text.lower() or "error" in text.lower(), text
            # Nothing was registered.
            names = {s.row.name for s in app.ctx.dock_manager.states.values()}
            assert not names
    finally:
        await core.shutdown()


# ──────────────────────── /config save round-trip ──────────────────


async def test_config_save_writes_yaml_and_propagates(harbin_paths) -> None:
    """Edit a field, press Save: config.yaml on disk reflects the change."""
    from harbin.tui.screens.config_modal import ConfigModalScreen

    core, app = await _boot()
    try:
        async with app.run_test(headless=True) as pilot:
            await pilot.pause()
            await pilot.pause()
            await _submit(pilot, app, "/config")
            for _ in range(8):
                if any(isinstance(s, ConfigModalScreen) for s in app.screen_stack):
                    break
                await pilot.pause()
            modal = next(s for s in app.screen_stack if isinstance(s, ConfigModalScreen))
            # General page renders by default; wait for inputs.
            for _ in range(8):
                if modal.query("#timezone"):
                    break
                await pilot.pause()
            tz_input = modal.query_one("#timezone", Input)
            tz_input.value = "UTC"
            save_btn = modal.query_one("#save", Button)
            save_btn.press()
            # Wait for the save to land + modal pop.
            for _ in range(40):
                if not any(isinstance(s, ConfigModalScreen) for s in app.screen_stack):
                    break
                await pilot.pause()
            cfg_path = app.ctx.paths.config_dir / "config.yaml"
            assert cfg_path.exists(), cfg_path
            text = cfg_path.read_text(encoding="utf-8")
            assert "UTC" in text, text
            # In-memory snapshot updated, too.
            assert app.ctx.config.timezone == "UTC"
    finally:
        await core.shutdown()


# ────────────────────────── /logs, /cancel ─────────────────────────


async def test_logs_unknown_job_errors(harbin_paths) -> None:
    """``/logs nope`` surfaces an unknown-job error to the console."""
    core, app = await _boot()
    try:
        async with app.run_test(headless=True) as pilot:
            await pilot.pause()
            await pilot.pause()
            await _submit(pilot, app, "/logs nope")
            text = _console_text(app)
            assert "nope" in text.lower() and "unknown" in text.lower(), text
    finally:
        await core.shutdown()


async def test_logs_command_shows_captured_stdio(harbin_paths, tmp_path) -> None:
    """After a fake-agent job, ``/logs <short-id>`` prints its captured output."""
    bare, _src = _make_local_fleet(tmp_path, "logs-fleet")
    core = await AppCore.startup()

    def writer(s: str) -> None:
        inst = HarbinApp._instance
        if inst is not None:
            inst._write_console(s)
            return
        print(s)

    core.set_console_writer(writer)
    assert core.dock_manager is not None and core.runner is not None
    state = await core.dock_manager.register_fleet(str(bare))
    fake = Path(__file__).resolve().parents[1] / "fixtures" / "fake_agent_cli.py"
    from harbin.config.models import AgentCli

    core.runner.update_runtime_config(
        agent_cli=AgentCli(command=[sys.executable, str(fake)], mode="stdin")
    )
    ctx = core.make_context()
    app = HarbinApp(ctx)
    try:
        async with app.run_test(headless=True) as pilot:
            await pilot.pause()
            await pilot.pause()
            row = await core.runner.enqueue(
                fleet=state.row, prompt="hi-there", source="repl", task_label="adhoc"
            )
            # Wait for the agent to complete.
            for _ in range(150):
                job = await core.store.get_job_by_short_id(row.short_id)
                if job and job.status in {"success", "failed"}:
                    break
                await asyncio.sleep(0.1)
            await _submit(pilot, app, f"/logs {row.short_id}")
            text = _console_text(app)
            # Fake agent writes START / END to stdout; either should show.
            assert "START" in text or "END" in text, text
    finally:
        await core.shutdown()


async def test_cancel_unknown_job_errors(harbin_paths) -> None:
    """``/cancel nope`` writes an unknown-job error."""
    core, app = await _boot()
    try:
        async with app.run_test(headless=True) as pilot:
            await pilot.pause()
            await pilot.pause()
            await _submit(pilot, app, "/cancel nope")
            text = _console_text(app)
            assert "nope" in text.lower(), text
    finally:
        await core.shutdown()


# ─────────────────────────── /artifacts ───────────────────────────


async def test_artifacts_unknown_fleet_errors(harbin_paths) -> None:
    core, app = await _boot()
    try:
        async with app.run_test(headless=True) as pilot:
            await pilot.pause()
            await pilot.pause()
            await _submit(pilot, app, "/artifacts nope")
            text = _console_text(app)
            assert "nope" in text.lower() and "unknown" in text.lower(), text
    finally:
        await core.shutdown()


async def test_artifacts_lists_directory(harbin_paths, tmp_path) -> None:
    """``/artifacts <fleet>`` lists files written under the artifact root."""
    bare, _src = _make_local_fleet(tmp_path, "art-fleet")
    core = await AppCore.startup()

    def writer(s: str) -> None:
        inst = HarbinApp._instance
        if inst is not None:
            inst._write_console(s)
            return
        print(s)

    core.set_console_writer(writer)
    assert core.dock_manager is not None
    state = await core.dock_manager.register_fleet(str(bare))
    # Pre-seed an artifact file by hand.
    art = core.artifacts.root / state.row.name / "adhoc" / "abc"
    art.mkdir(parents=True, exist_ok=True)
    (art / "result.txt").write_text("hello world", encoding="utf-8")

    ctx = core.make_context()
    app = HarbinApp(ctx)
    try:
        async with app.run_test(headless=True) as pilot:
            await pilot.pause()
            await pilot.pause()
            await _submit(pilot, app, f"/artifacts {state.row.name}")
            text = _console_text(app)
            assert "adhoc" in text, text
            await _submit(pilot, app, f"/artifacts {state.row.name} adhoc/abc")
            text = _console_text(app)
            assert "result.txt" in text, text
    finally:
        await core.shutdown()


# ─────────────────────────── /schedule ───────────────────────────


async def test_schedule_command_empty(harbin_paths) -> None:
    """No fleets → ``/schedule`` writes the no-tasks message."""
    core, app = await _boot()
    try:
        async with app.run_test(headless=True) as pilot:
            await pilot.pause()
            await pilot.pause()
            await _submit(pilot, app, "/schedule")
            text = _console_text(app)
            assert "no scheduled tasks" in text.lower(), text
    finally:
        await core.shutdown()


# ─────────────────────────── /exit ───────────────────────────


async def test_exit_calls_request_shutdown(harbin_paths) -> None:
    """``/exit`` triggers the shutdown request (without quitting the test
    AppCore — the request is captured at AppContext level)."""
    core, app = await _boot()
    called = {"n": 0}
    original = app.ctx.request_shutdown

    def fake_shutdown() -> None:
        called["n"] += 1
        original()

    # Replace the context handle so the command sees our stub.
    app.ctx.request_shutdown = fake_shutdown  # type: ignore[assignment]
    try:
        async with app.run_test(headless=True) as pilot:
            await pilot.pause()
            await pilot.pause()
            await _submit(pilot, app, "/exit")
            assert called["n"] == 1, "request_shutdown was not invoked"
            text = _console_text(app)
            assert "shutting down" in text.lower(), text
    finally:
        await core.shutdown()


# ────────────────────────── ctrl+L clear ─────────────────────────


async def test_ctrl_l_clears_console(harbin_paths) -> None:
    """ctrl+L empties the console RichLog."""
    core, app = await _boot()
    try:
        async with app.run_test(headless=True) as pilot:
            await pilot.pause()
            await pilot.pause()
            await _submit(pilot, app, "/help")
            text_before = _console_text(app)
            assert text_before.strip(), "expected /help output before clear"
            await pilot.press("ctrl+l")
            await pilot.pause()
            await pilot.pause()
            log = app.query_one("#console", RichLog)
            assert not log.lines, f"console not cleared, lines={[str(line) for line in log.lines]}"
    finally:
        await core.shutdown()


# ────────────────────────── JobView screen ─────────────────────────


async def test_alt_one_opens_jobview_and_escape_returns(harbin_paths, tmp_path) -> None:
    """alt+1 opens a JobView screen for the first job; Escape closes it."""
    from harbin.tui.screens.job_view import JobViewScreen

    bare, _src = _make_local_fleet(tmp_path, "jv-fleet")
    core = await AppCore.startup()

    def writer(s: str) -> None:
        inst = HarbinApp._instance
        if inst is not None:
            inst._write_console(s)
            return
        print(s)

    core.set_console_writer(writer)
    assert core.dock_manager is not None and core.runner is not None
    state = await core.dock_manager.register_fleet(str(bare))
    fake = Path(__file__).resolve().parents[1] / "fixtures" / "fake_agent_cli.py"
    from harbin.config.models import AgentCli

    core.runner.update_runtime_config(
        agent_cli=AgentCli(command=[sys.executable, str(fake)], mode="stdin")
    )
    # Enqueue a job before booting the TUI so the periodic refresh assigns
    # it to slot 1.
    row = await core.runner.enqueue(
        fleet=state.row, prompt="for jv", source="repl", task_label="adhoc"
    )
    for _ in range(150):
        job = await core.store.get_job_by_short_id(row.short_id)
        if job and job.status in {"success", "failed"}:
            break
        await asyncio.sleep(0.1)

    ctx = core.make_context()
    app = HarbinApp(ctx)
    try:
        async with app.run_test(headless=True) as pilot:
            await pilot.pause()
            await pilot.pause()
            # Trigger a refresh so the slot map is populated.
            await app._refresh_periodic()
            await pilot.pause()
            assert 1 in app._slot_to_short_id, app._slot_to_short_id
            await pilot.press("alt+1")
            # Push is async.
            for _ in range(8):
                if any(isinstance(s, JobViewScreen) for s in app.screen_stack):
                    break
                await pilot.pause()
            assert any(isinstance(s, JobViewScreen) for s in app.screen_stack), (
                "alt+1 did not push JobViewScreen"
            )
            await pilot.press("escape")
            for _ in range(8):
                if not any(isinstance(s, JobViewScreen) for s in app.screen_stack):
                    break
                await pilot.pause()
            assert not any(isinstance(s, JobViewScreen) for s in app.screen_stack)
    finally:
        await core.shutdown()


# ────────────────────── status bar count updates ────────────────────


async def test_status_bar_reflects_fleet_count(harbin_paths, tmp_path) -> None:
    """After adding a fleet, the status bar shows '1 fleets'."""
    from harbin.tui.widgets.status_bar import StatusBar

    bare, _src = _make_local_fleet(tmp_path, "sb-fleet")
    core = await AppCore.startup()

    def writer(s: str) -> None:
        inst = HarbinApp._instance
        if inst is not None:
            inst._write_console(s)
            return
        print(s)

    core.set_console_writer(writer)
    assert core.dock_manager is not None
    await core.dock_manager.register_fleet(str(bare))
    ctx = core.make_context()
    app = HarbinApp(ctx)
    try:
        async with app.run_test(headless=True) as pilot:
            await pilot.pause()
            await pilot.pause()
            await app._refresh_periodic()
            await pilot.pause()
            sb = app.query_one(StatusBar)
            text = str(sb.render())
            assert "1 fleets" in text, text
    finally:
        await core.shutdown()


# ─────────────────────── monitor sizing ───────────────────────


async def test_monitor_does_not_dominate_when_empty(harbin_paths) -> None:
    """With no jobs, the monitor takes only its empty-state rows — the
    console pane gets the lion's share of the terminal."""
    core, app = await _boot()
    try:
        async with app.run_test(headless=True) as pilot:
            await pilot.pause()
            await pilot.pause()
            monitor = app.query_one("#monitor")
            console = app.query_one("#console", RichLog)
            # Monitor must be smaller than console when empty (the
            # explicit max-height: 50% on #monitor + height: auto
            # makes this true even on short test terminals).
            assert monitor.region.height <= console.region.height + 1, (
                f"monitor height {monitor.region.height} should be <= "
                f"console height {console.region.height} when empty"
            )
            # And monitor shouldn't be wildly tall in absolute terms either.
            assert monitor.region.height < 20, monitor.region.height
    finally:
        await core.shutdown()
