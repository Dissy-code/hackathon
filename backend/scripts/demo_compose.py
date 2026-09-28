"""Сборка тестовой колоды по шаблону без модели — проверка вёрстки отдельно от генерации.

    python scripts/demo_compose.py vk_workspace [vk_tech …]   # -> data/debug/compose_<шаблон>.pptx + .png
"""

from __future__ import annotations

import subprocess
import sys
import tempfile
from pathlib import Path

from PIL import Image, ImageDraw

from prism.config import BACKEND_DIR
from prism.layout.compose import compose_deck
from prism.parsing.spec import parse_template_sync
from prism.planning.schemas import ChartData, ChartSeries, Item, SlideContent, SlideKind, TableData

DECK = [
    SlideContent(kind=SlideKind.title, title="Доставка еды в Казани: 60 млн ₽ на захват растущего рынка",
                 subtitle="Питч для инвесторов посевной стадии"),
    SlideContent(kind=SlideKind.cards, title="Рестораны теряют до трети выручки на комиссиях агрегаторов",
                 items=[Item(heading="Комиссия", text="Конкуренты берут 25–35% с каждого заказа"),
                        Item(heading="Нет лидера", text="Ни один сервис не держит больше 40% рынка"),
                        Item(heading="Рост", text="Рынок прибавляет 21% в год уже третий год")]),
    SlideContent(kind=SlideKind.kpi, title="Пилот доказал спрос за шесть недель",
                 items=[Item(value="3 200", text="заказов в двух районах"),
                        Item(value="38%", text="повторных заказов"),
                        Item(value="1 150 ₽", text="средний чек")]),
    SlideContent(kind=SlideKind.process, title="Модель запуска района повторяется за четыре шага",
                 items=[Item(heading="Рестораны", text="Подключаем 30 партнёров с комиссией 12%"),
                        Item(heading="Курьеры", text="Нанимаем и обучаем команду за две недели"),
                        Item(heading="Маркетинг", text="Локальная реклама и первые промокоды"),
                        Item(heading="Масштаб", text="Выходим в соседний район через месяц")]),
    SlideContent(kind=SlideKind.chart, title="Рынок доставки в Казани растёт на 21% в год",
                 subtitle="Объём рынка, млрд ₽",
                 chart=ChartData(type="column", categories=["2023", "2024", "2025", "2026"],
                                 series=[ChartSeries(name="Рынок", values=[9.6, 11.6, 14.0, 16.9])], unit="млрд ₽")),
    SlideContent(kind=SlideKind.timeline, title="За 18 месяцев выходим из двух районов в пять",
                 items=[Item(value="Q1", text="Два пилотных района"), Item(value="Q2", text="Третий район"),
                        Item(value="Q3", text="Собственная кухня"), Item(value="Q4", text="Пять районов")]),
    SlideContent(kind=SlideKind.table, title="60 млн ₽ разойдутся на три направления",
                 table=TableData(columns=["Статья", "Сумма", "Доля"],
                                 rows=[["Курьеры", "27 млн ₽", "45%"], ["Маркетинг", "21 млн ₽", "35%"],
                                       ["Новые районы", "12 млн ₽", "20%"]])),
    SlideContent(kind=SlideKind.bullets, title="Почему мы выиграем у агрегаторов",
                 items=[Item(text="Комиссия для ресторанов в два раза ниже рынка"),
                        Item(text="Доставка за 25 минут внутри района"),
                        Item(text="Своя команда курьеров вместо самозанятых")]),
    SlideContent(kind=SlideKind.closing, title="Займите Казань вместе с нами", subtitle="invest@example.ru"),
]


def render(name: str) -> Path:
    tpl = BACKEND_DIR / "data" / "templates" / f"{name}.pptx"
    spec = parse_template_sync(tpl)
    data, reports = compose_deck(tpl, spec, DECK)
    out_dir = BACKEND_DIR / "data" / "debug"
    out_dir.mkdir(parents=True, exist_ok=True)
    pptx = out_dir / f"compose_{name}.pptx"
    pptx.write_bytes(data)
    for r in reports:
        print(f"  {r.index:>2} {r.kind:<10} <- {r.pattern:<9} {'; '.join(r.warnings)}")

    with tempfile.TemporaryDirectory() as tmp:
        subprocess.run(["soffice", "--headless", "--convert-to", "pdf", "--outdir", tmp, str(pptx)],
                       check=True, capture_output=True)
        subprocess.run(["pdftoppm", "-r", "40", "-png", f"{tmp}/{pptx.stem}.pdf", f"{tmp}/s"], check=True)
        pages = sorted(Path(tmp).glob("s-*.png"), key=lambda p: int(p.stem.split("-")[1]))
        ims = [Image.open(p).convert("RGB") for p in pages]
    w, h = ims[0].size
    cols = 3
    sheet = Image.new("RGB", (cols * (w + 6), ((len(ims) + cols - 1) // cols) * (h + 6)), "gray")
    for i, im in enumerate(ims):
        x, y = (i % cols) * (w + 6), (i // cols) * (h + 6)
        sheet.paste(im, (x, y))
        ImageDraw.Draw(sheet).text((x + 3, y + 2), f"{i + 1} {reports[i].kind} <- {reports[i].pattern}", fill="red")
    png = out_dir / f"compose_{name}.png"
    sheet.save(png)
    return png


if __name__ == "__main__":
    for n in sys.argv[1:]:
        print(n)
        print("  ->", render(n))
