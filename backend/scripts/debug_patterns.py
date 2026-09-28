"""Визуальная проверка паттернов: рамки слотов и групп поверх рендера слайдов.

    python scripts/debug_patterns.py vk_tech 14 21 26      # -> data/debug/<шаблон>_patterns.png

Нужны data/parsed/<шаблон>.raw.json и data/render/<шаблон>.pdf (soffice --convert-to pdf).
"""

from __future__ import annotations

import subprocess
import sys
import tempfile
from pathlib import Path

from PIL import Image, ImageDraw

from prism.config import BACKEND_DIR
from prism.parsing.model import TemplateRaw
from prism.parsing.patterns import build_pattern
from prism.parsing.tokens import build_tokens

GROUP_COLORS = ["#ff2d55", "#34c759", "#ff9500", "#af52de", "#00c7be", "#ffcc00"]


def render(name: str, indices: list[int]) -> Path:
    raw = TemplateRaw.model_validate_json((BACKEND_DIR / f"data/parsed/{name}.raw.json").read_text())
    tokens = build_tokens(raw)
    pdf = BACKEND_DIR / f"data/render/{name}.pdf"
    tiles = []
    with tempfile.TemporaryDirectory() as tmp:
        for idx in indices:
            subprocess.run(["pdftoppm", "-r", "50", "-png", "-f", str(idx), "-l", str(idx), "-singlefile",
                            str(pdf), f"{tmp}/s"], check=True)
            im = Image.open(f"{tmp}/s.png").convert("RGB")
            d = ImageDraw.Draw(im)
            k = im.width / raw.slide_w
            p = build_pattern(raw, raw.slides[idx - 1], tokens)

            def box(b, color, width=2):
                d.rectangle([b.x * k, b.y * k, b.right * k, b.bottom * k], outline=color, width=width)

            if p.title:
                box(p.title.bbox, "#0a84ff", 3)
            for s in p.slots:
                box(s.bbox, "#8e8e93")
                d.text((s.bbox.x * k + 2, s.bbox.y * k + 1), s.kind.value, fill="#8e8e93")
            for gi, g in enumerate(p.groups):
                color = GROUP_COLORS[gi % len(GROUP_COLORS)]
                for ib in g.item_bboxes:
                    box(ib, color, 3)
                for slot in g.items[0]:
                    d.text((slot.bbox.x * k + 2, slot.bbox.y * k + 1), slot.kind.value, fill=color)
            label = f"#{idx} " + " | ".join(f"{g.count}x{g.arrangement}[{','.join(g.item_kinds())}]" for g in p.groups)
            d.rectangle([0, im.height - 14, im.width, im.height], fill="black")
            d.text((3, im.height - 13), label[:110], fill="white")
            tiles.append(im)

    cols = 3
    w, h = tiles[0].size
    sheet = Image.new("RGB", (cols * w, ((len(tiles) + cols - 1) // cols) * h), "gray")
    for i, t in enumerate(tiles):
        sheet.paste(t, ((i % cols) * w, (i // cols) * h))
    out = BACKEND_DIR / f"data/debug/{name}_patterns.png"
    out.parent.mkdir(parents=True, exist_ok=True)
    sheet.save(out)
    return out


if __name__ == "__main__":
    print(render(sys.argv[1], [int(x) for x in sys.argv[2:]]))
