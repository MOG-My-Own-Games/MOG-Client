#!/usr/bin/env python3
"""Dev-loop MCP server for MOG-Client.

Wraps the CLI's own commands as MCP tools, so an agent can drive a running
MOG-Server for end-to-end testing without hand-typing CLI invocations. Not a
product surface - keep it small.
"""

from __future__ import annotations

import os

from mcp.server.fastmcp import FastMCP

from mog_client.cli import Client, MogClient

mcp = FastMCP("mog-client-dev")

_BASE = os.getenv("MOG_SERVER_URL", "http://localhost:5000")
_USER = os.getenv("MOG_MCP_USER", "admin")
_PASS = os.getenv("MOG_MCP_PASS", "")


def _client() -> MogClient:
    return MogClient(Client(_BASE, _USER, _PASS))


@mcp.tool()
def candidates(game_id: int) -> dict:
    """Detect installer candidates for a game."""
    return _client().candidates(game_id)


@mcp.tool()
def start_install(game_id: int, installer_path: str | None = None, auto_mode: bool = False) -> dict:
    """Start (or return the running) install session for a game."""
    return _client().start_session(game_id, installer_path, None, None, auto_mode or None, None)


@mcp.tool()
def install_status(game_id: int) -> dict:
    """Latest install session state for a game."""
    return _client().get_session(game_id)


@mcp.tool()
def cancel_install(game_id: int) -> dict:
    """Cancel the running install session for a game."""
    return _client().cancel_session(game_id)


if __name__ == "__main__":
    mcp.run()
