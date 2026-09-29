import pytest
from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.messages import AIMessage, HumanMessage
from pydantic import BaseModel

from prism.config import load_config
from prism.llm.client import LLMFactory, strip_think
from prism.llm.structured import StructuredOutputError, _extract_json, ainvoke_structured

ENV = {"LLM_BASE_URL": "http://llm/v1", "LLM_API_KEY": "sk-test", "LLM_MODEL": "m"}


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("<think>hmm</think>\n\nОтвет", "Ответ"),
        ("Ответ", "Ответ"),
        ("<think>оборвано на размышлениях", ""),
    ],
)
def test_strip_think(raw, expected):
    assert strip_think(raw) == expected


@pytest.mark.parametrize(
    "raw",
    ['{"a": 1}', '```json\n{"a": 1}\n```', 'Вот JSON: {"a": 1}', '<think>x</think>{"a": 1}'],
)
def test_extract_json(raw):
    assert _extract_json(raw) == '{"a": 1}'


def test_factory_passes_thinking_switch_and_top_k():
    f = LLMFactory(load_config(env=ENV))
    m = f.for_role("outline_planner", think=True)
    assert m.extra_body == {"top_k": 20, "chat_template_kwargs": {"enable_thinking": True}}
    assert f.for_role("slide_writer").extra_body["chat_template_kwargs"] == {"enable_thinking": False}
    assert f.for_role("slide_writer") is f.for_role("slide_writer")
    assert f.for_role("slide_writer", max_tokens=5).max_tokens == 5


def test_openrouter_style_uses_reasoning_switch():
    f = LLMFactory(load_config(env=ENV | {"LLM_API_STYLE": "openrouter"}))
    assert f.for_role("slide_writer").extra_body == {"reasoning": {"enabled": False, "exclude": True}}
    assert f.for_role("outline_planner", think=True).extra_body["reasoning"]["enabled"] is True
    assert LLMFactory(load_config(env=ENV | {"LLM_API_STYLE": "plain"})).for_role("slide_writer").extra_body == {}


class Point(BaseModel):
    x: int
    y: int


class FakeWithBind(GenericFakeChatModel):
    """Фейковая модель, игнорирующая response_format (как провайдер без constrained decoding)."""

    def bind(self, **kwargs):
        return self


async def test_structured_retries_with_validation_feedback():
    model = FakeWithBind(messages=iter([AIMessage('{"x": 1}'), AIMessage('{"x": 1, "y": 2}')]))
    assert await ainvoke_structured(model, [HumanMessage("точка")], Point, retries=1) == Point(x=1, y=2)


async def test_structured_gives_up():
    model = FakeWithBind(messages=iter([AIMessage("не json"), AIMessage("всё ещё нет")]))
    with pytest.raises(StructuredOutputError) as e:
        await ainvoke_structured(model, [HumanMessage("точка")], Point, retries=1)
    assert e.value.last_raw == "всё ещё нет"


def test_spend_limit_stops_immediately_with_readable_message():
    import asyncio

    import httpx
    import openai
    import pytest
    from langchain_core.messages import HumanMessage
    from pydantic import BaseModel

    from prism.llm.structured import ProviderError, ainvoke_structured

    class Out(BaseModel):
        x: int

    calls = []

    class Broke:
        def bind(self, **_):
            return self

        async def ainvoke(self, _):
            calls.append(1)
            req = httpx.Request("POST", "http://llm/v1/chat/completions")
            raise openai.APIStatusError("limit", response=httpx.Response(402, request=req), body=None)

    with pytest.raises(ProviderError, match="баланс"):
        asyncio.run(ainvoke_structured(Broke(), [HumanMessage("hi")], Out, retries=3))
    assert len(calls) == 1                          # не долбим провайдера повторами
