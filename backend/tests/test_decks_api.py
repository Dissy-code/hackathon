"""API генерации: загрузка шаблона -> POST /api/decks -> SSE-прогресс -> результат -> скачивание pptx."""

import json
import shutil

import pytest
from fastapi.testclient import TestClient
from test_pipeline import FakeFactory

import app.db as db_module


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(db_module, "DB_PATH", tmp_path / "test.db")
    for k, v in {"LLM_BASE_URL": "http://llm/v1", "LLM_API_KEY": "sk", "LLM_MODEL": "m"}.items():
        monkeypatch.setenv(k, v)
    from app.main import app

    with TestClient(app) as c:
        app.state.llm = FakeFactory()                     # подставная модель вместо провайдера
        yield c


def test_unknown_template_is_404(client):
    r = client.post("/api/decks", json={"prompt": "Питч", "template_ids": ["0000000000000000"]})
    assert r.status_code == 404


def test_image_upload_validation(client):
    assert client.post("/api/images", files={"file": ("a.txt", b"hi")}).status_code == 415
    r = client.post("/api/images", files={"file": ("a.png", b"\x89PNG\r\n\x1a\n" + b"0" * 32)})
    assert r.status_code == 200 and r.json()["url"].startswith("/api/images/")


@pytest.mark.skipif(shutil.which("soffice") is None, reason="нет LibreOffice")
def test_generate_via_api(client, deck):
    with open(deck, "rb") as f:
        tid = client.post("/api/templates", files={"file": ("deck.pptx", f)}).json()["id"]
    deck_id = client.post("/api/decks", json={"prompt": "Питч доставки еды", "template_ids": [tid],
                                              "slides": 3}).json()["id"]

    events = []
    with client.stream("GET", f"/api/decks/{deck_id}/events") as stream:
        for line in stream.iter_lines():
            if line.startswith("data: "):
                events.append(json.loads(line[6:]))
    assert events[-1]["stage"] == "done", events[-1]
    assert any(e["stage"] == "write" for e in events)

    body = client.get(f"/api/decks/{deck_id}").json()
    assert body["status"] == "done"
    [deck] = body["result"]["decks"]
    assert [v["label"] for v in deck["variants"]] == ["Сбалансированный", "Визуальный", "Компактный"]
    d = deck["variants"][0]
    assert len(d["slides"]) == 3
    pptx = client.get(d["pptx"])
    assert pptx.status_code == 200 and pptx.content[:2] == b"PK"   # zip = настоящий pptx
    assert client.get(d["slides"][0]).headers["content-type"] == "image/png"
    # выход за пределы каталога колоды не отдаётся
    assert client.get(f"/api/decks/{deck_id}/files/../../prism.db").status_code == 404
