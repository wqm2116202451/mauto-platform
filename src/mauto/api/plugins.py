"""插件市场与自建插件接口。"""
from __future__ import annotations

import json
import time

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel

from .. import db, llm
from ..deps import ApiError, current_user, log_event, ok, paginate

router = APIRouter(tags=["plugins"])

CATEGORIES = ["全部", "办公效率", "数据分析", "内容创作", "生活服务", "开发工具"]


class InstallPatch(BaseModel):
    status: str | None = None
    is_default: bool | None = None
    authorized: bool | None = None


class PluginIn(BaseModel):
    name: str
    icon: str = "🧩"
    description: str = ""
    detail: str = ""
    category: str = "办公效率"
    auth_type: str = "none"
    api_url: str = ""
    params: list[dict] = []
    examples: list[dict] = []
    permissions: list[str] = []


class DebugIn(BaseModel):
    params: dict = {}


class ReviewIn(BaseModel):
    action: str = "approve"
    reason: str = ""


def _installed(user_id: str) -> dict[str, dict]:
    rows = db.q("SELECT * FROM user_plugin WHERE user_id = ?", (user_id,))
    return {r["plugin_id"]: r for r in rows}


def plugin_out(row: dict, installed: dict | None = None) -> dict:
    up = (installed or {}).get(row["id"])
    author = db.q1("SELECT nickname FROM user WHERE id = ?", (row.get("author_id"),)) if row.get("author_id") else None
    return {
        "id": row["id"], "name": row["name"], "icon": row["icon"],
        "description": row["description"], "detail": row["detail"],
        "category": row["category"], "type": row["type"], "status": row["status"],
        "install_count": row["install_count"], "rating": row["rating"],
        "rating_count": row["rating_count"], "version": row["version"],
        "changelog": row["changelog"],
        "params": db.jload(row.get("params_json"), []),
        "examples": db.jload(row.get("examples_json"), []),
        "permissions": db.jload(row.get("permissions_json"), []),
        "auth_type": row["auth_type"], "api_url": row.get("api_url", ""),
        "author": author["nickname"] if author else "MAuto 官方",
        "installed": bool(up),
        "installed_status": up["status"] if up else None,
        "is_default": bool(up["is_default"]) if up else False,
        "authorized": bool(up["authorized"]) if up else False,
        "installed_at": up["installed_at"] if up else None,
        "reject_reason": row.get("reject_reason", ""),
        "created_at": row["created_at"],
    }


@router.get("/plugin-categories")
def categories():
    return ok(CATEGORIES)


@router.get("/plugins")
def market(
    q: str = "", category: str = "全部", sort: str = "hot",
    page: int = Query(1, ge=1), page_size: int = Query(24, ge=1, le=60),
    scope: str = "all",  # all | mine
    user: dict = Depends(current_user),
):
    installed = _installed(user["id"])
    where, args = ["p.status = 'approved'"], []
    if scope == "mine":
        where = ["p.author_id = ?", "p.status != 'offline'"]
        args = [user["id"]]
    if category and category != "全部":
        where.append("p.category = ?")
        args.append(category)
    if q.strip():
        where.append("(p.name LIKE ? OR p.description LIKE ?)")
        args += [f"%{q.strip()}%", f"%{q.strip()}%"]
    cond = " AND ".join(where)
    order = {"hot": "p.install_count DESC", "rating": "p.rating DESC",
             "new": "p.created_at DESC"}.get(sort, "p.install_count DESC")
    total = db.q1(f"SELECT COUNT(*) AS n FROM plugin p WHERE {cond}", args)["n"]
    rows = db.q(f"SELECT * FROM plugin p WHERE {cond} ORDER BY {order} LIMIT ? OFFSET ?",
                [*args, page_size, (page - 1) * page_size])
    items = []
    for r in rows:
        item = plugin_out(r, installed)
        item["is_mine"] = r.get("author_id") == user["id"]
        items.append(item)
    return ok({**paginate(items, page, page_size, total), "installed_count": len(installed)})


@router.get("/plugins/mine")
def my_plugins(user: dict = Depends(current_user)):
    installed = _installed(user["id"])
    rows = db.q(
        "SELECT p.* FROM plugin p JOIN user_plugin up ON up.plugin_id = p.id "
        "WHERE up.user_id = ? ORDER BY up.installed_at DESC", (user["id"],)
    )
    custom = db.q("SELECT * FROM plugin WHERE author_id = ? ORDER BY created_at DESC", (user["id"],))
    return ok({
        "installed": [plugin_out(r, installed) for r in rows],
        "created": [plugin_out(r, installed) for r in custom],
    })


