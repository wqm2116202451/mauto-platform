"""账号、全局搜索、通知中心、设置与元数据接口。"""
from __future__ import annotations

import json

from fastapi import APIRouter, Depends, Query, Response
from pydantic import BaseModel

from .. import auth, db, llm
from ..config import settings
from ..deps import ApiError, current_user, log_event, ok, paginate

router = APIRouter(tags=["misc"])


class RegisterIn(BaseModel):
    email: str
    password: str
    nickname: str = ""


class LoginIn(BaseModel):
    email: str
    password: str


class ProfileIn(BaseModel):
    nickname: str | None = None
    avatar: str | None = None
    prefs: dict | None = None


class PasswordIn(BaseModel):
    old_password: str
    new_password: str


class ReadIn(BaseModel):
    is_read: bool = True


RECOMMEND_QUESTIONS = [
    {"icon": "📊", "title": "写一份季度复盘", "desc": "梳理数据、问题与下一步", "prompt": "帮我写一份本季度工作复盘，包含关键数据、问题分析和下一步计划。"},
    {"icon": "🔍", "title": "行业趋势速览", "desc": "三条主线 + 关键数据", "prompt": "总结一下最近 AI 行业的三个主要趋势，并给出关键数据。"},
    {"icon": "📽️", "title": "生成 PPT 大纲", "desc": "12 页，可直接编辑", "prompt": "帮我生成一份主题为「AI 提升团队效率」的 12 页 PPT 大纲。"},
    {"icon": "🧠", "title": "解释一个概念", "desc": "用例子讲清楚", "prompt": "用通俗的例子解释什么是「智能体（Agent）」，以及它能做什么。"},
    {"icon": "✍️", "title": "润色一段文字", "desc": "更专业、更简洁", "prompt": "帮我把这段话润色得更专业、更简洁：我们做了很多事情，效果还不错，后面会继续努力。"},
    {"icon": "💡", "title": "拆解一个目标", "desc": "可执行的行动清单", "prompt": "帮我把「两个月内提升团队交付效率 30%」拆解成可执行的行动清单。"},
]


@router.get("/health")
def health():
    return ok({"status": "ok", "service": "mauto-platform", "version": "1.0.0",
               "llm": "remote" if llm.API_KEY else "builtin-demo-engine"})


@router.get("/meta/config")
def meta_config():
    return ok({
        "models": llm.MODELS,
        "env": settings.env,
        "base_url": settings.base_url or None,
        "recommend_questions": RECOMMEND_QUESTIONS,
        "industries": db.q("SELECT id, name, icon FROM news_category ORDER BY sort"),
        "task_templates": __import__("mauto.scheduler", fromlist=["TASK_TEMPLATES"]).TASK_TEMPLATES,
        "plugin_categories": ["全部", "办公效率", "数据分析", "内容创作", "生活服务", "开发工具"],
        "ppt_categories": ["全部", "商务汇报", "教育教学", "科技互联网", "医疗健康", "政府政务", "简约通用"],
        "ppt_styles": ["全部", "简约", "科技", "国潮", "扁平"],
        "versions": {"web": "V1.0.0", "api": "v1"},
        "events": ["page_view", "send_message", "stop_generate", "create_task", "run_task",
                   "click_news", "install_plugin", "generate_ppt", "export_ppt", "create_project"],
    })


# ------------------------------------------------------------------ 账号
@router.post("/auth/register")
def register(payload: RegisterIn, response: Response):
    user = auth.create_user(payload.email, payload.password, payload.nickname)
    token = auth.issue_token(user["id"])
    auth.set_token_cookie(response, token)
    db.insert("notification", {
        "id": db.uid("n_"), "user_id": user["id"], "type": "system",
        "title": "欢迎加入 MAuto", "body": "从左侧「新聊天」开始提问，或在「任务」中创建第一个自动化任务。",
        "ref_type": "", "ref_id": "", "is_read": 0, "created_at": db.now_utc(),
    })
    return ok({"token": token, "user": auth.public_user(user)})


@router.post("/auth/login")
def login(payload: LoginIn, response: Response):
    user = auth.authenticate(payload.email, payload.password)
    token = auth.issue_token(user["id"])
    auth.set_token_cookie(response, token)
    return ok({"token": token, "user": auth.public_user(user)})


@router.post("/auth/logout")
def logout(response: Response, authorization: str = "", user: dict = Depends(current_user)):
    auth.clear_token_cookie(response)
    return ok({"logged_out": True})


