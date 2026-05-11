"""Top-level CLI dispatch for harbin (sub-spec 01 §3)."""

from __future__ import annotations

import argparse
import asyncio
import sys
from collections.abc import Sequence

from harbin import __version__


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="harbin",
        description="A minimalist command center for GitHub Copilot AI agents.",
    )
    p.add_argument("--version", action="version", version=f"harbin {__version__}")
    sub = p.add_subparsers(dest="cmd")
    serve = sub.add_parser("serve", help="run textual-serve")
    serve.add_argument("--port", type=int, default=None)
    serve.add_argument("--host", type=str, default=None)
    sf = sub.add_parser("sample-fleet", help="manage sample fleets")
    sf_sub = sf.add_subparsers(dest="sf_cmd")
    sf_add = sf_sub.add_parser("add", help="clone and register a sample fleet")
    sf_add.add_argument("name", help="sample name (news | price-monitor)")
    return p


def main(argv: Sequence[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(list(argv) if argv is not None else None)

    if args.cmd is None:
        return _run_tui()
    if args.cmd == "serve":
        return _run_serve(args.port, args.host)
    if args.cmd == "sample-fleet":
        if args.sf_cmd == "add":
            return _run_sample_add(args.name)
        parser.parse_args(["sample-fleet", "--help"])  # show help
        return 2
    parser.print_help()
    return 0


def _run_tui() -> int:
    return asyncio.run(_async_tui())


async def _async_tui() -> int:
    from harbin.app import AppCore
    from harbin.tui.app import HarbinApp

    core = await AppCore.startup()

    def console_writer(s: str) -> None:
        try:
            from harbin.tui.app import HarbinApp as _A

            inst = _A._instance
            if inst is not None:
                inst._write_console(s)
                return
        except Exception:
            pass
        print(s)

    core.set_console_writer(console_writer)
    ctx = core.make_context()
    app = HarbinApp(ctx)
    try:
        await app.run_async()
    finally:
        await core.shutdown()
    return 0


def _run_serve(port: int | None, host: str | None) -> int:
    """Run textual-serve.

    Note: ``textual-serve.Server`` spawns ``harbin`` as a subprocess per
    connection — it doesn't share AppCore. The serve process itself does
    not need AppCore.
    """
    from harbin.web.serve import serve

    cfg_port = port
    cfg_host = host
    if cfg_port is None or cfg_host is None:
        # We need defaults from config without spinning up full subsystems.
        from harbin.config.loader import load_config
        from harbin.paths import ensure_all, resolve

        paths = resolve()
        ensure_all(paths)
        cfg = load_config(paths.config_dir / "config.yaml")
        cfg_port = cfg_port or cfg.web.port
        cfg_host = cfg_host or cfg.web.host
    return serve(port=cfg_port, host=cfg_host)


def _run_sample_add(name: str) -> int:
    return asyncio.run(_async_sample_add(name))


async def _async_sample_add(name: str) -> int:
    from harbin.db.store import Store
    from harbin.errors import HarbinError
    from harbin.logging import setup as setup_logging
    from harbin.paths import ensure_all, resolve
    from harbin.samples import add_sample

    paths = resolve()
    ensure_all(paths)
    setup_logging(log_dir=paths.log_dir, level="info")
    store = await Store.open(paths.db_path)
    try:
        msg = await add_sample(name, paths=paths, store=store)
        print(msg)
        print("start harbin to use it.")
        return 0
    except HarbinError as e:
        print(f"error: {e.message}", file=sys.stderr)
        return 2 if e.code.startswith("user") else 3
    finally:
        await store.close()


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
