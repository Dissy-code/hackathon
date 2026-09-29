"""Пайплайн целиком на подставной модели: граф, вёрстка, рендер и API — без сети и без настоящей LLM."""

import asyncio
import json
import shutil

import pytest
from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.messages import AIMessage

from prism.generation.pipeline import detect_language, run
from prism.parsing.spec import SPECS_DIR, parse_template_sync
from prism.skills.registry import SkillRegistry

OUTLINE = {
    "title": "Доставка еды в Казани", "language": "ru", "audience": "инвесторы",
    "storyline": "Рынок растёт, пилот сработал, просим деньги на масштаб.",
    "slides": [
        {"kind": "title", "title": "Доставка еды в Казани", "key_message": "Питч", "content_brief": "титул"},
        {"kind": "cards", "title": "Рестораны переплачивают агрегаторам", "key_message": "Комиссии высокие",
         "content_brief": "две карточки"},
        {"kind": "process", "title": "Запуск района за три шага", "key_message": "Модель повторяется",
         "content_brief": "три шага"},
    ],
}
SLIDES = {"slides": [
    {"kind": "title", "title": "Доставка еды в Казани", "subtitle": "Питч для инвесторов"},
    {"kind": "cards", "title": "Рестораны переплачивают агрегаторам",
     "items": [{"heading": "Комиссия", "text": "До 35% с заказа"}, {"heading": "Рост", "text": "21% в год"}]},
    {"kind": "process", "title": "Запуск района за три шага",
     "items": [{"text": "Рестораны"}, {"text": "Курьеры"}, {"text": "Маркетинг"}]},
]}


class FakeModel(GenericFakeChatModel):
    def bind(self, **kwargs):
        return self


class FakeFactory:
    """Вместо LLMFactory: роль -> фейковая модель с заранее известным ответом."""

    def __init__(self):
        self.answers = {"outline_planner": json.dumps(OUTLINE, ensure_ascii=False),
                        "slide_writer": json.dumps(SLIDES, ensure_ascii=False)}

    def for_role(self, role: str, **_):
        return FakeModel(messages=iter([AIMessage(self.answers[role])] * 3))

    def resolve(self, role: str):
        return type("R", (), {"model": "fake"})()


def test_detect_language():
    assert detect_language("Питч для инвесторов") == "ru"
    assert detect_language("Investor pitch deck") == "en"


@pytest.mark.skipif(shutil.which("soffice") is None, reason="нет LibreOffice")
def test_pipeline_end_to_end(deck, tmp_path):
    spec = parse_template_sync(deck, use_cache=False)
    tid = spec.sha256[:16]
    state = {"deck_id": "t1", "out_dir": str(tmp_path), "brief": "Питч доставки еды", "n_slides": 3,
             "language": "ru", "images": [],
             "templates": [{"id": tid, "name": "deck.pptx", "pptx": str(deck),
                            "spec": str(SPECS_DIR / tid / "spec.json")}]}
    events = []

    async def collect(e):
        events.append(e)

    final = asyncio.run(run(state, FakeFactory(), SkillRegistry(), on_event=collect))
    stages = [e["stage"] for e in events]
    assert stages[0] == "plan" and stages[-1] == "audit"
    assert events[-1]["progress"] == 1.0
    [result] = final["decks"]
    assert [v["variant"] for v in result["variants"]] == [1, 2, 3]
    for v in result["variants"]:
        assert (tmp_path / tid / f"v{v['variant']}" / "deck.pptx").exists()
        assert len(v["previews"]) == 3 and v["pdf"].endswith(".pdf") and v["html"].endswith(".html")
        # аудит — часть пайплайна: у каждого варианта есть audit.json и сводка
        assert (tmp_path / tid / f"v{v['variant']}" / "audit.json").exists() and "errors" in v["audit"]
    assert (tmp_path / "context.json").exists()
    assert [r["kind"] for r in result["variants"][0]["reports"]] == ["title", "cards", "process"]
    skills = [s["skill"] for s in final["manifest"]["skills"]]
    assert any(s.startswith("outline_planner@") for s in skills) and any(s.startswith("slide_writer@") for s in skills)