@router.get("/auth/me")
def me(user: dict = Depends(current_user)):
    stats = {
        "conversations": db.q1("SELECT COUNT(*) AS n FROM conversation WHERE user_id = ?", (user["id"],))["n"],
        "tasks": db.q1("SELECT COUNT(*) AS n FROM task WHERE user_id = ?", (user["id"],))["n"],
        "projects": db.q1("SELECT COUNT(*) AS n FROM project WHERE owner_id = ? AND is_deleted = 0",
                          (user["id"],))["n"],
        "ppts": db.q1("SELECT COUNT(*) AS n FROM ppt_document WHERE user_id = ?", (user["id"],))["n"],
        "unread": db.q1("SELECT COUNT(*) AS n FROM notification WHERE user_id = ? AND is_read = 0",
                        (user["id"],))["n"],
    }
    return ok({**auth.public_user(user), "stats": stats})


@router.patch("/users/me")
def update_profile(payload: ProfileIn, user: dict = Depends(current_user)):
    data: dict = {}
    if payload.nickname is not None:
        nickname = payload.nickname.strip()
        if not nickname or len(nickname) > 32:
            raise ApiError("昵称需为 1~32 个字符")
        data["nickname"] = nickname
    if payload.avatar is not None:
        data["avatar"] = payload.avatar
    if payload.prefs is not None:
        merged = {**db.jload(user.get("prefs_json"), {}), **payload.prefs}
        data["prefs_json"] = json.dumps(merged, ensure_ascii=False)
    if data:
        db.update("user", user["id"], data)
    fresh = db.q1("SELECT * FROM user WHERE id = ?", (user["id"],))
    return ok(auth.public_user(fresh))


@router.post("/users/me/password")
def change_password(payload: PasswordIn, user: dict = Depends(current_user)):
    if user["password_hash"] != auth.hash_password(payload.old_password, user["salt"]):
        raise ApiError("原密码不正确")
    if len(payload.new_password) < 6:
        raise ApiError("新密码至少 6 位")
    import secrets
    salt = secrets.token_hex(8)
    db.update("user", user["id"], {
        "salt": salt, "password_hash": auth.hash_password(payload.new_password, salt)})
    return ok({"updated": True})


@router.get("/data/export")
def export_data(user: dict = Depends(current_user)):
    convs = db.q("SELECT * FROM conversation WHERE user_id = ?", (user["id"],))
    msgs = db.q(
        "SELECT m.* FROM message m JOIN conversation c ON c.id = m.conversation_id WHERE c.user_id = ?",
        (user["id"],),
    )
    return ok({
        "user": auth.public_user(user), "conversations": convs, "messages": msgs,
        "tasks": db.q("SELECT * FROM task WHERE user_id = ?", (user["id"],)),
        "projects": db.q("SELECT * FROM project WHERE owner_id = ?", (user["id"],)),
        "exported_at": db.now_utc(),
    })


@router.post("/data/clear-conversations")
def clear_conversations(user: dict = Depends(current_user)):
    convs = db.q("SELECT id FROM conversation WHERE user_id = ?", (user["id"],))
    for c in convs:
        db.ex("DELETE FROM message WHERE conversation_id = ?", (c["id"],))
    db.ex("DELETE FROM conversation WHERE user_id = ?", (user["id"],))
    return ok({"deleted": len(convs)})


