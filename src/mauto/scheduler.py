"""任务调度：计算下次执行时间、执行任务、失败重试。"""
from __future__ import annotations

import calendar
import json
import re
import threading
import time
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from . import db, llm
from .config import settings

FREQ_LABELS = {
    "daily": "每天",
    "workdays": "工作日",
    "weekly": "每周",
    "monthly": "每月",
}
WEEK_NAMES = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]

_scheduler_started = False
_scheduler_lock = None
_lock = threading.Lock()


def freq_text(task: dict) -> str:
    ft = task.get("freq_type")
    val = (task.get("freq_value") or "").strip()
    run_time = task.get("run_time", "08:00")
    if ft == "weekly":
        days = [WEEK_NAMES[int(v)] for v in val.split(",") if v.isdigit() and 0 <= int(v) < 7]
        return f"每周{'、'.join(days) or '一'} {run_time}"
    if ft == "monthly":
        return f"每月 {val or 1} 日 {run_time}"
    return f"{FREQ_LABELS.get(ft, '每天')} {run_time}"


def compute_next_run(task: dict, after: datetime | None = None) -> str:
    tz = ZoneInfo(task.get("timezone") or "Asia/Shanghai")
    now = (after or datetime.now(timezone.utc)).astimezone(tz)
    hour, minute = (int(x) for x in (task.get("run_time") or "08:00").split(":"))
    ft = task.get("freq_type", "daily")
    val = (task.get("freq_value") or "").strip()

    candidate = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if candidate <= now:
        candidate += timedelta(days=1)
        candidate = candidate.replace(hour=hour, minute=minute)

    if ft == "daily":
        pass
    elif ft == "workdays":
        while candidate.weekday() >= 5:
            candidate += timedelta(days=1)
    elif ft == "weekly":
        days = {int(v) for v in val.split(",") if v.isdigit()}
        if not days:
            days = {0}
        guard = 0
        while candidate.weekday() not in days and guard < 14:
            candidate += timedelta(days=1)
            guard += 1
    elif ft == "monthly":
        day = max(1, min(28, int(val) if val.isdigit() else 1))
        last_day = calendar.monthrange(candidate.year, candidate.month)[1]
        candidate = candidate.replace(day=min(day, last_day))
        if candidate <= now:
            year, month = (candidate.year + 1, 1) if candidate.month == 12 else (candidate.year, candidate.month + 1)
            last_day = calendar.monthrange(year, month)[1]
            candidate = candidate.replace(year=year, month=month, day=min(day, last_day))
    return db.iso(candidate)


def summarize_result(result: str, limit: int = 320) -> str:
    """把执行结果压缩成可读摘要：优先取 Markdown 表格的数据行（如单词列表），否则取正文要点。"""
    text = (result or "").strip()
    if not text:
        return ""
    rows: list[list[str]] = []
    for line in text.splitlines():
        s = line.strip()
        if not s.startswith("|"):
            continue
        cells = [c.strip() for c in s.strip("|").split("|")]
        if not any(cells):
            continue
        if all(set(c) <= set("-: ") for c in cells if c):
            continue  # 表头分隔行
        rows.append(cells)

    parts: list[str] = []
    if len(rows) >= 2:
        for cells in rows[1:6]:
            clean = [c for c in cells if c]
            if clean:
                parts.append(" ".join(clean))
    else:
        for line in text.splitlines():
            s = re.sub(r"^[#>\-*\s]+", "", line).strip().replace("**", "").replace("`", "")
            if not s or set(s) <= set("-|: "):
                continue
            parts.append(s)
            if sum(len(p) for p in parts) > limit:
                break
    summary = "；".join(parts).strip()
    if not summary:
        summary = re.sub(r"\s+", " ", text)
    return summary[:limit]


def _ensure_output_conversation(task: dict) -> str:
    """每个任务复用一个「任务产出」会话，执行结果以消息形式落在其中。"""
    conv_id = task.get("output_conversation_id")
    if conv_id:
        exists = db.q1(
            "SELECT id FROM conversation WHERE id = ? AND user_id = ?", (conv_id, task["user_id"])
        )
        if exists:
            return conv_id
    now = db.now_utc()
    conv_id = db.uid("c_")
    db.insert("conversation", {
        "id": conv_id, "user_id": task["user_id"],
        "title": f"任务产出 · {task['name']}"[:60],
        "project_id": None, "model": "MAuto-Air",
        "is_pinned": 0, "is_archived": 0, "created_at": now, "updated_at": now,
    })
    db.update("task", task["id"], {"output_conversation_id": conv_id})
    task["output_conversation_id"] = conv_id
    return conv_id


