"""Аккаунты: регистрация, вход, сессия, выход, шаблоны пользователя."""

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import app.db as db_module
from app.auth import hash_password, verify_password

TEMPLATES = Path(__file__).resolve().parent.parent / "data" / "templates"


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(db_module, "DB_PATH", tmp_path / "test.db")
    monkeypatch.setenv("LLM_BASE_URL", "http://llm/v1")
    monkeypatch.setenv("LLM_API_KEY", "sk-test")
    monkeypatch.setenv("LLM_MODEL", "m")
    from app.main import app

    with TestClient(app) as c:
        yield c


def test_password_hash_roundtrip():
    h = hash_password("секрет-123")
    assert h.startswith("scrypt$") and "секрет" not in h
    assert verify_password("секрет-123", h)
    assert not verify_password("секрет-124", h)
    assert not verify_password("x", "мусор")
    assert hash_password("a" * 8) != hash_password("a" * 8)      # соль


def test_register_login_me_logout(client):
    r = client.post("/api/auth/register", json={"name": "Аня", "email": " Anya@Example.com ", "password": "pa55word"})
    assert r.status_code == 200 and r.json()["email"] == "anya@example.com"
    assert "cds_session" in r.cookies

    me = client.get("/api/auth/me").json()
    assert (me["name"], me["templates"]) == ("Аня", 0)

    assert client.post("/api/auth/logout").status_code == 204
    assert client.get("/api/auth/me").status_code == 401

    bad = client.post("/api/auth/login", json={"email": "anya@example.com", "password": "wrong"})
    assert bad.status_code == 401 and bad.json()["detail"] == "Неверная почта или пароль"
    ok = client.post("/api/auth/login", json={"email": "ANYA@example.com", "password": "pa55word"})
    assert ok.status_code == 200
    assert client.get("/api/auth/me").status_code == 200


def test_duplicate_and_validation(client):
    body = {"name": "Б", "email": "b@example.com", "password": "123456"}
    assert client.post("/api/auth/register", json=body).status_code == 200
    assert client.post("/api/auth/register", json=body).status_code == 409
    assert client.post("/api/auth/register", json=body | {"email": "не-почта"}).status_code == 422
    assert client.post("/api/auth/register", json=body | {"email": "c@example.com", "password": "123"}).status_code == 422


def test_forged_cookie_is_guest(client):
    client.cookies.set("cds_session", "forged-token-value")
    assert client.get("/api/auth/me").status_code == 401


@pytest.mark.skipif(not (TEMPLATES / "vk_workspace.pptx").exists(), reason="нет шаблона")
def test_templates_are_saved_to_account(client):
    with open(TEMPLATES / "vk_workspace.pptx", "rb") as f:
        data = f.read()
    # гость может загрузить, но в историю не попадает
    assert client.post("/api/templates", files={"file": ("ws.pptx", data)}).status_code == 200
    assert client.get("/api/templates").json() == []

    client.post("/api/auth/register", json={"name": "В", "email": "v@example.com", "password": "123456"})
    client.post("/api/templates", files={"file": ("Воркспейс.pptx", data)})
    mine = client.get("/api/templates").json()
    assert [t["name"] for t in mine] == ["Воркспейс.pptx"]
    assert client.get("/api/auth/me").json()["templates"] == 1
