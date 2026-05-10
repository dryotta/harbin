"""harbin — A minimalist command center for GitHub Copilot AI agents."""

from __future__ import annotations

try:
    from importlib.metadata import PackageNotFoundError, version

    try:
        __version__ = version("harbin")
    except PackageNotFoundError:
        __version__ = "0.0.0+dev"
except Exception:
    __version__ = "0.0.0+dev"

__all__ = ["__version__"]
