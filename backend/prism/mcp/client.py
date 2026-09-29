"""Клиент MCP-серверов для узлов графа: инструменты подключаются через langchain-mcp-adapters.

    hub = McpHub({"web": "http://mcp-web:8001/mcp", "pptx": "http://mcp-pptx:8002/mcp"})
    results = await hub.call("web", "web_search", query="…")

Инструменты — обычные LangChain-инструменты: их можно и вызывать напрямую из узла (так делает research),
и отдать агенту LangGraph (hub.tools("pptx")) — модель сама решит, что вызвать.
"""

from __future__ import annotations

import json
from typing import Any

from langchain_core.tools import BaseTool
from langchain_mcp_adapters.client import MultiServerMCPClient


class McpError(RuntimeError):
    pass


class McpHub:
    def __init__(self, servers: dict[str, str]):
        self.servers = {name: url for name, url in servers.items() if url}
        self._client = MultiServerMCPClient(
            {name: {"transport": "streamable_http", "url": url} for name, url in self.servers.items()})
        self._tools: dict[str, dict[str, BaseTool]] = {}

    def has(self, server: str) -> bool:
        return server in self.servers

    async def tools(self, server: str) -> dict[str, BaseTool]:
        if server not in self.servers:
            raise McpError(f"MCP-сервер {server!r} не настроен (MCP_{server.upper()}_URL)")
        if server not in self._tools:
            try:
                tools = await self._client.get_tools(server_name=server)
            except Exception as e:                      # сеть, сервер не поднят — узел решает, что делать
                raise McpError(f"MCP-сервер {server!r} недоступен: {e}") from e
            self._tools[server] = {t.name: t for t in tools}
        return self._tools[server]

    async def call(self, server: str, tool: str, **args: Any) -> Any:
        """Вызов инструмента; ответ — разобранный JSON (dict/list), если сервер вернул структуру."""
        tools = await self.tools(server)
        if tool not in tools:
            raise McpError(f"у MCP-сервера {server!r} нет инструмента {tool!r}")
        try:
            result = await tools[tool].ainvoke(args)
        except Exception as e:
            raise McpError(f"{server}.{tool}: {e}") from e
        value = _unwrap(result)
        if isinstance(value, str) and value.startswith("Error executing tool"):
            raise McpError(f"{server}.{tool}: {value.split(':', 1)[-1].strip()}")   # ошибка сервера приходит текстом
        return value


def _unwrap(result: Any) -> Any:
    """Адаптер отдаёт текст или список content-блоков; FastMCP кладёт в текст JSON результата."""
    if isinstance(result, tuple):                       # (content, artifact)
        result = result[0]
    if isinstance(result, list):
        texts = [b.get("text", "") if isinstance(b, dict) else getattr(b, "text", str(b)) for b in result]
        if len(texts) > 1:
            parsed = [_json(t) for t in texts]
            return parsed
        result = texts[0] if texts else ""
    return _json(result) if isinstance(result, str) else result


def _json(text: str) -> Any:
    try:
        return json.loads(text)
    except (json.JSONDecodeError, TypeError):
        return text
