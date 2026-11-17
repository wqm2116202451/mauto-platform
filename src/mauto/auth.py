"""账号体系：注册、登录、令牌、资料与偏好。"""
from __future__ import annotations

import hashlib
import secrets

from . import db
from .config import TOKEN_COOKIE_NAME, settings
from .deps import ApiError

TOKEN_TTL_DAYS = 30


def hash_password(password: str, salt: str) -> str:
    return hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), 120_000).hex()


def create_user(email: str, password: str, nickname: str) -> dict:
    email = (email or "").strip().lower()
    if "@" not in email or len(email) < 5:
        raise ApiError("请输入有效的邮箱地址")
    if len(password or "") < 6:
        raise ApiError("密码至少 6 位")
    if db.q1("SELECT id FROM user WHERE email = ?", (email,)):
        raise ApiError("该邮箱已注册")
    uid = db.uid("u_")
    salt = secrets.token_hex(8)
    user = {
        "id": uid,
        "nickname": (nickname or email.split("@")[0])[:32],
        "email": email,
        "password_hash": hash_password(password, salt),
        "salt": salt,
        "avatar": "",
        "status": "active",
        "prefs_json": db_json_default(),
        "created_at": db.now_utc(),
    }
    db.insert("user", user)
    return user


def db_json_default() -> str:
    import json
    return json.dumps({
        "model": "MAuto-Air",
        "web_search": False,
        "deep_think": False,
        "theme": "light",
        "language": "zh-CN",
        "sidebar_collapsed": False,
    }, ensure_ascii=False)


def authenticate(email: str, password: str) -> dict:
    email = (email or "").strip().lower()
    row = db.q1("SELECT * FROM user WHERE email = ?", (email,))
    if not row or row["password_hash"] != hash_password(password or "", row["salt"]):
        raise ApiError("邮箱或密码不正确", code=401, status_code=401)
    if row.get("status") != "active":
        raise ApiError("账号已被禁用", code=403, status_code=403)
    return row


def issue_token(user_id: str) -> str:
    token = secrets.token_urlsafe(32)
    db.insert("token", {"token": token, "user_id": user_id, "created_at": db.now_utc()})
    return token


def set_token_cookie(response, token: str) -> None:
    """登录态 Cookie：secure / samesite / domain 均由环境变量控制。"""
    response.set_cookie(
        TOKEN_COOKIE_NAME,
        token,
        max_age=TOKEN_TTL_DAYS * 86400,
        httponly=True,
        secure=settings.cookie_secure,
        samesite=settings.cookie_samesite,
        domain=settings.cookie_domain or None,
        path="/",
    )


def clear_token_cookie(response) -> None:
    response.delete_cookie(
        TOKEN_COOKIE_NAME,
        domain=settings.cookie_domain or None,
        path="/",
        secure=settings.cookie_secure,
        samesite=settings.cookie_samesite,
    )


def public_user(user: dict) -> dict:
    return {
        "id": user["id"],
        "nickname": user["nickname"],
        "email": user["email"],
        "avatar": user.get("avatar") or "",
        "status": user.get("status"),
        "prefs": db.jload(user.get("prefs_json"), {}),
        "created_at": user.get("created_at"),
    }
