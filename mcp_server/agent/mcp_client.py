"""MCP client adapter for streamable HTTP. Connects through the gateway, so every
call and listing is logged by the detector like any other agent's."""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Any, AsyncIterator

from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client

from .loop import ToolError


class McpToolAdapter:
    def __init__(self, session: ClientSession) -> None:
        self.session = session

    async def list_tools(self) -> list[dict[str, Any]]:
        result = await self.session.list_tools()
        return [{"name": t.name, "description": t.description or "",
                 "inputSchema": t.inputSchema or {}} for t in result.tools]

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> str:
        result = await self.session.call_tool(name, arguments)
        text = "\n".join(c.text for c in result.content if getattr(c, "text", None) is not None)
        if result.isError:
            raise ToolError(text or "tool reported an error")
        return text


@asynccontextmanager
async def connect(url: str, consumer: str, token: str = "") -> AsyncIterator[McpToolAdapter]:
    headers = {"X-Consumer": consumer}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    async with streamablehttp_client(url, headers=headers) as (read, write, _):
        async with ClientSession(read, write) as session:
            await session.initialize()
            yield McpToolAdapter(session)