def _publish_output(task: dict, result: str, summary: str) -> tuple[str, str]:
    """把本次执行产出写入任务会话，返回 (conversation_id, message_id)。"""
    conv_id = _ensure_output_conversation(task)
    now = db.now_utc()
    tz = ZoneInfo(task.get("timezone") or "Asia/Shanghai")
    stamp = datetime.now(timezone.utc).astimezone(tz).strftime("%Y-%m-%d %H:%M")
    content = f"**{task['name']}** · {stamp} 执行完成\n\n> {summary}\n\n{result}"
    msg_id = db.uid("m_")
    db.insert("message", {
        "id": msg_id, "conversation_id": conv_id, "role": "assistant", "content": content,
        "tokens": llm.estimate_tokens(content), "status": "done", "model": "MAuto-Air",
        "feedback": 0, "citations_json": "[]", "plugin_calls_json": "[]", "reasoning": "",
        "attachments_json": "[]", "created_at": now,
    })
    db.update("conversation", conv_id, {"updated_at": now})
    return conv_id, msg_id


def execute_task(task: dict, *, manual: bool = False, attempt: int = 1) -> dict:
    started = time.time()
    status, result, error = "success", "", ""
    try:
        result = llm.generate_once(task["prompt"], model="MAuto-Air")
        time.sleep(0.35)
        if "[fail]" in (task.get("prompt") or ""):
            raise RuntimeError("模型服务暂时不可用")
    except Exception as exc:  # noqa: BLE001 - 失败需落地记录
        status, error = "failed", f"{type(exc).__name__}: {exc}"
    duration = int((time.time() - started) * 1000)

    now = db.now_utc()
    summary = summarize_result(result) if status == "success" else ""
    conversation_id = message_id = None
    if status == "success":
        conversation_id, message_id = _publish_output(task, result, summary)

    run_id = db.uid("tr_")
    db.insert("task_run", {
        "id": run_id, "task_id": task["id"], "run_at": now, "status": status,
        "duration_ms": duration, "result_content": result, "summary": summary,
        "conversation_id": conversation_id, "message_id": message_id,
        "error_msg": error, "attempt": attempt,
    })
    # 仅保留最近 30 条执行记录
    db.ex(
        "DELETE FROM task_run WHERE task_id = ? AND id NOT IN "
        "(SELECT id FROM task_run WHERE task_id = ? ORDER BY run_at DESC LIMIT 30)",
        (task["id"], task["id"]),
    )

    updates: dict = {"last_run_at": now, "last_status": status, "updated_at": now}
    if status == "success":
        updates.update({"last_result": result[:5000], "fail_count": 0,
                        "retry_count": 0, "retry_at": None,
                        "next_run_at": compute_next_run(task)})
        if task.get("output_format") in ("file", "markdown"):
            path = db.EXPORT_DIR / f"task_{task['id']}_{int(time.time())}.md"
            path.write_text(f"# {task['name']}\n\n{result}", encoding="utf-8")
        _notify(
            task, f"「{task['name']}」执行完成", summary or result[:200],
            "conversation" if conversation_id else "task",
            conversation_id or task["id"],
        )
    else:
        retry_count = attempt
        fail_count = (task.get("fail_count") or 0) + 1
        if retry_count <= 2:
            updates.update({
                "retry_count": retry_count, "fail_count": fail_count,
                "retry_at": db.iso(datetime.now(timezone.utc) + timedelta(minutes=5)),
            })
        else:
            paused = fail_count >= 3
            updates.update({
                "retry_count": 0, "fail_count": fail_count, "retry_at": None,
                "next_run_at": compute_next_run(task),
                "status": "paused" if paused else "enabled",
            })
            _notify(task, "任务执行失败" + ("，已自动暂停" if paused else ""),
                    f"{task['name']} 连续失败 {fail_count} 次：{error}。请检查任务配置。", "task", task["id"])
    db.update("task", task["id"], updates)
    task.update(updates)
    return {
        "status": status, "duration_ms": duration, "result": result, "error": error,
        "run_id": run_id, "run_at": now, "summary": summary,
        "conversation_id": conversation_id, "message_id": message_id,
    }


