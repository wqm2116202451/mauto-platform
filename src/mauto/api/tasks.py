"""任务模块接口。"""
from __future__ import annotations

import json
from datetime import datetime

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel

from .. import db, scheduler
from ..deps import ApiError, current_user, log_event, ok, paginate

router = APIRouter(tags=["tasks"])

MAX_ENABLED_TASKS = 20


class TaskIn(BaseModel):
    name: str
    prompt: str
    freq_type: str = "daily"
    freq_value: str = ""
    run_time: str = "08:00"
    timezone: str = "Asia/Shanghai"
    notify_types: list[str] = ["inbox"]
    output_format: str = "chat"


class TaskPatch(BaseModel):
    name: str | None = None
    prompt: str | None = None
    freq_type: str | None = None
    freq_value: str | None = None
    run_time: str | None = None
    timezone: str | None = None
    notify_types: list[str] | None = None
    output_format: str | None = None
    status: str | None = None


def _validate(payload: TaskIn) -> None:
    name = payload.name.strip()
    if not name:
        raise ApiError("请输入任务名称")
    if len(name) > 30:
        raise ApiError("任务名称不能超过 30 字")
    if not payload.prompt.strip():
        raise ApiError("请输入执行指令")
    if len(payload.prompt) > 2000:
        raise ApiError("执行指令不能超过 2000 字")
    if payload.freq_type not in ("daily", "workdays", "weekly", "monthly"):
        raise ApiError("执行频率不正确")
    if payload.freq_type == "weekly" and not payload.freq_value:
        raise ApiError("请选择每周执行的日期")
    if payload.freq_type == "monthly":
        if not payload.freq_value.isdigit() or not 1 <= int(payload.freq_value) <= 28:
            raise ApiError("每月执行日期需在 1-28 日之间")
    try:
        hh, mm = (int(x) for x in payload.run_time.split(":"))
        assert 0 <= hh <= 23 and 0 <= mm <= 59
    except (ValueError, AssertionError):
        raise ApiError("执行时间格式应为 HH:MM") from None
    valid_notify = {"inbox", "email", "wechat"}
    if not set(payload.notify_types) & valid_notify:
        raise ApiError("请至少选择一种通知方式")


def _get(task_id: str, user_id: str) -> dict:
    row = db.q1("SELECT * FROM task WHERE id = ? AND user_id = ?", (task_id, user_id))
    if not row:
        raise ApiError("任务不存在", code=404, status_code=404)
    return row


@router.get("/task-templates")
def templates(user: dict = Depends(current_user)):
    return ok(scheduler.TASK_TEMPLATES)


@router.get("/tasks")
def list_tasks(status: str = "", page: int = Query(1, ge=1), page_size: int = Query(50, ge=1, le=100),
               user: dict = Depends(current_user)):
    where, args = ["user_id = ?"], [user["id"]]
    if status in ("enabled", "paused"):
        where.append("status = ?")
        args.append(status)
    cond = " AND ".join(where)
    total = db.q1(f"SELECT COUNT(*) AS n FROM task WHERE {cond}", args)["n"]
    rows = db.q(
        f"SELECT * FROM task WHERE {cond} ORDER BY created_at DESC LIMIT ? OFFSET ?",
        [*args, page_size, (page - 1) * page_size],
    )
    enabled = db.q1("SELECT COUNT(*) AS n FROM task WHERE user_id = ? AND status='enabled'", (user["id"],))["n"]
    return ok({**paginate([scheduler.task_out(r) for r in rows], page, page_size, total),
               "enabled_count": enabled, "max_enabled": MAX_ENABLED_TASKS})