# ------------------------------------------------------------------ 全局搜索
@router.get("/search")
def global_search(q: str = "", user: dict = Depends(current_user)):
    keyword = (q or "").strip()
    if not keyword:
        return ok({"q": "", "groups": [], "total": 0})
    like = f"%{keyword}%"
    groups = []

    convs = db.q(
        "SELECT id, title, updated_at FROM conversation WHERE user_id = ? AND title LIKE ? "
        "ORDER BY updated_at DESC LIMIT 5", (user["id"], like),
    )
    conv_ids = [c["id"] for c in convs]
    msg_hits = db.q(
        "SELECT m.id, m.conversation_id, m.content, c.title FROM message m "
        "JOIN conversation c ON c.id = m.conversation_id "
        "WHERE c.user_id = ? AND m.content LIKE ? ORDER BY m.created_at DESC LIMIT 5",
        (user["id"], like),
    )
    conv_items = [{"id": c["id"], "title": c["title"], "sub": (c["updated_at"] or "")[:10],
                   "route": f"#/chat/{c['id']}"} for c in convs]
    for m in msg_hits:
        if m["conversation_id"] in conv_ids:
            continue
        snippet = m["content"][:60]
        conv_items.append({"id": m["conversation_id"], "title": m["title"],
                           "sub": f"消息命中：{snippet}", "route": f"#/chat/{m['conversation_id']}"})
    if conv_items:
        groups.append({"type": "conversation", "name": "会话", "items": conv_items[:8]})

    tasks = db.q("SELECT id, name, prompt FROM task WHERE user_id = ? AND (name LIKE ? OR prompt LIKE ?) LIMIT 5",
                 (user["id"], like, like))
    if tasks:
        groups.append({"type": "task", "name": "任务", "items": [
            {"id": t["id"], "title": t["name"], "sub": t["prompt"][:40], "route": "#/tasks"} for t in tasks]})

    news = db.q("SELECT id, title, source FROM news WHERE status='online' AND (title LIKE ? OR summary LIKE ?) "
                "ORDER BY heat DESC LIMIT 5", (like, like))
    if news:
        groups.append({"type": "news", "name": "资讯", "items": [
            {"id": n["id"], "title": n["title"], "sub": n["source"], "route": f"#/news/{n['id']}"} for n in news]})

    plugins = db.q("SELECT id, name, description FROM plugin WHERE status='approved' AND "
                   "(name LIKE ? OR description LIKE ?) LIMIT 5", (like, like))
    if plugins:
        groups.append({"type": "plugin", "name": "插件", "items": [
            {"id": p["id"], "title": p["name"], "sub": p["description"][:40], "route": f"#/plugins/{p['id']}"}
            for p in plugins]})

    ppts = db.q("SELECT id, title FROM ppt_document WHERE user_id = ? AND title LIKE ? LIMIT 5",
                (user["id"], like))
    if ppts:
        groups.append({"type": "ppt", "name": "PPT", "items": [
            {"id": p["id"], "title": p["title"], "sub": "演示文稿", "route": f"#/ppt/{p['id']}"} for p in ppts]})

    projects = db.q(
        "SELECT id, name, description FROM project WHERE is_deleted = 0 AND (owner_id = ? OR id IN "
        "(SELECT project_id FROM project_member WHERE user_id = ?)) AND (name LIKE ? OR description LIKE ?) LIMIT 5",
        (user["id"], user["id"], like, like),
    )
    if projects:
        groups.append({"type": "project", "name": "项目", "items": [
            {"id": p["id"], "title": p["name"], "sub": p["description"][:40], "route": f"#/projects/{p['id']}"}
            for p in projects]})
    total = sum(len(g["items"]) for g in groups)
    return ok({"q": keyword, "groups": groups, "total": total})


# ------------------------------------------------------------------ 通知中心
@router.get("/notifications")
def notifications(page: int = Query(1, ge=1), page_size: int = Query(20, ge=1, le=50),
                  unread_only: int = 0, user: dict = Depends(current_user)):
    where, args = ["user_id = ?"], [user["id"]]
    if unread_only:
        where.append("is_read = 0")
    cond = " AND ".join(where)
    total = db.q1(f"SELECT COUNT(*) AS n FROM notification WHERE {cond}", args)["n"]
    rows = db.q(f"SELECT * FROM notification WHERE {cond} ORDER BY created_at DESC LIMIT ? OFFSET ?",
                [*args, page_size, (page - 1) * page_size])
    unread = db.q1("SELECT COUNT(*) AS n FROM notification WHERE user_id = ? AND is_read = 0",
                   (user["id"],))["n"]
    return ok({**paginate(rows, page, page_size, total), "unread": unread})


@router.get("/notifications/unread-count")
def unread_count(user: dict = Depends(current_user)):
    n = db.q1("SELECT COUNT(*) AS n FROM notification WHERE user_id = ? AND is_read = 0", (user["id"],))["n"]
    return ok({"unread": n})


@router.post("/notifications/read-all")
def read_all(user: dict = Depends(current_user)):
    db.ex("UPDATE notification SET is_read = 1 WHERE user_id = ?", (user["id"],))
    return ok({"unread": 0})


@router.post("/notifications/{notif_id}/read")
def read_one(notif_id: str, payload: ReadIn, user: dict = Depends(current_user)):
    db.ex("UPDATE notification SET is_read = ? WHERE id = ? AND user_id = ?",
          (1 if payload.is_read else 0, notif_id, user["id"]))
    return ok({"ok": True})


@router.get("/settings")
def get_settings(user: dict = Depends(current_user)):
    return ok({"prefs": db.jload(user.get("prefs_json"), {}), "user": auth.public_user(user)})


@router.patch("/settings")
def update_settings(payload: dict, user: dict = Depends(current_user)):
    allowed = {"model", "web_search", "deep_think", "theme", "language", "sidebar_collapsed"}
    merged = {**db.jload(user.get("prefs_json"), {}), **{k: v for k, v in payload.items() if k in allowed}}
    db.update("user", user["id"], {"prefs_json": json.dumps(merged, ensure_ascii=False)})
    return ok({"prefs": merged})


@router.post("/events")
def track_event(payload: dict, user: dict = Depends(current_user)):
    event = str(payload.get("event", ""))[:40]
    if event:
        log_event(user["id"], event, payload.get("payload") or {})
    return ok({"tracked": True})
