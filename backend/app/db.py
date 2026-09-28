"""SQLite: пользователи, сессии, привязка шаблонов и колод к пользователю.

Соединение — на запрос (sqlite3 из stdlib, без ORM): нагрузка хакатонная, схема маленькая.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from pathlib import Path

from prism.config import BACKEND_DIR

DB_PATH = BACKEND_DIR / "data" / "prism.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id          INTEGER PRIMARY KEY,
    email       TEXT NOT NULL UNIQUE,
    name        TEXT NOT NULL,
    pw_hash     TEXT NOT NULL,
    created_at  TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS sessions (
    token_hash  TEXT PRIMARY KEY,           -- sha256 токена: сам токен живёт только в cookie
    user_id     INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    expires_at  TEXT NOT NULL,
    created_at  TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS user_templates (
    user_id     INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    template_id TEXT NOT NULL,              -- = первые 16 символов sha256 файла (data/specs/<id>)
    name        TEXT NOT NULL,
    format      TEXT NOT NULL,
    created_at  TEXT NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (user_id, template_id)
);
CREATE TABLE IF NOT EXISTS user_decks (
    user_id     INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    deck_id     TEXT NOT NULL,              -- каталог data/decks/<id>
    title       TEXT NOT NULL,              -- начало промпта — чтобы узнать колоду в списке
    created_at  TEXT NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (user_id, deck_id)
);
"""


def connect(path: Path | None = None) -> sqlite3.Connection:
    path = path or DB_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    # соединение живёт один запрос, но FastAPI может открыть его в пуле потоков, а использовать в цикле событий
    conn = sqlite3.connect(path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db(path: Path | None = None) -> None:
    with connect(path) as conn:
        conn.execute("PRAGMA journal_mode = WAL")
        conn.executescript(SCHEMA)


def get_db() -> Iterator[sqlite3.Connection]:
    """FastAPI-зависимость: соединение на время запроса."""
    conn = connect()
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()
