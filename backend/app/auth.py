"""Аккаунты: регистрация и вход по почте и паролю, сессия в HttpOnly-cookie.

    POST /api/auth/register {name, email, password, remember?}
    POST /api/auth/login    {email, password, remember?}
    POST /api/auth/logout
    GET  /api/auth/me
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import re
import secrets
import sqlite3
from datetime import UTC, datetime, timedelta
from typing import Annotated

from fastapi import APIRouter, Cookie, Depends, HTTPException, Response
from pydantic import BaseModel, Field

from app.db import get_db

router = APIRouter(prefix="/api/auth", tags=["auth"])

COOKIE = "cds_session"
SESSION_DAYS_REMEMBER = 30
SESSION_HOURS_SHORT = 12
_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
# scrypt: ~16 МБ памяти и десятки миллисекунд на хеш — дорого для перебора, терпимо для входа
_N, _R, _P = 2**14, 8, 1

Db = Annotated[sqlite3.Connection, Depends(get_db)]


# ── пароли ─────────────────────────────────────────────────────────────────


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, n=_N, r=_R, p=_P, dklen=32)
    return f"scrypt${_N}${_R}${_P}${base64.b64encode(salt).decode()}${base64.b64encode(digest).decode()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        _, n, r, p, salt, digest = stored.split("$")
        expected = base64.b64decode(digest)
        actual = hashlib.scrypt(password.encode(), salt=base64.b64decode(salt), n=int(n), r=int(r), p=int(p),
                                dklen=len(expected))
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(actual, expected)


# ── сессии ─────────────────────────────────────────────────────────────────


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def _open_session(db: sqlite3.Connection, response: Response, user_id: int, remember: bool) -> None:
    token = secrets.token_urlsafe(32)
    ttl = timedelta(days=SESSION_DAYS_REMEMBER) if remember else timedelta(hours=SESSION_HOURS_SHORT)
    expires = datetime.now(UTC) + ttl
    db.execute("INSERT INTO sessions (token_hash, user_id, expires_at) VALUES (?, ?, ?)",
               (_token_hash(token), user_id, expires.isoformat()))
    response.set_cookie(
        COOKIE, token, httponly=True, samesite="lax", path="/",
        # без «запомнить» — сессионная cookie браузера, пропадает с закрытием
        max_age=int(ttl.total_seconds()) if remember else None,
    )


class User(BaseModel):
    id: int
    name: str
    email: str


def _user(row: sqlite3.Row) -> User:
    return User(id=row["id"], name=row["name"], email=row["email"])


def current_user(db: Db, cds_session: Annotated[str | None, Cookie()] = None) -> User | None:
    """Пользователь по cookie или None для гостя."""
    if not cds_session:
        return None
    row = db.execute(
        "SELECT u.* FROM sessions s JOIN users u ON u.id = s.user_id WHERE s.token_hash = ? AND s.expires_at > ?",
        (_token_hash(cds_session), datetime.now(UTC).isoformat()),
    ).fetchone()
    return _user(row) if row else None


OptionalUser = Annotated[User | None, Depends(current_user)]


def require_user(user: OptionalUser) -> User:
    if user is None:
        raise HTTPException(401, "Нужно войти в аккаунт")
    return user


# ── эндпоинты ──────────────────────────────────────────────────────────────


class RegisterIn(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    email: str = Field(max_length=254)
    password: str = Field(min_length=6, max_length=256)
    remember: bool = True


class LoginIn(BaseModel):
    email: str
    password: str
    remember: bool = True


def _norm_email(email: str) -> str:
    email = email.strip().lower()
    if not _EMAIL.match(email):
        raise HTTPException(422, "Проверьте адрес почты")
    return email


@router.post("/register", response_model=User)
def register(body: RegisterIn, response: Response, db: Db) -> User:
    email = _norm_email(body.email)
    name = body.name.strip()
    if not name:
        raise HTTPException(422, "Укажите имя")
    try:
        cur = db.execute("INSERT INTO users (email, name, pw_hash) VALUES (?, ?, ?)",
                         (email, name, hash_password(body.password)))
    except sqlite3.IntegrityError:
        raise HTTPException(409, "Аккаунт с этой почтой уже есть — войдите") from None
    _open_session(db, response, cur.lastrowid, body.remember)
    return User(id=cur.lastrowid, name=name, email=email)


@router.post("/login", response_model=User)
def login(body: LoginIn, response: Response, db: Db) -> User:
    row = db.execute("SELECT * FROM users WHERE email = ?", (body.email.strip().lower(),)).fetchone()
    # одно сообщение на «нет такой почты» и «неверный пароль» — не подсказываем, какие почты есть
    if row is None or not verify_password(body.password, row["pw_hash"]):
        raise HTTPException(401, "Неверная почта или пароль")
    _open_session(db, response, row["id"], body.remember)
    return _user(row)


@router.post("/logout", status_code=204)
def logout(response: Response, db: Db, cds_session: Annotated[str | None, Cookie()] = None) -> None:
    if cds_session:
        db.execute("DELETE FROM sessions WHERE token_hash = ?", (_token_hash(cds_session),))
    response.delete_cookie(COOKIE, path="/")


class Me(User):
    templates: int
    decks: int


@router.get("/me", response_model=Me)
def me(user: Annotated[User, Depends(require_user)], db: Db) -> Me:
    templates = db.execute("SELECT count(*) FROM user_templates WHERE user_id = ?", (user.id,)).fetchone()[0]
    decks = db.execute("SELECT count(*) FROM user_decks WHERE user_id = ?", (user.id,)).fetchone()[0]
    return Me(**user.model_dump(), templates=templates, decks=decks)
