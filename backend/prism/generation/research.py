"""Поиск материалов в интернете для короткого брифа (узел research графа).

Бриф «компания Nestle» не даёт ни одной цифры, и без поиска колода вышла бы общими словами. Поэтому:

    researcher (скилл)  -> 2–3 поисковых запроса
    MCP web.web_search  -> выдача SearXNG по каждому запросу (параллельно)
    MCP web.fetch_page  -> основной текст лучших страниц (разные сайты, параллельно)
    research_digest     -> короткие факты с источником -> materials для планировщика и писателя

Всё укладывается в бюджет времени (research.budget_s): не успели или MCP недоступен — генерация идёт
по одному брифу, а пользователь видит предупреждение.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import re
from urllib.parse import urlparse

from pydantic import BaseModel, Field

from prism.config import ResearchConfig
from prism.llm.client import LLMFactory
from prism.llm.structured import StructuredOutputError, ainvoke_structured
from prism.mcp.client import McpError, McpHub
from prism.skills.registry import SkillRegistry

# сайты, где текста для фактов нет или его не достать
_SKIP_DOMAINS = ("youtube.com", "youtu.be", "pinterest.", "instagram.com", "facebook.com", "tiktok.com",
                 "vk.com/video", "twitter.com", "x.com", "t.me")
MIN_PAGE_CHARS = 400
PAGE_TIMEOUT_S = 15


class ResearchPlan(BaseModel):
    topic: str
    queries: list[str] = Field(min_length=1, max_length=5)


class Fact(BaseModel):
    text: str
    source: str


class Digest(BaseModel):
    facts: list[Fact] = Field(default_factory=list, max_length=20)


def needs_research(brief: str, cfg: ResearchConfig) -> bool:
    """Короткий бриф почти без чисел — материалов нет, их надо искать."""
    words = len(brief.split())
    numbers = len(re.findall(r"\d+(?:[.,]\d+)?", brief))
    return words < cfg.min_brief_words and numbers < 3


def _domain(url: str) -> str:
    host = urlparse(url).hostname or ""
    return host.removeprefix("www.")


def _pick_pages(result_lists: list[list[dict]], limit: int) -> list[dict]:
    """По очереди из выдачи каждого запроса, не больше одной страницы с сайта."""
    picked, domains = [], set()
    depth = max((len(r) for r in result_lists), default=0)
    for i in range(depth):
        for results in result_lists:
            if i >= len(results) or len(picked) >= limit:
                continue
            r = results[i]
            d = _domain(r.get("url", ""))
            if not d or d in domains or any(s in r["url"] for s in _SKIP_DOMAINS):
                continue
            domains.add(d)
            picked.append(r)
    return picked


async def research(brief: str, language: str, hub: McpHub, llm: LLMFactory, skills: SkillRegistry,
                   cfg: ResearchConfig, emit) -> dict:
    """{"materials": [...], "sources": [...], "warnings": [...], "skills": [...]}."""
    planner, digest = skills.get("researcher"), skills.get("research_digest")
    model = llm.for_role(planner.role)
    out: dict = {"materials": [], "sources": [], "warnings": [], "skills": [planner.manifest_entry(),
                                                                          digest.manifest_entry()]}
    try:
        async with asyncio.timeout(cfg.budget_s):
            emit("ищем материалы в интернете", 0.02)
            plan = await ainvoke_structured(model, planner.render(
                brief=brief, language=language, max_queries=cfg.max_queries,
                today=dt.datetime.now(dt.UTC).date().isoformat()), ResearchPlan, retries=1)
            queries = plan.queries[: cfg.max_queries]

            async def search(q: str) -> list[dict]:
                lang = "ru" if re.search(r"[а-яё]", q, re.IGNORECASE) else "all"
                try:
                    return (await hub.call("web", "web_search", query=q, limit=8, language=lang)).get("results", [])
                except (McpError, AttributeError) as e:
                    out["warnings"].append(f"поиск «{q}»: {e}")
                    return []

            found = await asyncio.gather(*(search(q) for q in queries))
            candidates = _pick_pages(list(found), cfg.max_pages + 2)       # запас на страницы, что не откроются
            emit(f"читаем источники: {len(candidates)}", 0.05)

            skipped: list[str] = []

            async def fetch(r: dict) -> dict | None:
                try:
                    async with asyncio.timeout(PAGE_TIMEOUT_S):
                        page = await hub.call("web", "fetch_page", url=r["url"], max_chars=cfg.page_chars)
                except TimeoutError:
                    skipped.append(f"{_domain(r['url'])}: не ответил за {PAGE_TIMEOUT_S} с")
                    return None
                except McpError as e:
                    skipped.append(f"{_domain(r['url'])}: {str(e).split(': ', 1)[-1][:80]}")
                    return None
                if not isinstance(page, dict) or len(page.get("text", "")) < MIN_PAGE_CHARS:
                    skipped.append(f"{_domain(r['url'])}: мало текста")
                    return None
                return {"title": page.get("title") or r.get("title", ""), "url": page.get("url", r["url"]),
                        "text": page["text"]}

            pages = [p for p in await asyncio.gather(*(fetch(r) for r in candidates)) if p][: cfg.max_pages]
            if not pages:
                why = "; ".join(skipped[:4]) or "поиск ничего не нашёл"
                out["warnings"].append(f"поиск не дал читаемых страниц ({why}) — колода по брифу")
                return out
            for i, p in enumerate(pages, 1):
                p["id"] = f"p{i}"
            emit("выбираем факты", 0.08)
            facts = (await ainvoke_structured(model, digest.render(
                brief=brief, language=language, topic=plan.topic, max_facts=15, pages=pages),
                Digest, retries=1)).facts
    except TimeoutError:
        out["warnings"].append(f"поиск не уложился в {cfg.budget_s:.0f} с — колода по брифу")
        return out
    except (McpError, StructuredOutputError) as e:
        out["warnings"].append(f"поиск не удался: {e}")
        return out

    by_id = {p["id"]: p for p in pages}
    used = [p for p in pages if any(f.source == p["id"] for f in facts)]
    out["sources"] = [{"id": p["id"], "title": p["title"], "url": p["url"]} for p in used]
    out["materials"] = [{"id": f"m{i}", "text": f"{f.text} [{_domain(by_id[f.source]['url'])}]"
                         if f.source in by_id else f.text} for i, f in enumerate(facts, 1)]
    return out