@router.get("/plugin-calls")
def call_logs(page: int = Query(1, ge=1), page_size: int = Query(20, ge=1, le=50),
              user: dict = Depends(current_user)):
    total = db.q1("SELECT COUNT(*) AS n FROM plugin_call WHERE user_id = ?", (user["id"],))["n"]
    rows = db.q(
        "SELECT pc.*, p.name, p.icon FROM plugin_call pc LEFT JOIN plugin p ON p.id = pc.plugin_id "
        "WHERE pc.user_id = ? ORDER BY pc.created_at DESC LIMIT ? OFFSET ?",
        (user["id"], page_size, (page - 1) * page_size),
    )
    return ok(paginate(rows, page, page_size, total))


@router.get("/plugins/{plugin_id}")
def detail(plugin_id: str, user: dict = Depends(current_user)):
    row = db.q1("SELECT * FROM plugin WHERE id = ?", (plugin_id,))
    if not row:
        raise ApiError("插件不存在", code=404, status_code=404)
    if row["status"] != "approved" and row.get("author_id") != user["id"]:
        raise ApiError("插件未上架", code=403, status_code=403)
    item = plugin_out(row, _installed(user["id"]))
    item["is_mine"] = row.get("author_id") == user["id"]
    item["call_logs"] = db.q(
        "SELECT status, duration_ms, created_at FROM plugin_call WHERE plugin_id = ? AND user_id = ? "
        "ORDER BY created_at DESC LIMIT 5", (plugin_id, user["id"]),
    )
    return ok(item)


@router.post("/plugins/{plugin_id}/install")
def install(plugin_id: str, user: dict = Depends(current_user)):
    plugin = db.q1("SELECT * FROM plugin WHERE id = ?", (plugin_id,))
    if not plugin:
        raise ApiError("插件不存在", code=404, status_code=404)
    if plugin["status"] != "approved" and plugin.get("author_id") != user["id"]:
        raise ApiError("该插件尚未上架，无法安装")
    existing = db.q1("SELECT * FROM user_plugin WHERE user_id = ? AND plugin_id = ?", (user["id"], plugin_id))
    if existing:
        db.ex("UPDATE user_plugin SET status = 'enabled' WHERE id = ?", (existing["id"],))
    else:
        db.insert("user_plugin", {
            "id": db.uid("up_"), "user_id": user["id"], "plugin_id": plugin_id,
            "status": "enabled", "is_default": 0, "authorized": 0, "installed_at": db.now_utc(),
        })
        db.ex("UPDATE plugin SET install_count = install_count + 1 WHERE id = ?", (plugin_id,))
    log_event(user["id"], "install_plugin", {"plugin_id": plugin_id})
    return ok({"installed": True, "need_authorize": bool(db.jload(plugin.get("permissions_json"), []))})


@router.delete("/plugins/{plugin_id}/install")
def uninstall(plugin_id: str, user: dict = Depends(current_user)):
    db.ex("DELETE FROM user_plugin WHERE user_id = ? AND plugin_id = ?", (user["id"], plugin_id))
    db.ex("UPDATE plugin SET install_count = MAX(install_count - 1, 0) WHERE id = ?", (plugin_id,))
    return ok({"installed": False})


@router.patch("/plugins/{plugin_id}/install")
def patch_install(plugin_id: str, payload: InstallPatch, user: dict = Depends(current_user)):
    row = db.q1("SELECT * FROM user_plugin WHERE user_id = ? AND plugin_id = ?", (user["id"], plugin_id))
    if not row:
        raise ApiError("请先安装该插件")
    data = {}
    if payload.status is not None:
        if payload.status not in ("enabled", "disabled"):
            raise ApiError("状态不合法")
        data["status"] = payload.status
    if payload.is_default is not None:
        if payload.is_default:
            db.ex("UPDATE user_plugin SET is_default = 0 WHERE user_id = ?", (user["id"],))
        data["is_default"] = 1 if payload.is_default else 0
    if payload.authorized is not None:
        data["authorized"] = 1 if payload.authorized else 0
    if data:
        db.update("user_plugin", row["id"], data)
    updated = db.q1("SELECT * FROM user_plugin WHERE id = ?", (row["id"],))
    return ok({"status": updated["status"], "is_default": bool(updated["is_default"]),
               "authorized": bool(updated["authorized"])})


@router.post("/plugins")
def create_plugin(payload: PluginIn, user: dict = Depends(current_user)):
    if not payload.name.strip():
        raise ApiError("请输入插件名称")
    if len(payload.name) > 20:
        raise ApiError("插件名称不能超过 20 字")
    now = db.now_utc()
    pid = db.uid("p_")
    db.insert("plugin", {
        "id": pid, "name": payload.name.strip(), "icon": payload.icon or "🧩",
        "description": payload.description.strip(), "detail": payload.detail.strip(),
        "category": payload.category, "type": "custom", "author_id": user["id"],
        "status": "draft", "install_count": 0, "rating": 5.0, "rating_count": 0,
        "version": "0.1.0", "changelog": "首个版本",
        "params_json": json.dumps(payload.params, ensure_ascii=False),
        "examples_json": json.dumps(payload.examples, ensure_ascii=False),
        "permissions_json": json.dumps(payload.permissions, ensure_ascii=False),
        "auth_type": payload.auth_type, "api_url": payload.api_url,
        "reject_reason": "", "created_at": now, "updated_at": now,
    })
    return ok(plugin_out(db.q1("SELECT * FROM plugin WHERE id = ?", (pid,))))


