"""Gemini Web MCP package metadata."""

from importlib.metadata import PackageNotFoundError, version

DISTRIBUTION_NAME = "gemini-mcp-server"

try:
    __version__ = version(DISTRIBUTION_NAME)
except PackageNotFoundError:
    # A source tree can be imported before installation.  Do not duplicate the
    # release version here; installed distributions always use package metadata.
    __version__ = "0+unknown"


__all__ = ["DISTRIBUTION_NAME", "__version__"]
