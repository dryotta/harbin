"""`harbin serve` — textual-serve binding (sub-spec 14 §1)."""

from __future__ import annotations

import sys
from typing import TYPE_CHECKING

from harbin.logging import get_logger

if TYPE_CHECKING:  # pragma: no cover
    pass

_log = get_logger("web.serve")


def serve(port: int, host: str) -> int:
    """Start the textual-serve HTTP/WebSocket server. Blocks until shutdown."""
    try:
        from textual_serve.server import Server
    except ImportError as e:  # pragma: no cover - tested transitively
        _log.error("textual-serve not installed: %s", e)
        return 2

    command = "harbin"
    server = Server(command=command, host=host, port=port)
    sys.stderr.write(f"harbin serving on http://{host}:{port}/\n")
    if host not in {"127.0.0.1", "localhost", "::1"}:
        sys.stderr.write(
            "warning: serving on a non-loopback interface without authentication.\n"
            "prefer 'harbin' (loopback) + '/tunnel start' (devtunnel). "
            "see doc/remote-access.md.\n"
        )
    try:
        server.serve()
    except KeyboardInterrupt:
        return 130
    return 0
