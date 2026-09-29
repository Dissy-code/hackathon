"""Экспорт в HTML: слайды — живой текст и фигуры, картинки встроены один раз, файл самодостаточный."""

from pptx import Presentation
from pptx.chart.data import CategoryChartData
from pptx.enum.chart import XL_CHART_TYPE
from pptx.util import Emu, Pt

from prism.export.html import deck_to_html


def test_html_keeps_text_tables_and_charts(tmp_path):
    prs = Presentation()
    s = prs.slides.add_slide(prs.slide_layouts[5])                 # заголовок + пусто
    s.shapes.title.text = "Выручка выросла на 20%"
    tb = s.shapes.add_textbox(Emu(500000), Emu(1600000), Emu(4000000), Emu(600000))
    tb.text_frame.text = "Рост <быстрее> рынка"
    tb.text_frame.paragraphs[0].runs[0].font.size = Pt(18)
    table = s.shapes.add_table(2, 2, Emu(500000), Emu(2500000), Emu(4000000), Emu(800000)).table
    table.cell(0, 0).text, table.cell(1, 0).text = "Год", "2025"
    data = CategoryChartData()
    data.categories = ["2024", "2025"]
    data.add_series("Выручка", (58, 79))
    s.shapes.add_chart(XL_CHART_TYPE.COLUMN_CLUSTERED, Emu(5000000), Emu(1600000), Emu(3500000), Emu(2500000), data)
    path = tmp_path / "deck.pptx"
    prs.save(str(path))

    page = deck_to_html(path, "Тест")
    assert page.count('<section class="slide') == 1
    assert "Выручка выросла на 20%" in page
    assert "Рост &lt;быстрее&gt; рынка" in page                    # текст экранирован, а не вставлен как HTML
    assert "<table" in page and "2025" in page
    assert "<svg" in page and "<rect" in page                      # диаграмма нарисована по данным
    assert "cqw" in page                                           # кегль масштабируется вместе со слайдом
