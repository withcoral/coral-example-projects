from __future__ import annotations

import asyncio
import json
import os
import shutil
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


class CoralMcpError(RuntimeError):
    """Raised when the local Coral MCP server cannot be started or queried."""


def load_coral_env() -> dict[str, str]:
    env = {key: value for key, value in os.environ.items() if value is not None}

    if not env.get("SLACK_TOKEN") and env.get("SLACK_BOT_TOKEN"):
        env["SLACK_TOKEN"] = env["SLACK_BOT_TOKEN"]

    return env


def detect_coral_mcp_args(coral_bin: str | None = None) -> list[str]:
    """Return the argv tail for starting Coral's stdio MCP server.

    Coral exposes a single `mcp-stdio` subcommand (since 0.3). This helper
    just validates the binary is on PATH and returns the canonical args
    so callers can stay decoupled from the exact subcommand name."""
    command = coral_bin or os.getenv("CORAL_BIN", "coral")
    if shutil.which(command) is None:
        raise CoralMcpError(f"Could not find Coral binary: {command}")
    return ["mcp-stdio"]


def _jsonable(value: Any) -> Any:
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json", exclude_none=True)
    if hasattr(value, "dict"):
        return value.dict(exclude_none=True)
    return value


def _serialize_content(items: Iterable[Any]) -> str:
    chunks: list[str] = []
    for item in items:
        item_type = getattr(item, "type", None)
        text = getattr(item, "text", None)
        if item_type == "text" and text is not None:
            chunks.append(text)
        else:
            chunks.append(json.dumps(_jsonable(item), indent=2, sort_keys=True))
    return "\n".join(chunks)


@dataclass(frozen=True)
class McpTool:
    name: str
    description: str
    input_schema: dict[str, Any]

    def to_anthropic_tool(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "input_schema": self.input_schema or {"type": "object", "properties": {}},
        }


class CoralMcpClient:
    def __init__(self, coral_bin: str | None = None):
        self.coral_bin = coral_bin or os.getenv("CORAL_BIN", "coral")
        self.mcp_args = detect_coral_mcp_args(self.coral_bin)

    async def list_tools(self) -> list[McpTool]:
        async with self._session() as session:
            response = await session.list_tools()
            return [
                McpTool(
                    name=tool.name,
                    description=tool.description or "",
                    input_schema=getattr(tool, "inputSchema", None)
                    or getattr(tool, "input_schema", None)
                    or {},
                )
                for tool in response.tools
            ]

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> str:
        async with self._session() as session:
            result = await session.call_tool(name, arguments)
            content = _serialize_content(result.content)
            if getattr(result, "isError", False):
                return f"Coral MCP tool error from {name}:\n{content}"
            return content

    async def smoke_test(self) -> str:
        tools = await self.list_tools()
        tool_names = ", ".join(tool.name for tool in tools)
        return f"Coral MCP is reachable. Tools: {tool_names}"

    def smoke_test_sync(self) -> str:
        return asyncio.run(self.smoke_test())

    def _server_params(self) -> StdioServerParameters:
        return StdioServerParameters(
            command=self.coral_bin,
            args=self.mcp_args,
            env=load_coral_env(),
        )

    def _session(self):
        return _CoralSession(self._server_params())


class _CoralSession:
    def __init__(self, params: StdioServerParameters):
        self.params = params
        self._stdio_context = None
        self._session_context = None
        self.session: ClientSession | None = None

    async def __aenter__(self) -> ClientSession:
        self._stdio_context = stdio_client(self.params)
        read, write = await self._stdio_context.__aenter__()
        self._session_context = ClientSession(read, write)
        self.session = await self._session_context.__aenter__()
        await self.session.initialize()
        return self.session

    async def __aexit__(self, exc_type, exc, tb) -> None:
        if self._session_context is not None:
            await self._session_context.__aexit__(exc_type, exc, tb)
        if self._stdio_context is not None:
            await self._stdio_context.__aexit__(exc_type, exc, tb)

