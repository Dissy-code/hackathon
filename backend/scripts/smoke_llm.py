"""Проверка возможностей LLM-провайдера, от которых зависит пайплайн.

    python scripts/smoke_llm.py                   # провайдер и модель из backend/.env
    python scripts/smoke_llm.py --only json_schema,tools

Отчёт пишется в data/smoke/<model>-<время>.json — его стоит приложить к MODELS.md.
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import io
import json
import sys
import time
from collections.abc import Awaitable, Callable
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from typing import Any

from langchain_core.messages import HumanMessage
from langchain_core.tools import tool
from PIL import Image, ImageDraw, ImageFont
from pydantic import BaseModel

from prism.config import BACKEND_DIR, load_config
from prism.llm.client import LLMFactory, strip_think
from prism.llm.structured import StructuredOutputError, ainvoke_structured
from prism.planning.schemas import DeckOutline
from prism.skills.registry import SkillRegistry


@dataclass
class CheckResult:
    name: str
    ok: bool
    seconds: float
    detail: str
    data: dict[str, Any] = field(default_factory=dict)


def _usage(msg) -> dict[str, int]:
    u = getattr(msg, "usage_metadata", None) or {}
    return {"in": u.get("input_tokens", 0), "out": u.get("output_tokens", 0)}


class Smoke:
    def __init__(self, llm: LLMFactory, skills: SkillRegistry, args: argparse.Namespace):
        self.llm, self.skills, self.args = llm, skills, args

    # ── базовые ─────────────────────────────────────────────────────────

    async def basic(self) -> tuple[bool, str, dict]:
        m = self.llm.for_role("slide_writer", think=False, max_tokens=64)
        r = await m.ainvoke("Назови столицу Франции одним словом.")
        text = strip_think(r.content)
        return "париж" in text.lower(), f"ответ: {text!r}", _usage(r)

    async def max_tokens(self) -> tuple[bool, str, dict]:
        m = self.llm.for_role("slide_writer", think=False, max_tokens=12)
        r = await m.ainvoke("Напиши подробный рассказ на 500 слов о море.")
        u = _usage(r)
        return u["out"] <= 12, f"лимит 12, сгенерировано {u['out']} токенов", u

    async def thinking_toggle(self) -> tuple[bool, str, dict]:
        q = "Сколько будет 17 * 23? Ответь только числом."
        on = await self.llm.for_role("outline_planner", think=True, max_tokens=2048).ainvoke(q)
        off = await self.llm.for_role("outline_planner", think=False, max_tokens=2048).ainvoke(q)
        u_on, u_off = _usage(on), _usage(off)
        leaked = "<think>" in on.content or "<think>" in off.content
        ok = u_off["out"] < u_on["out"] and not leaked and "391" in strip_think(off.content)
        detail = f"think=on {u_on['out']} ток., think=off {u_off['out']} ток., <think> в content: {leaked}"
        return ok, detail, {"on": u_on, "off": u_off, "think_in_content": leaked}

    # ── структурированный вывод и инструменты ──────────────────────────

    async def json_schema(self) -> tuple[bool, str, dict]:
        class City(BaseModel):
            name: str
            country: str
            population_millions: float
            landmarks: list[str]

        m = self.llm.for_role("slide_writer", think=False, max_tokens=512)
        msgs = [HumanMessage("Опиши Казань: название, страна, население в миллионах, 3 достопримечательности.")]
        try:
            city = await ainvoke_structured(m, msgs, City, retries=0)
        except StructuredOutputError as e:
            return False, f"невалидный JSON с первой попытки: {e}", {"raw": e.last_raw[:500]}
        except Exception as e:  # noqa: BLE001 — сервер мог отвергнуть response_format
            return False, f"{type(e).__name__}: {e}", {}
        return len(city.landmarks) == 3, f"валидно с первой попытки: {city.model_dump()}", {}

    async def tools(self) -> tuple[bool, str, dict]:
        @tool
        def get_slide_count(deck_id: str) -> int:
            """Возвращает число слайдов в колоде по её идентификатору."""
            return 12

        m = self.llm.for_role("fix_agent", think=False, max_tokens=512).bind_tools([get_slide_count])
        r = await m.ainvoke("Сколько слайдов в колоде deck-42? Узнай через инструмент.")
        calls = r.tool_calls
        ok = bool(calls) and calls[0]["name"] == "get_slide_count" and calls[0]["args"].get("deck_id") == "deck-42"
        return ok, f"tool_calls: {calls}", {}

    # ── vision ─────────────────────────────────────────────────────────

    async def vision(self) -> tuple[bool, str, dict]:
        img = Image.new("RGB", (960, 540), "white")
        d = ImageDraw.Draw(img)
        d.rectangle((560, 140, 880, 400), fill=(220, 30, 30))
        d.text((60, 200), "CODE 7319", fill="black", font=ImageFont.load_default(size=96))
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        url = "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()

        m = self.llm.for_role("audit_vlm", think=False, max_tokens=128)
        r = await m.ainvoke([HumanMessage(content=[
            {"type": "text", "text": "Какое число написано на картинке и какого цвета прямоугольник? Ответь кратко."},
            {"type": "image_url", "image_url": {"url": url}},
        ])])
        text = strip_think(r.content).lower()
        ok = "7319" in text and ("красн" in text or "red" in text)
        return ok, f"ответ: {text!r}", _usage(r)

    # ── контекст и параллельность ──────────────────────────────────────

    async def long_context(self) -> tuple[bool, str, dict]:
        target = self.args.ctx_tokens
        filler = [
            f"Абзац {i}. Отчёт о квартальных показателях подразделения {i % 17}: выручка изменилась, "
            f"команда провела {i % 9 + 1} ретроспектив, а на складе {i * 37 % 1000} позиций."
            for i in range(target // 45 + 1)
        ]
        prompt = (
            "Кодовое слово проекта: ЛАЗУРИТ-58. Запомни его.\n\n"
            + "\n".join(filler)
            + "\n\nКакое кодовое слово проекта было названо в самом начале? Ответь только им."
        )
        m = self.llm.for_role("slide_writer", think=False, max_tokens=32)
        t0 = time.perf_counter()
        r = await m.ainvoke(prompt)
        dt = time.perf_counter() - t0
        u = _usage(r)
        ok = "лазурит-58" in strip_think(r.content).lower()
        return ok, f"{u['in']} ток. на входе, {dt:.1f} с (~{u['in'] / dt:.0f} ток/с prefill), ответ {r.content!r}", u

    async def parallel(self) -> tuple[bool, str, dict]:
        n = self.args.parallel
        m = self.llm.for_role("slide_writer", think=False, max_tokens=160, temperature=0.7)
        prompts = [f"Напиши 5 тезисов для слайда о пользе {t}." for t in
                   ["автоматизации", "аналитики", "облаков", "тестов", "документации", "CI", "мониторинга", "кеша"]]
        prompts = (prompts * (n // len(prompts) + 1))[:n]

        t0 = time.perf_counter()
        single = await m.ainvoke(prompts[0])
        t_single = time.perf_counter() - t0

        t0 = time.perf_counter()
        replies = await m.abatch(prompts, config={"max_concurrency": n})
        t_par = time.perf_counter() - t0
        out_tokens = sum(_usage(r)["out"] for r in replies)
        speedup = n * t_single / t_par
        detail = (
            f"1 запрос {t_single:.1f} с; {n} параллельно {t_par:.1f} с; ускорение x{speedup:.1f}; "
            f"суммарно {out_tokens / t_par:.0f} ток/с"
        )
        return speedup > 1.5, detail, {"single_s": t_single, "parallel_s": t_par, "speedup": speedup,
                                       "single_out": _usage(single)["out"]}

    # ── реальный скилл ─────────────────────────────────────────────────

    async def outline(self) -> tuple[bool, str, dict]:
        skill = self.skills.get("outline_planner")
        msgs = skill.render(
            purpose="product",
            audience="инвесторы посевной стадии",
            n_slides=10,
            language="ru",
            brief="Запуск сервиса доставки еды в Казани: питч для инвесторов.",
            materials=[
                {"id": "m1", "text": "Рынок доставки еды в Казани в 2025 году — 14 млрд ₽, рост 21% в год."},
                {"id": "m2", "text": "Средний чек 1 150 ₽, конкуренты берут комиссию 25–35% с ресторанов."},
                {"id": "m3", "text": "Пилот в двух районах: 3 200 заказов за 6 недель, повторные заказы 38%."},
                {"id": "m4", "text": "Просим 60 млн ₽ на 18 месяцев: курьеры, маркетинг, выход в 5 районов."},
            ],
        )
        m = self.llm.for_role(skill.role)
        try:
            outline = await ainvoke_structured(m, msgs, DeckOutline, retries=1)
        except StructuredOutputError as e:
            return False, str(e), {"raw": e.last_raw[:1000]}
        titles = [f"{s.kind}: {s.title}" for s in outline.slides]
        cyr = sum(any("а" <= c.lower() <= "я" for c in s.title) for s in outline.slides)
        ok = 8 <= len(outline.slides) <= 12 and cyr == len(outline.slides)
        return ok, f"{skill.ref}: {len(outline.slides)} слайдов\n      " + "\n      ".join(titles), {
            "skill": skill.manifest_entry(), "outline": outline.model_dump(mode="json")}


CHECKS = ["basic", "max_tokens", "thinking_toggle", "json_schema", "tools", "vision", "long_context", "parallel",
          "outline"]


async def run(args: argparse.Namespace) -> int:
    cfg = load_config(args.config)
    smoke = Smoke(LLMFactory(cfg), SkillRegistry(pins=cfg.skills), args)
    selected = args.only.split(",") if args.only else CHECKS
    text_role = cfg.resolve_role("slide_writer")
    print(f"{text_role.model} @ {text_role.llm.base_url}\n")

    results: list[CheckResult] = []
    for name in selected:
        fn: Callable[[], Awaitable[tuple[bool, str, dict]]] = getattr(smoke, name)
        t0 = time.perf_counter()
        try:
            ok, detail, data = await fn()
        except Exception as e:  # noqa: BLE001 — упавшая проверка не должна останавливать остальные
            ok, detail, data = False, f"{type(e).__name__}: {e}", {}
        res = CheckResult(name, ok, round(time.perf_counter() - t0, 2), detail, data)
        results.append(res)
        print(f"{'✅' if ok else '❌'} {name:<16} {res.seconds:>6.1f} с  {detail}", flush=True)

    out_dir = BACKEND_DIR / "data" / "smoke"
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / f"{text_role.model}-{datetime.now(UTC):%Y%m%d-%H%M%S}.json"
    out.write_text(json.dumps({
        "model": text_role.model, "base_url": text_role.llm.base_url,
        "results": [asdict(r) for r in results],
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\nотчёт: {out.relative_to(BACKEND_DIR)}")
    return 0 if all(r.ok for r in results) else 1


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--config", default=None)
    p.add_argument("--only", default=None, help=f"через запятую из: {','.join(CHECKS)}")
    p.add_argument("--ctx-tokens", type=int, default=16000, help="размер промпта для long_context")
    p.add_argument("--parallel", type=int, default=8, help="число одновременных запросов")
    sys.exit(asyncio.run(run(p.parse_args())))


if __name__ == "__main__":
    main()
