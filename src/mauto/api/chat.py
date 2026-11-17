"""会话、消息与流式对话接口。"""
from __future__ import annotations

import asyncio
import json
import re
import time
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, Depends, File, Query, Request, UploadFile
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel

from .. import db, fileutil, llm, websearch
from ..deps import ApiError, current_user, log_event, ok, paginate

router = APIRouter(tags=["chat"])

STOP_FLAGS: dict[str, bool] = {}
UPLOAD_ROOT = Path(db.UPLOAD_DIR)
EXPORT_ROOT = Path(db.EXPORT_DIR)


class ConversationIn(BaseModel):
    title: str | None = None
    project_id: str | None = None
    model: str | None = None


class ConversationPatch(BaseModel):
    title: str | None = None
    is_pinned: bool | None = None
    is_archived: bool | None = None


class ChatIn(BaseModel):
    conversation_id: str | None = None
    content: str = ""
    model: str = "MAuto-Air"
    web_search: bool = False
    deep_think: bool = False
    plugin_ids: list[str] = []
    attachments: list[dict] = []
    regenerate: bool = False
    message_id: str | None = None
    edit_message_id: str | None = None
    project_id: str | None = None


class StopIn(BaseModel):
    conversation_id: str


class FeedbackIn(BaseModel):
    feedback: int = 0