@router.patch("/plugins/{plugin_id}")
def update_plugin(plugin_id: str, payload: PluginIn, user: dict = Depends(current_user)):
    row = db.q1("SELECT * FROM plugin WHERE id = ? AND author_id = ?", (plugin_id, user["id"]))
    if not row:
        raise ApiError("插件不存在或无权限", code=404, status_code=404)
    db.update("plugin", plugin_id, {
        "name": payload.name.strip(), "icon": payload.icon, "description": payload.description,
        "detail": payload.detail, "category": payload.category, "auth_type": payload.auth_type,
        "api_url": payload.api_url,
        "params_json": json.dumps(payload.params, ensure_ascii=False),
        "examples_json": json.dumps(payload.examples, ensure_ascii=False),
        "permissions_json": json.dumps(payload.permissions, ensure_ascii=False),
        "status": "draft" if row["status"] == "rejected" else row["status"],
        "updated_at": db.now_utc(),
    })
    return ok(plugin_out(db.q1("SELECT * FROM plugin WHERE id = ?", (plugin_id,))))


@router.post("/plugins/{plugin_id}/debug")
def debug_plugin(plugin_id: str, payload: DebugIn, user: dict = Depends(current_user)):
    row = db.q1("SELECT * FROM plugin WHERE id = ?", (plugin_id,))
    if not row:
        raise ApiError("插件不存在", code=404, status_code=404)
    params = db.jload(row.get("params_json"), [])
    for p in params:
        if p.get("required") and not str(payload.params.get(p["name"], "")).strip():
            raise ApiError(f"缺少必填参数：{p['name']}")
    started = time.time()
    time.sleep(0.12)
    output = llm.simulate_plugin(row, payload.params)
    duration = int((time.time() - started) * 1000)
    db.insert("plugin_call", {
        "id": db.uid("pc_"), "user_id": user["id"], "plugin_id": plugin_id,
        "conversation_id": None, "input_json": json.dumps(payload.params, ensure_ascii=False),
        "output": output, "duration_ms": duration, "status": "success", "created_at": db.now_utc(),
    })
    return ok({"output": output, "duration_ms": duration, "status": "success",
               "endpoint": row.get("api_url") or "（未配置，使用内置沙箱执行）"})


@router.post("/plugins/{plugin_id}/publish")
def publish(plugin_id: str, user: dict = Depends(current_user)):
    row = db.q1("SELECT * FROM plugin WHERE id = ? AND author_id = ?", (plugin_id, user["id"]))
    if not row:
        raise ApiError("插件不存在或无权限", code=404, status_code=404)
    if not row["description"]:
        raise ApiError("发布前请填写插件简介")
    db.update("plugin", plugin_id, {"status": "pending", "reject_reason": "", "updated_at": db.now_utc()})
    return ok({"status": "pending"})


@router.post("/plugins/{plugin_id}/review")
def review(plugin_id: str, payload: ReviewIn, user: dict = Depends(current_user)):
    """模拟平台审核动作（演示用，正常应由运营后台调用）。"""
    row = db.q1("SELECT * FROM plugin WHERE id = ?", (plugin_id,))
    if not row:
        raise ApiError("插件不存在", code=404, status_code=404)
    if payload.action == "approve":
        db.update("plugin", plugin_id, {"status": "approved", "reject_reason": ""})
        db.insert("notification", {
            "id": db.uid("n_"), "user_id": row["author_id"], "type": "plugin",
            "title": f"插件「{row['name']}」审核通过", "body": "已上架到插件市场，可在对话中勾选调用。",
            "ref_type": "plugin", "ref_id": plugin_id, "is_read": 0, "created_at": db.now_utc(),
        })
    else:
        db.update("plugin", plugin_id, {"status": "rejected", "reject_reason": payload.reason or "不符合上架规范"})
        db.insert("notification", {
            "id": db.uid("n_"), "user_id": row["author_id"], "type": "plugin",
            "title": f"插件「{row['name']}」审核未通过", "body": payload.reason or "不符合上架规范",
            "ref_type": "plugin", "ref_id": plugin_id, "is_read": 0, "created_at": db.now_utc(),
        })
    return ok({"status": db.q1("SELECT status FROM plugin WHERE id = ?", (plugin_id,))["status"]})