@router.post("/tasks")
def create_task(payload: TaskIn, user: dict = Depends(current_user)):
    _validate(payload)
    enabled = db.q1("SELECT COUNT(*) AS n FROM task WHERE user_id = ? AND status='enabled'", (user["id"],))["n"]
    if enabled >= MAX_ENABLED_TASKS:
        raise ApiError("已达任务上限（20 个启用中任务），请先暂停或删除其他任务")
    dup = db.q1(
        "SELECT id FROM task WHERE user_id = ? AND name = ? AND freq_type = ? AND freq_value = ? AND run_time = ?",
        (user["id"], payload.name.strip(), payload.freq_type, payload.freq_value, payload.run_time),
    )
    if dup:
        raise ApiError("已存在相同名称与执行时间的任务")
    task = {
        "id": db.uid("t_"), "user_id": user["id"], "name": payload.name.strip(),
        "prompt": payload.prompt.strip(), "freq_type": payload.freq_type,
        "freq_value": payload.freq_value, "run_time": payload.run_time,
        "timezone": payload.timezone or "Asia/Shanghai",
        "notify_types": json.dumps(payload.notify_types, ensure_ascii=False),
        "output_format": payload.output_format, "status": "enabled",
        "next_run_at": None, "last_run_at": None, "last_status": None, "last_result": None,
        "fail_count": 0, "retry_count": 0, "retry_at": None,
        "created_at": db.now_utc(), "updated_at": db.now_utc(),
    }
    task["next_run_at"] = scheduler.compute_next_run(task)
    db.insert("task", task)
    scheduler.start_scheduler()
    log_event(user["id"], "create_task", {"task_id": task["id"], "freq": task["freq_type"]})
    return ok(scheduler.task_out(task))


@router.patch("/tasks/{task_id}")
def patch_task(task_id: str, payload: TaskPatch, user: dict = Depends(current_user)):
    task = _get(task_id, user["id"])
    data: dict = {}
    if payload.status is not None:
        if payload.status not in ("enabled", "paused"):
            raise ApiError("状态不合法")
        if payload.status == "enabled" and task["status"] != "enabled":
            enabled = db.q1("SELECT COUNT(*) AS n FROM task WHERE user_id = ? AND status='enabled'", (user["id"],))["n"]
            if enabled >= MAX_ENABLED_TASKS:
                raise ApiError("已达任务上限（20 个启用中任务）")
        data["status"] = payload.status
        if payload.status == "enabled":
            data["next_run_at"] = scheduler.compute_next_run(task)
            data["retry_at"] = None
            data["fail_count"] = 0
    for field in ("name", "prompt", "freq_type", "freq_value", "run_time", "timezone", "output_format"):
        value = getattr(payload, field)
        if value is not None:
            data[field] = value.strip() if isinstance(value, str) else value
    if payload.notify_types is not None:
        data["notify_types"] = json.dumps(payload.notify_types, ensure_ascii=False)
    if data:
        if "name" in data and len(data["name"]) > 30:
            raise ApiError("任务名称不能超过 30 字")
        merged = {**task, **data}
        if any(k in data for k in ("freq_type", "freq_value", "run_time", "timezone")) and merged["status"] == "enabled":
            data["next_run_at"] = scheduler.compute_next_run(merged)
        data["updated_at"] = db.now_utc()
        db.update("task", task["id"], data)
    return ok(scheduler.task_out(_get(task_id, user["id"])))


@router.delete("/tasks/{task_id}")
def delete_task(task_id: str, user: dict = Depends(current_user)):
    task = _get(task_id, user["id"])
    db.ex("DELETE FROM task WHERE id = ?", (task["id"],))
    db.ex("DELETE FROM task_run WHERE task_id = ?", (task["id"],))
    return ok({"deleted": True})


@router.post("/tasks/{task_id}/run")
def run_task(task_id: str, user: dict = Depends(current_user)):
    task = _get(task_id, user["id"])
    result = scheduler.execute_task(task, manual=True)
    log_event(user["id"], "run_task", {"task_id": task_id, "status": result["status"]})
    return ok(result)


@router.get("/tasks/{task_id}/runs")
def task_runs(task_id: str, page: int = Query(1, ge=1), page_size: int = Query(20, ge=1, le=50),
              user: dict = Depends(current_user)):
    task = _get(task_id, user["id"])
    total = db.q1("SELECT COUNT(*) AS n FROM task_run WHERE task_id = ?", (task["id"],))["n"]
    rows = db.q(
        "SELECT * FROM task_run WHERE task_id = ? ORDER BY run_at DESC LIMIT ? OFFSET ?",
        (task["id"], page_size, (page - 1) * page_size),
    )
    items = [{
        "id": r["id"], "run_at": r["run_at"], "status": r["status"],
        "duration_ms": r["duration_ms"], "result_content": r["result_content"],
        "summary": r.get("summary") or "", "conversation_id": r.get("conversation_id"),
        "message_id": r.get("message_id"), "error_msg": r["error_msg"], "attempt": r["attempt"],
    } for r in rows]
    return ok(paginate(items, page, page_size, total))