def _clean(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


def conv_out(row: dict, project_name: str | None = None) -> dict:
    return {
        "id": row["id"],
        "title": row["title"],
        "project_id": row.get("project_id"),
        "project_name": project_name,
        "model": row.get("model"),
        "is_pinned": bool(row.get("is_pinned")),
        "is_archived": bool(row.get("is_archived")),
        "created_at": row.get("created_at"),
        "updated_at": row.get("updated_at"),
    }


def msg_out(row: dict) -> dict:
    return {
        "id": row["id"],
        "conversation_id": row["conversation_id"],
        "role": row["role"],
        "content": row["content"],
        "tokens": row["tokens"],
        "status": row["status"],
        "model": row.get("model"),
        "feedback": row.get("feedback", 0),
        "citations": db.jload(row.get("citations_json"), []),
        "plugin_calls": db.jload(row.get("plugin_calls_json"), []),
        "reasoning": row.get("reasoning") or "",
        "attachments": db.jload(row.get("attachments_json"), []),
        "created_at": row["created_at"],
    }


def get_conv(conv_id: str, user_id: str) -> dict:
    row = db.q1("SELECT * FROM conversation WHERE id = ? AND user_id = ?", (conv_id, user_id))
    if not row:
        raise ApiError("会话不存在或已删除", code=404, status_code=404)
    return row


def create_conversation(user_id: str, title: str | None = None, project_id: str | None = None,
                        model: str = "MAuto-Air") -> dict:
    now = db.now_utc()
    conv_id = db.uid("c_")
    db.insert("conversation", {
        "id": conv_id, "user_id": user_id, "title": title or "新会话",
        "project_id": project_id, "model": model,
        "is_pinned": 0, "is_archived": 0, "created_at": now, "updated_at": now,
    })
    return db.q1("SELECT * FROM conversation WHERE id = ?", (conv_id,))


@router.get("/conversations")
def list_conversations(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    q: str = "",
    archived: int = 0,
    recent_only: int = 0,
    user: dict = Depends(current_user),
):
    where = ["c.user_id = ?", "c.is_archived = ?"]
    args: list = [user["id"], 1 if archived else 0]
    keyword = _clean(q)
    if keyword:
        where.append(
            "(c.title LIKE ? OR EXISTS (SELECT 1 FROM message m WHERE m.conversation_id = c.id AND m.content LIKE ?))"
        )
        args.extend([f"%{keyword}%", f"%{keyword}%"])
    cond = " AND ".join(where)
    total = db.q1(f"SELECT COUNT(*) AS n FROM conversation c WHERE {cond}", args)["n"]
    limit = 15 if recent_only else page_size
    offset = 0 if recent_only else (page - 1) * page_size
    rows = db.q(
        f"SELECT c.*, p.name AS project_name FROM conversation c "
        f"LEFT JOIN project p ON p.id = c.project_id "
        f"WHERE {cond} ORDER BY c.is_pinned DESC, c.updated_at DESC LIMIT ? OFFSET ?",
        [*args, limit, offset],
    )
    items = [conv_out(r, r.get("project_name")) for r in rows]
    return ok({
        **paginate(items, page, page_size, total),
        "groups": _group_recent(items),
    })


def _group_recent(items: list[dict]) -> list[dict]:
    if len(items) <= 10:
        return []
    now = datetime.now(timezone.utc)
    buckets = {"今天": [], "昨天": [], "近 7 天": [], "更早": []}
    for it in items:
        dt = db.parse_iso(it["updated_at"]) or now
        days = (now.date() - dt.date()).days
        key = "今天" if days <= 0 else "昨天" if days == 1 else "近 7 天" if days < 7 else "更早"
        buckets[key].append(it)
    return [{"name": k, "items": v} for k, v in buckets.items() if v]


@router.post("/conversations")
def new_conversation(payload: ConversationIn, user: dict = Depends(current_user)):
    conv = create_conversation(user["id"], payload.title, payload.project_id, payload.model or "MAuto-Air")
    log_event(user["id"], "create_conversation", {"id": conv["id"]})
    return ok(conv_out(conv))


@router.get("/conversations/{conv_id}")
def conversation_detail(conv_id: str, user: dict = Depends(current_user)):
    row = get_conv(conv_id, user["id"])
    messages = db.q(
        "SELECT * FROM message WHERE conversation_id = ? ORDER BY created_at ASC, rowid ASC", (conv_id,)
    )
    project = db.q1("SELECT * FROM project WHERE id = ?", (row.get("project_id"),)) if row.get("project_id") else None
    return ok({
        **conv_out(row, project["name"] if project else None),
        "messages": [msg_out(m) for m in messages],
        "project": {"id": project["id"], "name": project["name"]} if project else None,
    })


@router.patch("/conversations/{conv_id}")
def patch_conversation(conv_id: str, payload: ConversationPatch, user: dict = Depends(current_user)):
    row = get_conv(conv_id, user["id"])
    data: dict = {"updated_at": db.now_utc()}
    if payload.title is not None:
        title = _clean(payload.title)[:60]
        if not title:
            raise ApiError("会话标题不能为空")
        data["title"] = title
    if payload.is_pinned is not None:
        data["is_pinned"] = 1 if payload.is_pinned else 0
    if payload.is_archived is not None:
        data["is_archived"] = 1 if payload.is_archived else 0
    db.update("conversation", row["id"], data)
    return ok(conv_out(db.q1("SELECT * FROM conversation WHERE id = ?", (conv_id,))))


@router.delete("/conversations/{conv_id}")
def delete_conversation(conv_id: str, user: dict = Depends(current_user)):
    row = get_conv(conv_id, user["id"])
    db.ex("DELETE FROM message WHERE conversation_id = ?", (row["id"],))
    db.ex("DELETE FROM conversation WHERE id = ?", (row["id"],))
    log_event(user["id"], "delete_conversation", {"id": row["id"]})
    return ok({"deleted": True})


@router.post("/messages/{msg_id}/feedback")
def message_feedback(msg_id: str, payload: FeedbackIn, user: dict = Depends(current_user)):
    row = db.q1(
        "SELECT m.* FROM message m JOIN conversation c ON c.id = m.conversation_id "
        "WHERE m.id = ? AND c.user_id = ?", (msg_id, user["id"]),
    )
    if not row:
        raise ApiError("消息不存在", code=404, status_code=404)
    db.ex("UPDATE message SET feedback = ? WHERE id = ?", (int(payload.feedback), msg_id))
    return ok({"feedback": payload.feedback})


@router.post("/chat/stop")
def stop_chat(payload: StopIn, user: dict = Depends(current_user)):
    get_conv(payload.conversation_id, user["id"])
    STOP_FLAGS[payload.conversation_id] = True
    log_event(user["id"], "stop_generate", {"conversation_id": payload.conversation_id})
    return ok({"stopped": True})


# ---------------------------------------------------------------- 文件上传
@router.post("/files")
def upload_file(file: UploadFile = File(...), user: dict = Depends(current_user)):
    name = Path(file.filename or "file").name
    ext = Path(name).suffix.lower()
    allowed = {".pdf", ".docx", ".xlsx", ".pptx", ".txt", ".md", ".png", ".jpg", ".jpeg"}
    if ext not in allowed:
        raise ApiError(f"不支持的文件类型 {ext}，请上传 pdf/docx/xlsx/pptx/txt/md/png/jpg")
    raw = file.file.read()
    if len(raw) > 50 * 1024 * 1024:
        raise ApiError("单个文件不能超过 50MB")
    fid = db.uid("f_")
    stored = UPLOAD_ROOT / f"{fid}{ext}"
    stored.write_bytes(raw)
    status, text = fileutil.extract_text(stored, name)
    db.insert("attachment", {
        "id": fid, "user_id": user["id"], "filename": name, "stored_path": str(stored),
        "file_size": len(raw), "mime": file.content_type or "",
        "parse_status": status, "text_excerpt": text[:20000], "created_at": db.now_utc(),
    })
    return ok({
        "id": fid, "filename": name, "size": len(raw),
        "size_text": fileutil.human_size(len(raw)), "parse_status": status,
        "excerpt": text[:400],
    })


@router.get("/files/{fid}")
def download_file(fid: str, user: dict = Depends(current_user)):
    row = db.q1("SELECT * FROM attachment WHERE id = ? AND user_id = ?", (fid, user["id"]))
    if not row:
        raise ApiError("文件不存在", code=404, status_code=404)
    return FileResponse(row["stored_path"], filename=row["filename"])


# ---------------------------------------------------------------- SSE 对话
def _sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


def _context_messages(conv_id: str, limit: int = 12) -> list[dict]:
    rows = db.q(
        "SELECT role, content FROM message WHERE conversation_id = ? AND status != 'failed' "
        "ORDER BY created_at DESC, rowid DESC LIMIT ?", (conv_id, limit),
    )
    rows.reverse()
    return rows


def _build_prompt(content: str, conv: dict, attachments: list[dict]) -> str:
    parts = [content]
    if attachments:
        names = [a.get("filename", "附件") for a in attachments]
        parts.append(f"（用户上传了 {len(names)} 个附件：{'、'.join(names)}）")
    project = db.q1("SELECT * FROM project WHERE id = ?", (conv.get("project_id"),)) if conv.get("project_id") else None
    if project:
        if project.get("instructions"):
            parts.append(f"（项目指令：{project['instructions']}）")
        files = db.q("SELECT file_name FROM project_file WHERE project_id = ? LIMIT 5", (project["id"],))
        if files:
            parts.append(f"（项目知识库文件：{'、'.join(f['file_name'] for f in files)}）")
    return "\n".join(parts)


def _project_citations(conv: dict) -> list[dict]:
    if not conv.get("project_id"):
        return []
    files = db.q(
        "SELECT * FROM project_file WHERE project_id = ? ORDER BY created_at DESC LIMIT 3",
        (conv["project_id"],),
    )
    return [{
        "title": f["file_name"], "site": "项目知识库", "time": (f.get("created_at") or "")[:10],
        "url": f"/api/v1/project-files/{f['id']}", "snippet": f"引用自知识库文件，页码 1-2（共 {max(1, f['file_size'] // 4096)} 页）",
    } for f in files]


@router.post("/chat/completions")
async def chat_completions(payload: ChatIn, request: Request, user: dict = Depends(current_user)):
    content = payload.content.strip()
    if not content and not payload.regenerate and not payload.edit_message_id:
        raise ApiError("请输入内容")

    if payload.conversation_id:
        conv = get_conv(payload.conversation_id, user["id"])
    else:
        conv = create_conversation(
            user["id"], content[:20] if content else "新会话",
            payload.project_id, payload.model,
        )

    conv_id = conv["id"]
    STOP_FLAGS[conv_id] = False

    user_msg = None
    if payload.edit_message_id:
        target = db.q1(
            "SELECT rowid AS rid, * FROM message WHERE id = ? AND conversation_id = ?",
            (payload.edit_message_id, conv_id),
        )
        if not target:
            raise ApiError("待编辑消息不存在", code=404, status_code=404)
        db.ex("UPDATE message SET content = ?, tokens = ? WHERE id = ?",
              (content, llm.estimate_tokens(content), target["id"]))
        db.ex("DELETE FROM message WHERE conversation_id = ? AND rowid > ?", (conv_id, target["rid"]))
        user_msg = db.q1("SELECT * FROM message WHERE id = ?", (target["id"],))
    elif payload.regenerate:
        if payload.message_id:
            target = db.q1("SELECT * FROM message WHERE id = ? AND conversation_id = ?",
                           (payload.message_id, conv_id))
            if target and target["role"] == "assistant":
                db.ex("DELETE FROM message WHERE id = ?", (target["id"],))
                db.ex("DELETE FROM message WHERE conversation_id = ? AND created_at > ? AND role='assistant'",
                      (conv_id, target["created_at"]))
        user_msg = db.q1(
            "SELECT * FROM message WHERE conversation_id = ? AND role = 'user' "
            "ORDER BY created_at DESC, rowid DESC LIMIT 1", (conv_id,)
        )
        if not user_msg:
            raise ApiError("没有可重新生成的提问")
        content = user_msg["content"]
    else:
        msg_id = db.uid("m_")
        db.insert("message", {
            "id": msg_id, "conversation_id": conv_id, "role": "user", "content": content,
            "tokens": llm.estimate_tokens(content), "status": "done", "model": payload.model,
            "feedback": 0, "citations_json": "[]", "plugin_calls_json": "[]", "reasoning": "",
            "attachments_json": json.dumps(payload.attachments, ensure_ascii=False),
            "created_at": db.now_utc(),
        })
        user_msg = db.q1("SELECT * FROM message WHERE id = ?", (msg_id,))
        if conv["title"] in ("新会话", "") and not conv.get("is_pinned"):
            title = _clean(content)[:20]
            db.update("conversation", conv_id, {"title": title, "updated_at": db.now_utc()})
        log_event(user["id"], "send_message", {"conversation_id": conv_id, "model": payload.model})

    attachments = db.jload(user_msg.get("attachments_json"), []) if user_msg else payload.attachments
    prompt = _build_prompt(content, conv, attachments)
    assistant_id = db.uid("m_")
    created_at = db.now_utc()
    db.insert("message", {
        "id": assistant_id, "conversation_id": conv_id, "role": "assistant", "content": "",
        "tokens": 0, "status": "generating", "model": payload.model, "feedback": 0,
        "citations_json": "[]", "plugin_calls_json": "[]", "reasoning": "",
        "attachments_json": "[]", "created_at": created_at,
    })
    db.update("conversation", conv_id, {"updated_at": created_at, "model": payload.model})

    # 插件准备与调用（调用结果会在流中推送）
    plugin_calls: list[dict] = []
    plugin_results: list[dict] = []
    for pid in payload.plugin_ids[:3]:
        row = db.q1(
            "SELECT p.* FROM plugin p JOIN user_plugin up ON up.plugin_id = p.id "
            "WHERE up.user_id = ? AND up.plugin_id = ? AND up.status = 'enabled'",
            (user["id"], pid),
        )
        if not row:
            continue
        started = time.time()
        output = llm.simulate_plugin(row, {"query": content[:40]})
        dur = int((time.time() - started) * 1000) + 180
        call = {"id": db.uid("pc_"), "name": row["name"], "icon": row["icon"],
                "status": "success", "duration_ms": dur, "output": output[:800]}
        plugin_calls.append(call)
        plugin_results.append(call)
        db.insert("plugin_call", {
            "id": call["id"], "user_id": user["id"], "plugin_id": row["id"],
            "conversation_id": conv_id, "input_json": json.dumps({"query": content[:40]}, ensure_ascii=False),
            "output": output[:2000], "duration_ms": dur, "status": "success", "created_at": db.now_utc(),
        })
        db.ex("UPDATE plugin SET install_count = install_count + 0 WHERE id = ?", (row["id"],))

    answer = "" if payload.web_search else llm.build_answer(
        content, web_search=False, deep_think=payload.deep_think,
        model=payload.model, attachments=[a.get("filename", "") for a in attachments],
        plugin_results=plugin_results,
    )
    citations = [] if payload.web_search else _project_citations(conv)
    reasoning = llm.build_reasoning(content) if payload.deep_think else ""

    async def event_stream():
        nonlocal answer, citations
        final_status = "done"
        produced: list[str] = []
        try:
            yield _sse("meta", {
                "conversation_id": conv_id, "message_id": assistant_id,
                "user_message_id": user_msg["id"] if user_msg else None,
                "title": _clean(content)[:20], "model": payload.model,
            })
            for call in plugin_calls:
                yield _sse("plugin", call)
            if payload.web_search and final_status == "done":
                # 真实联网检索：后台线程抓取，期间持续推送进度避免前端空转
                task = asyncio.create_task(asyncio.to_thread(websearch.retrieve, content))
                while True:
                    done, _ = await asyncio.wait({task}, timeout=1.4)
                    if done:
                        break
                    if STOP_FLAGS.get(conv_id) or await request.is_disconnected():
                        task.cancel()
                        final_status = "stopped"
                        break
                    yield _sse("progress", {"text": "正在联网检索网页…"})
                if final_status == "stopped":
                    task.cancel()
                else:
                    retrieval = task.result()
                    results = retrieval.get("results") or []
                    fetched = sum(1 for r in results if (r.get("text") or "").strip())
                    note = retrieval.get("error") or ""
                    call = {
                        "id": db.uid("pc_"), "name": "联网搜索", "icon": "🌐",
                        "status": "success" if results else "failed",
                        "duration_ms": retrieval.get("elapsed_ms", 0),
                        "output": (f"检索「{content[:30]}」：{len(results)} 条结果，抓取 {fetched} 个网页正文"
                                   + (f"；{note}" if note else "")),
                    }
                    plugin_calls.append(call)
                    yield _sse("plugin", call)
                    answer = await asyncio.to_thread(llm.build_web_answer_remote, content, retrieval)
                    # 网页来源排在前，保证回答里的 [n] 与卡片编号一一对应
                    citations = websearch.to_citations(results) + _project_citations(conv)
            if reasoning and final_status == "done":
                for chunk_text in _split(reasoning, 8):
                    if STOP_FLAGS.get(conv_id) or await request.is_disconnected():
                        final_status = "stopped"
                        break
                    yield _sse("reasoning", {"text": chunk_text})
                    await _sleep(0.01)
            if final_status == "done":
                for chunk_text in _split(answer, 4):
                    if STOP_FLAGS.get(conv_id) or await request.is_disconnected():
                        final_status = "stopped"
                        break
                    produced.append(chunk_text)
                    yield _sse("delta", {"text": chunk_text})
                    await _sleep(0.011)
            if citations:
                yield _sse("citation", {"citations": citations})
            text = "".join(produced)
            db.ex(
                "UPDATE message SET content = ?, status = ?, tokens = ?, citations_json = ?, "
                "plugin_calls_json = ?, reasoning = ? WHERE id = ?",
                (text, final_status, llm.estimate_tokens(text),
                 json.dumps(citations, ensure_ascii=False),
                 json.dumps(plugin_calls, ensure_ascii=False), reasoning, assistant_id),
            )
            db.update("conversation", conv_id, {"updated_at": db.now_utc()})
            yield _sse("done", {
                "message_id": assistant_id, "conversation_id": conv_id,
                "status": final_status, "tokens": llm.estimate_tokens(text),
                "citations": citations,
            })
            log_event(user["id"], "chat_done", {"conversation_id": conv_id, "status": final_status})
        except Exception as exc:  # noqa: BLE001 - 流中断时保留已生成内容
            text = "".join(produced)
            db.ex("UPDATE message SET content = ?, status = 'failed' WHERE id = ?", (text, assistant_id))
            yield _sse("error", {"message": f"服务异常：{type(exc).__name__}"})

    return StreamingResponse(event_stream(), media_type="text/event-stream", headers={
        "Cache-Control": "no-cache", "X-Accel-Buffering": "no",
    })


def _split(text: str, size: int):
    for i in range(0, len(text), size):
        yield text[i:i + size]


async def _sleep(seconds: float) -> None:
    import asyncio
    await asyncio.sleep(seconds)


# ---------------------------------------------------------------- 导出
@router.get("/conversations/{conv_id}/export")
def export_conversation(conv_id: str, format: str = "md", user: dict = Depends(current_user)):
    conv = get_conv(conv_id, user["id"])
    messages = db.q(
        "SELECT * FROM message WHERE conversation_id = ? ORDER BY created_at ASC, rowid ASC", (conv_id,)
    )
    title = re.sub(r'[\\/:*?"<>|]', "_", conv["title"])[:40] or "conversation"
    stamp = datetime.now().strftime("%Y%m%d%H%M%S")
    if format == "docx":
        import docx
        document = docx.Document()
        document.add_heading(conv["title"], level=1)
        document.add_paragraph(f"导出时间：{datetime.now().strftime('%Y-%m-%d %H:%M')} · 模型：{conv.get('model')}")
        for m in messages:
            document.add_heading("我" if m["role"] == "user" else "MAuto", level=2)
            document.add_paragraph(m["content"] or "（空）")
        out = EXPORT_ROOT / f"{title}_{stamp}.docx"
        document.save(str(out))
        return FileResponse(out, filename=out.name,
                            media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document")
    lines = [f"# {conv['title']}", "", f"> 导出时间：{datetime.now().strftime('%Y-%m-%d %H:%M')}", ""]
    for m in messages:
        who = "我" if m["role"] == "user" else "MAuto"
        lines += [f"## {who}", "", m["content"] or "（空）", ""]
        if m.get("citations_json") and db.jload(m["citations_json"], []):
            lines.append("**引用来源**")
            for c in db.jload(m["citations_json"], []):
                lines.append(f"- [{c['title']}]({c['url']}) — {c['site']}")
            lines.append("")
    out = EXPORT_ROOT / f"{title}_{stamp}.md"
    out.write_text("\n".join(lines), encoding="utf-8")
    return FileResponse(out, filename=out.name, media_type="text/markdown")
