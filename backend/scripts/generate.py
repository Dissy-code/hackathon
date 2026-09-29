"""Генерация колод из командной строки: промпт + шаблоны -> .pptx/.pdf/превью в data/decks/<id>/.

    python scripts/generate.py -t data/templates/vk_workspace.pptx -p "Питч сервиса доставки…"
    python scripts/generate.py -t a.pptx -t b.pptx -p @brief.md --slides 10 -i photo.jpg
"""

from __future__ import annotations

import argparse
import asyncio
import json
import uuid
from pathlib import Path

from prism.config import BACKEND_DIR, load_config
from prism.generation.pipeline import detect_language, run
from prism.llm.client import LLMFactory
from prism.mcp.client import McpHub
from prism.parsing.spec import SPECS_DIR, parse_template_sync
from prism.skills.registry import SkillRegistry


async def main(args: argparse.Namespace) -> None:
    cfg = load_config()
    brief = Path(args.prompt[1:]).read_text(encoding="utf-8") if args.prompt.startswith("@") else args.prompt
    templates = []
    for t in args.template:
        spec = parse_template_sync(t)
        templates.append({"id": spec.sha256[:16], "name": Path(t).name, "pptx": str(Path(t).resolve()),
                          "spec": str(SPECS_DIR / spec.sha256[:16] / "spec.json")})
    images = [{"id": f"img{i + 1}", "name": Path(p).name, "path": str(Path(p).resolve())}
              for i, p in enumerate(args.image or [])]
    deck_id = uuid.uuid4().hex[:12]
    out_dir = BACKEND_DIR / "data" / "decks" / deck_id
    out_dir.mkdir(parents=True, exist_ok=True)

    async def show(e: dict) -> None:
        print(f"  [{e['progress'] * 100:5.1f}%] {e['stage']:<8} {e['message']}", flush=True)
        for title in e.get("titles", []):
            print(f"            · {title}")

    state = {"deck_id": deck_id, "out_dir": str(out_dir), "brief": brief, "n_slides": args.slides,
             "language": detect_language(brief), "templates": templates, "images": images}
    final = await run(state, LLMFactory(cfg), SkillRegistry(pins=cfg.skills), on_event=show,
                      mcp=McpHub(cfg.mcp), research_cfg=cfg.research)
    (out_dir / "result.json").write_text(json.dumps(final, ensure_ascii=False, indent=1, default=str),
                                         encoding="utf-8")
    print(f"\nколода {deck_id} за {final['manifest']['seconds']} с -> {out_dir}")
    for d in final["decks"]:
        print(f"  {d['name']}:")
        for v in d["variants"]:
            warns = [w for r in v["reports"] for w in r["warnings"]]
            print(f"    v{v['variant']} {v['label']:<17} {v['pptx']} "
                  f"({len(v.get('previews', []))} превью, предупреждений: {len(warns)})")
    for w in final.get("warnings", []):
        print("  ⚠", w)
    for src in final.get("sources", []):
        print("  источник:", src["title"][:60], src["url"])


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("-t", "--template", action="append", required=True)
    ap.add_argument("-p", "--prompt", required=True, help="текст промпта или @файл")
    ap.add_argument("-i", "--image", action="append")
    ap.add_argument("--slides", type=int, default=None)
    asyncio.run(main(ap.parse_args()))
