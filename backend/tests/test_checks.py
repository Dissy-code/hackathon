"""Проверка содержимого слайдов до вёрстки и понижение типа."""

from prism.generation.checks import content_problems, degrade
from prism.planning.schemas import ChartData, ChartSeries, Item, SlideContent, SlideKind


def test_timeline_needs_points_with_dates_and_text():
    c = SlideContent(kind=SlideKind.timeline, title="План", items=[Item(value="Q1 2026")])
    problems = content_problems(c)
    assert any("нужно 3–6 пунктов" in p for p in problems)
    assert any("дата/период" in p for p in problems)
    assert degrade(c).kind == SlideKind.bullets


def test_chart_without_data_is_degraded():
    c = SlideContent(kind=SlideKind.chart, title="Рост", subtitle="Растём")
    assert content_problems(c)
    assert degrade(c).kind == SlideKind.section


def test_chart_series_must_match_categories():
    c = SlideContent(kind=SlideKind.chart, title="Рост",
                     chart=ChartData(categories=["2024", "2025"], series=[ChartSeries(name="x", values=[1])]))
    assert any("столько значений" in p for p in content_problems(c))


def test_kpi_without_numbers_becomes_cards():
    c = SlideContent(kind=SlideKind.kpi, title="Итоги",
                     items=[Item(heading="Рост", text="Быстрый"), Item(heading="Спрос", text="Высокий")])
    assert any("нет числа" in p for p in content_problems(c))
    assert degrade(c).kind == SlideKind.cards


def test_good_slide_has_no_problems():
    c = SlideContent(kind=SlideKind.process, title="Запуск за три шага",
                     items=[Item(heading="Партнёры", text="Подключаем рестораны"),
                            Item(heading="Курьеры", text="Нанимаем команду"),
                            Item(heading="Маркетинг", text="Запускаем рекламу")])
    assert content_problems(c) == []
    assert degrade(c).kind == SlideKind.process


def test_long_value_is_split_into_number_and_caption():
    from prism.generation.checks import normalize

    c = SlideContent(kind=SlideKind.kpi, title="Пилот", items=[
        Item(value="3 200 заказов за 6 недель пилота"), Item(value="1,2 млрд ₽ выручка", text="за год"),
        Item(value="38%", text="повторных")])
    items = normalize(c).items
    assert (items[0].value, items[0].text) == ("3 200", "заказов за 6 недель пилота")
    assert (items[1].value, items[1].text) == ("1,2 млрд ₽", "выручка за год")
    assert items[2].value == "38%"
    assert content_problems(normalize(c)) == []
