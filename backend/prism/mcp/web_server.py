"""MCP-сервер «web»: поиск в интернете через self-hosted SearXNG и чтение страниц.

Инструменты:
  web_search(query, limit, language) — выдача SearXNG: заголовок, адрес, сниппет;
  fetch_page(url, max_chars)          — основной текст страницы (trafilatura), без меню и рекламы.

    SEARXNG_URL=http://localhost:8080 python -m prism.mcp.web_server --port 8001

Страницы читаются только из публичного интернета: адреса, что указывают во внутреннюю сеть
(localhost, 10.x, 192.168.x, метаданные облака), отклоняются — и на каждом редиректе тоже.
"""

from __future__ import annotations

import argparse
import asyncio
import ipaddress
import os
import socket
from urllib.parse import urljoin, urlparse

import httpx
import trafilatura
from mcp.server.fastmcp import FastMCP

SEARXNG_URL = os.environ.get("SEARXNG_URL", "http://localhost:8080")
USER_AGENT = "Mozilla/5.0 (compatible; PrismResearch/1.0; +https://github.com/Dissy-code/hackathon)"
MAX_BYTES = 3_000_000
MAX_REDIRECTS = 5
TIMEOUT = httpx.Timeout(12.0, connect=5.0)
# Сети, которые не публичные, но и не внутренние: 198.18.0.0/15 — «фейковые» адреса DNS у прокси и VPN-роутеров
# в режиме fake-IP (Clash, sing-box): за ними настоящие сайты. Список — через запятую в FETCH_ALLOW_NETS.
ALLOW_NETS = [ipaddress.ip_network(n.strip()) for n in os.environ.get("FETCH_ALLOW_NETS", "198.18.0.0/15").split(",")
              if n.strip()]
MIN_TEXT = 400

class BlockedURL(ValueError):
    pass


async def _check_public(url: str) -> None:
    parts = urlparse(url)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        raise BlockedURL(f"поддерживаются только http(s)-адреса: {url}")
    infos = await asyncio.get_running_loop().getaddrinfo(parts.hostname, parts.port or 443, type=socket.SOCK_STREAM)
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if not ip.is_global and not any(ip in net for net in ALLOW_NETS):
            raise BlockedURL(f"адрес во внутренней сети: {parts.hostname}")


async def web_search(query: str, limit: int = 8, language: str = "ru") -> dict:
    """Поиск в интернете. {"results": [...]} — до limit результатов: title, url, snippet, engine."""
    params = {"q": query, "format": "json", "language": language, "safesearch": 1}
    async with httpx.AsyncClient(timeout=TIMEOUT) as client:
        r = await client.get(f"{SEARXNG_URL.rstrip('/')}/search", params=params)
        r.raise_for_status()
        results = r.json().get("results", [])
    out, seen = [], set()
    for item in results:
        url = item.get("url")
        if not url or url in seen:
            continue
        seen.add(url)
        out.append({"title": item.get("title", ""), "url": url, "snippet": (item.get("content") or "")[:400],
                    "engine": item.get("engine", "")})
        if len(out) >= max(1, min(limit, 20)):
            break
    return {"query": query, "results": out}


async def fetch_page(url: str, max_chars: int = 8000) -> dict:
    """Основной текст веб-страницы (без навигации и рекламы): url, title, text, truncated."""
    async with httpx.AsyncClient(timeout=TIMEOUT, headers={"User-Agent": USER_AGENT},
                                 follow_redirects=False) as client:
        current = url
        for _ in range(MAX_REDIRECTS + 1):
            await _check_public(current)
            async with client.stream("GET", current) as r:
                if r.is_redirect and r.headers.get("location"):
                    current = urljoin(current, r.headers["location"])
                    continue
                r.raise_for_status()
                kind = r.headers.get("content-type", "")
                if "html" not in kind and "text" not in kind:
                    return {"url": current, "title": "", "text": "", "truncated": False,
                            "error": f"не текстовая страница ({kind or 'тип не указан'})"}
                body = bytearray()
                async for chunk in r.aiter_bytes():
                    body.extend(chunk)
                    if len(body) > MAX_BYTES:
                        break
                html = body.decode(r.encoding or "utf-8", errors="replace")
                break
        else:
            raise BlockedURL(f"слишком много редиректов: {url}")
    text = await asyncio.to_thread(trafilatura.extract, html, include_comments=False, include_tables=True,
                                   favor_precision=True) or ""
    if len(text) < MIN_TEXT:            # строгий режим отрезал почти всё — берём полнее, с риском захватить лишнее
        text = await asyncio.to_thread(trafilatura.extract, html, include_comments=False, include_tables=True,
                                       favor_recall=True) or text
    meta = await asyncio.to_thread(trafilatura.extract_metadata, html)
    limit = max(500, min(max_chars, 30000))
    return {"url": current, "title": (meta.title if meta and meta.title else ""), "text": text[:limit],
            "truncated": len(text) > limit}


def build(host: str = "127.0.0.1", port: int = 8001) -> FastMCP:
    # host задаётся при создании: на 127.0.0.1 FastMCP включает защиту от DNS rebinding (только localhost),
    # в docker-сети (0.0.0.0) к серверу обращаются по имени сервиса
    server = FastMCP("prism-web", host=host, port=port,
                     instructions="Поиск в интернете (SearXNG) и чтение страниц: web_search, затем fetch_page.")
    server.add_tool(web_search)
    server.add_tool(fetch_page)
    return server


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--host", default=os.environ.get("MCP_HOST", "127.0.0.1"))
    ap.add_argument("--port", type=int, default=int(os.environ.get("MCP_PORT", "8001")))
    args = ap.parse_args()
    build(args.host, args.port).run(transport="streamable-http")


if __name__ == "__main__":
    main()
