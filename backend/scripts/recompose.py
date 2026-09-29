"""Пересборка вёрстки уже сгенерированной колоды — без запросов к модели (для настройки вёрстки).

    python scripts/recompose.py data/decks/<id> [шаблон.pptx …]   # -> data/debug/recompose/<шаблон>.png
"""

from __future__ import annotations

import asyncio
import json
import sys
import tempfile
from pathlib import Path

from PIL import Image, ImageDraw

from prism.config import BACKEND_DIR, load_config
from prism.generation.checks import content_problems, degrade, normalize
from prism.layout.compose import VARIANTS, compose_deck, select_patterns
from prism.llm.client import LLMFactory
from prism.parsing.spec import parse_template
from prism.planning.schemas import SlideContent
from prism.render import render_deck
from prism.skills.registry import SkillRegistry


def main(deck_dir: Path, templates: list[Path]) -> None:
    result = json.loads((deck_dir / "result.json").read_text(encoding="utf-8"))
    raw_contents = result["contents"]
    shared = isinstance(raw_contents, list)     # колоды до вариантов по содержанию: одно содержимое на все
    if shared:
        raw_contents = {str(v): raw_contents for v in VARIANTS}
    by_variant = {}
    for key, items in raw_contents.items():
        cs = [normalize(SlideContent.model_validate(c)) for c in items]
        by_variant[int(key)] = [degrade(c) if content_problems(c) else c for c in cs]
    out = BACKEND_DIR / "data" / "debug" / "recompose"
    out.mkdir(parents=True, exist_ok=True)
    cfg = load_config()
    llm, skills = LLMFactory(cfg), SkillRegistry(pins=cfg.skills)
    for tpl in templates:
        spec = asyncio.run(parse_template(tpl, llm=llm, skills=skills))   # разметка моделью, кешируется
        rows = []
        for v in VARIANTS:
            contents = by_variant[v]
            first = [ps.pattern.id for ps in select_patterns(spec, contents, 1)] if shared else None
            data, reports = compose_deck(tpl, spec, contents, None, v, first)
            print(f"{tpl.stem} v{v}: " + " ".join(r.pattern.split(":")[1] for r in reports),
                  f"| предупреждений: {sum(len(r.warnings) for r in reports)}")
            with tempfile.TemporaryDirectory() as tmp:
                pptx = Path(tmp) / "deck.pptx"
                pptx.write_bytes(data)
                _, pngs = render_deck(pptx, Path(tmp), dpi=36)
                ims = [Image.open(p).convert("RGB") for p in pngs]
            w, h = ims[0].size
            cols = 4
            grid = Image.new("RGB", (cols * (w + 4), ((len(ims) + cols - 1) // cols) * (h + 4) + 16), "white")
            ImageDraw.Draw(grid).text((4, 2), f"вариант {v}", fill="red")
            for i, im in enumerate(ims):
                grid.paste(im, ((i % cols) * (w + 4), 16 + (i // cols) * (h + 4)))
            grid.save(out / f"{tpl.stem}_v{v}.png")
            rows.append(grid)
        sheet = Image.new("RGB", (max(r.width for r in rows), sum(r.height + 4 for r in rows)), "gray")
        y = 0
        for r in rows:
            sheet.paste(r, (0, y))
            y += r.height + 4
        sheet.save(out / f"{tpl.stem}.png")


if __name__ == "__main__":
    deck = Path(sys.argv[1])
    tpls = [Path(t) for t in sys.argv[2:]] or sorted((BACKEND_DIR / "data" / "templates").glob("vk_*.pptx"))
    main(deck, tpls)