def _notify(task: dict, title: str, body: str, ref_type: str, ref_id: str) -> None:
    notify_types = db.jload(task.get("notify_types"), ["inbox"])
    if "inbox" in notify_types or not notify_types:
        db.insert("notification", {
            "id": db.uid("n_"), "user_id": task["user_id"], "type": "task",
            "title": title, "body": body, "ref_type": ref_type, "ref_id": ref_id,
            "is_read": 0, "created_at": db.now_utc(),
        })
    if "email" in notify_types or "wechat" in notify_types:
        channels = [c for c in ("email", "wechat") if c in notify_types]
        db.insert("notification", {
            "id": db.uid("n_"), "user_id": task["user_id"], "type": "task_channel",
            "title": f"[{'/'.join(channels)}] {title}",
            "body": "已按通知方式发送（演示环境仅记录发送动作）", "ref_type": ref_type,
            "ref_id": ref_id, "is_read": 0, "created_at": db.now_utc(),
        })


def _loop() -> None:
    while True:
        try:
            now = db.now_utc()
            due = db.q(
                "SELECT * FROM task WHERE status = 'enabled' AND next_run_at IS NOT NULL "
                "AND next_run_at <= ? ORDER BY next_run_at ASC LIMIT 20", (now,)
            )
            for task in due:
                execute_task(task)
            retries = db.q(
                "SELECT * FROM task WHERE status = 'enabled' AND retry_at IS NOT NULL AND retry_at <= ?",
                (now,),
            )
            for task in retries:
                db.ex("UPDATE task SET retry_at = NULL WHERE id = ?", (task["id"],))
                execute_task(task, attempt=(task.get("retry_count") or 0) + 1)
        except Exception:  # noqa: BLE001 - 调度线程不能退出
            pass
        time.sleep(15)


def start_scheduler() -> None:
    """启动调度线程；多 worker 部署时通过文件锁保证只有一个进程执行任务。"""
    global _scheduler_started, _scheduler_lock
    if not settings.enable_scheduler:
        print("[mauto] MAUTO_ENABLE_SCHEDULER=0，跳过任务调度", flush=True)
        return
    with _lock:
        if _scheduler_started:
            return
        lock = db.acquire_lock("scheduler", blocking=False)
        if lock is None:
            print("[mauto] 调度器已由其他 worker 持有，本进程跳过", flush=True)
            return
        _scheduler_lock = lock
        _scheduler_started = True
        thread = threading.Thread(target=_loop, name="mauto-scheduler", daemon=True)
        thread.start()


TASK_TEMPLATES = [
    {
        "id": "tpl_words", "name": "每日英文单词", "freq_type": "daily", "freq_value": "",
        "run_time": "08:00",
        "prompt": "每天推荐 5 个高频英文单词，包含音标、词性、释义、例句和记忆建议，输出 Markdown 表格。",
        "desc": "每天学习 5 个实用单词，含例句与记忆法",
    },
    {
        "id": "tpl_ai_news", "name": "每日 AI 资讯", "freq_type": "daily", "freq_value": "",
        "run_time": "08:30",
        "prompt": "汇总今日 AI 领域的重要资讯，输出 3 条主线、关键数据和启示，控制在 800 字以内。",
        "desc": "每天 8:30 获取 AI 行业要闻",
    },
    {
        "id": "tpl_industry", "name": "每日行业早报", "freq_type": "workdays", "freq_value": "",
        "run_time": "09:00",
        "prompt": "生成一份行业早报：今日三条主线、关键数据表、对我方业务的启示。",
        "desc": "工作日推送，快速掌握行业动态",
    },
    {
        "id": "tpl_todo", "name": "每日待办提醒", "freq_type": "daily", "freq_value": "",
        "run_time": "09:30",
        "prompt": "根据今天的日期生成一份待办清单建议，按重要紧急四象限组织，并给出时间安排。",
        "desc": "每天梳理优先级，安排一天节奏",
    },
    {
        "id": "tpl_weekly", "name": "每周工作总结", "freq_type": "weekly", "freq_value": "4",
        "run_time": "18:00",
        "prompt": "生成本周工作总结模板：本周进展、关键数据、问题与风险、下周计划，输出 Markdown。",
        "desc": "每周五下班前自动生成周报初稿",
    },
]


def task_out(task: dict) -> dict:
    return {
        "id": task["id"], "name": task["name"], "prompt": task["prompt"],
        "freq_type": task["freq_type"], "freq_value": task["freq_value"],
        "freq_text": freq_text(task), "run_time": task["run_time"],
        "timezone": task["timezone"],
        "notify_types": db.jload(task.get("notify_types"), ["inbox"]),
        "output_format": task.get("output_format", "chat"),
        "status": task["status"], "next_run_at": task.get("next_run_at"),
        "last_run_at": task.get("last_run_at"), "last_status": task.get("last_status"),
        "last_result": (task.get("last_result") or "")[:300],
        "output_conversation_id": task.get("output_conversation_id"),
        "fail_count": task.get("fail_count", 0),
        "created_at": task["created_at"], "updated_at": task.get("updated_at"),
    }
