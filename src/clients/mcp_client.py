"""Thin async helper for connecting to one of our MCP servers over stdio.

Usage:
    async with mcp_session("paper_parser") as session:
        result = await session.call_tool("parse_pdf", {"path": "x.pdf"})
        # result.content is a list of MCP content blocks; .data gives the parsed value

The server is launched as a subprocess (the official MCP stdio pattern).
"""

from __future__ import annotations

import json
import sys
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

SERVER_MODULES = {
    "paper_parser": "src.mcp_servers.paper_parser",
    # phase 3 servers will be added here
}


@asynccontextmanager
async def mcp_session(server_name: str) -> AsyncIterator[ClientSession]:
    if server_name not in SERVER_MODULES:
        raise ValueError(f"unknown MCP server: {server_name}. options: {list(SERVER_MODULES)}")
    params = StdioServerParameters(
        command=sys.executable,
        args=["-m", SERVER_MODULES[server_name]],
        env=None,
    )
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            yield session


def unwrap_tool_result(result: Any) -> Any:
    """Pull a Python value out of an MCP CallToolResult.

    Prefers `structuredContent` (FastMCP sets this for typed returns) which
    wraps non-object returns under `{"result": ...}`. Falls back to parsing
    the first text content block.
    """
    structured = getattr(result, "structuredContent", None)
    if structured is not None:
        if isinstance(structured, dict) and set(structured.keys()) == {"result"}:
            return structured["result"]
        return structured

    if not result.content:
        return None
    block = result.content[0]
    text = getattr(block, "text", None)
    if text is None:
        return block
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        return text
    if isinstance(parsed, dict) and set(parsed.keys()) == {"result"}:
        return parsed["result"]
    return parsed
