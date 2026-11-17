"""统一响应体、错误类型与登录态依赖。"""
from __future__ import annotations

import json
from typing import Any

from fastapi import Cookie, Header

from . import db
from .config import TOKEN_COOKIE_NAME


class ApiError(Exception):
    def __init__(self, message: str, code: int = 400, status_code: int = 400):
        super().__init__(message)
        self.message = message
        self.code = code
        self.status_code = status_code


def ok(data: Any = None, message: str = "ok") -> dict:
    return {"code": 0, "message": message, "data": data}


def paginate(items: list, page: int, page_size: int, total: int | None = None) -> dict:
    return {
        "list": items,
        "page": page,
        "page_size": page_size,
        "total": total if total is not None else len(items),
    }


def current_user(authorization: str | None = Header(default=None),
                 mauto_token: str | None = Cookie(default=None, alias=TOKEN_COOKIE_NAME)) -> dict:
    token = ""
    if authorization and authorization.lower().startswith("bearer "):
        token = authorization.split(" ", 1)[1].strip()
    elif mauto_token:
        token = mauto_token.strip()
    if not token:
        raise ApiError("未登录或登录已过期", code=401, status_code=401)
    row = db.q1(
        "SELECT u.* FROM token t JOIN user u ON u.id = t.user_id WHERE t.token = ?",
        (token,),
    )
    if not row:
        raise ApiError("未登录或登录已过期", code=401, status_code=401)
    row["prefs"] = db.jload(row.get("prefs_json"), {})
    return row


def user_prefs(user: dict) -> dict:
    return db.jload(user.get("prefs_json"), {}) if user else {}


def log_event(user_id: str | None, event: str, payload: dict | None = None) -> None:
    db.insert("event_log", {
        "id": db.uid("ev_"),
        "user_id": user_id,
        "event": event,
        "payload_json": json.dumps(payload or {}, ensure_ascii=False),
        "created_at": db.now_utc(),
    })
