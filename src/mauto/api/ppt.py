"""PPT 模块：模板库、AI 生成、在线编辑、导出。"""
from __future__ import annotations

import json
import re
import threading
import time
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Query
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel

from .. import db, llm, ppt_design, ppt_render
from ..deps import ApiError, current_user, log_event, ok, paginate

router = APIRouter(tags=["ppt"])

MONTHLY_QUOTA = 20
STYLES = ["全部", "简约", "科技", "国潮", "扁平"]
CATEGORIES = ["全部", "商务汇报", "教育教学", "科技互联网", "医疗健康", "政府政务", "简约通用"]


class OutlineIn(BaseModel):
    topic: str
    pages: int = 10
    template_id: str = "tpl_biz_blue"
    source_text: str = ""


class GenerateIn(BaseModel):
    topic: str
    pages: int = 10
    template_id: str = "tpl_biz_blue"
    outline: list[dict] | None = None
    ratio: str | None = None


class DocumentPatch(BaseModel):
    title: str | None = None
    slides: list[dict] | None = None
    cover_color: str | None = None
    template_id: str | None = None


class CreateBlankIn(BaseModel):
    title: str = "未命名演示文稿"
    ratio: str = "16:9"
    template_id: str = "tpl_min_gray"


class PreviewIn(BaseModel):
    slides: list[dict]
    template_id: str = "tpl_biz_blue"
    ratio: str = "16:9"
    index: int = 0
    width: int = 1200
    accent: str | None = None


def _template(tpl_id: str) -> dict:
    row = db.q1("SELECT * FROM ppt_template WHERE id = ?", (tpl_id,))
    if not row:
        raise ApiError("模板不存在", code=404, status_code=404)
    return row


def _template_out(row: dict) -> dict:
    """补充版式说明与真实预览地址，前端卡片直接使用。"""
    design = ppt_design.design_for(row["id"])
    base = f"/api/v1/ppt/templates/{row['id']}/preview"
    out = dict(row)
    out["design_key"] = design.id
    out["layout_desc"] = design.layout_desc
    out["thumb"] = f"{base}?page=cover&width=560"
    out["strip"] = [
        {"type": t, "label": ppt_design.PAGE_TYPE_LABELS[t],
         "url": f"{base}?page={t}&width=420"}
        for t in ("bullets", "metrics", "chart")
    ]
    out["page_types"] = [{"type": t, "label": ppt_design.PAGE_TYPE_LABELS[t]}
                         for t in ppt_design.PREVIEW_ORDER]
    return out


def _doc_out(doc: dict, with_slides: bool = True) -> dict:
    out = {
        "id": doc["id"], "title": doc["title"], "template_id": doc["template_id"],
        "page_count": doc["page_count"], "ratio": doc["ratio"], "style": doc["style"],
        "cover_color": doc["cover_color"], "version": doc["version"],
        "created_at": doc["created_at"], "updated_at": doc["updated_at"],
    }
    if with_slides:
        out["slides"] = db.jload(doc["content_json"], [])
    tpl = db.q1("SELECT * FROM ppt_template WHERE id = ?", (doc["template_id"],))
    out["template"] = {"id": tpl["id"], "name": tpl["name"], "color_a": tpl["color_a"],
                       "color_b": tpl["color_b"]} if tpl else None
    return out


def _quota(user_id: str) -> dict:
    month_start = datetime.now(timezone.utc).strftime("%Y-%m-01T00:00:00+00:00")
    used = db.q1(
        "SELECT COUNT(*) AS n FROM ppt_document WHERE user_id = ? AND created_at >= ?",
        (user_id, month_start),
    )["n"]
    return {"used": used, "limit": MONTHLY_QUOTA, "remaining": max(0, MONTHLY_QUOTA - used)}


def _get_doc(doc_id: str, user_id: str) -> dict:
    row = db.q1("SELECT * FROM ppt_document WHERE id = ? AND user_id = ?", (doc_id, user_id))
    if not row:
        raise ApiError("PPT 不存在或已删除", code=404, status_code=404)
    return row


# ------------------------------------------------------------------ 模板
@router.get("/ppt/templates")
def templates(category: str = "全部", style: str = "全部", q: str = "",
              page: int = Query(1, ge=1), page_size: int = Query(24, ge=1, le=60)):
    where, args = ["1=1"], []
    if category and category != "全部":
        where.append("category = ?")
        args.append(category)
    if style and style != "全部":
        where.append("style = ?")
        args.append(style)
    if q.strip():
        where.append("name LIKE ?")
        args.append(f"%{q.strip()}%")
    cond = " AND ".join(where)
    total = db.q1(f"SELECT COUNT(*) AS n FROM ppt_template WHERE {cond}", args)["n"]
    rows = db.q(f"SELECT * FROM ppt_template WHERE {cond} ORDER BY use_count DESC LIMIT ? OFFSET ?",
                [*args, page_size, (page - 1) * page_size])
    items = [_template_out(r) for r in rows]
    return ok({**paginate(items, page, page_size, total),
               "categories": CATEGORIES, "styles": STYLES})


@router.get("/ppt/templates/{tpl_id}/preview")
def template_preview(tpl_id: str, page: str = "cover",
                     width: int = Query(640, ge=240, le=2000)):
    """模板版式真实预览（公开，供模板卡片与详情页直接引用）。"""
    _template(tpl_id)
    png = ppt_render.template_preview_png(tpl_id, page, width)
    return Response(content=png, media_type="image/png",
                    headers={"Cache-Control": "public, max-age=600"})


@router.get("/ppt/templates/{tpl_id}")
def template_detail(tpl_id: str):
    row = _template(tpl_id)
    out = _template_out(row)
    base = f"/api/v1/ppt/templates/{tpl_id}/preview"
    out["previews"] = [
        {"type": t, "label": ppt_design.PAGE_TYPE_LABELS[t],
         "url": f"{base}?page={t}&width=880"}
        for t in ppt_design.PREVIEW_ORDER
    ]
    demo = ["封面标题", "目录", "背景与目标", "现状与痛点", "方案总览", "实施路径", "收益测算", "下一步"]
    out["preview"] = [
        {"index": i + 1, "title": demo[i % len(demo)], "layout": "cover" if i == 0 else "content"}
        for i in range(min(8, row["page_count"]))
    ]
    db.ex("UPDATE ppt_template SET use_count = use_count + 1 WHERE id = ?", (tpl_id,))
    return ok(out)


@router.get("/ppt/quota")
def quota(user: dict = Depends(current_user)):
    return ok(_quota(user["id"]))


@router.post("/ppt/preview")
def preview_slide(payload: PreviewIn, user: dict = Depends(current_user)):
    """按当前编辑内容实时渲染单页预览，编辑器与导出共用同一版式引擎。"""
    if not payload.slides:
        raise ApiError("页面内容为空")
    if len(payload.slides) > 60:
        raise ApiError("页面数量超出限制")
    png = ppt_render.preview_png(payload.slides, payload.template_id, payload.ratio,
                                 payload.index, payload.width, payload.accent)
    return Response(content=png, media_type="image/png", headers={"Cache-Control": "no-store"})


# ------------------------------------------------------------------ 生成
@router.post("/ppt/outline")
def outline(payload: OutlineIn, user: dict = Depends(current_user)):
    if not payload.topic.strip():
        raise ApiError("请输入 PPT 主题")
    if not 5 <= payload.pages <= 30:
        raise ApiError("页数需在 5~30 页之间")
    slides = llm.build_ppt_outline(payload.topic, payload.pages, payload.source_text)
    return ok({"topic": payload.topic, "pages": payload.pages, "slides": slides})


def _run_job(job_id: str) -> None:
    job = db.q1("SELECT * FROM ppt_job WHERE id = ?", (job_id,))
    if not job:
        return
    payload = db.jload(job["payload_json"], {})
    try:
        for progress, message in [(12, "解析主题与大纲"), (38, "匹配模板版式"), (64, "生成页面文案"), (86, "排版与配色")]:
            time.sleep(0.45)
            db.update("ppt_job", job_id, {"progress": progress, "message": message, "updated_at": db.now_utc()})
        tpl = _template(payload.get("template_id", "tpl_biz_blue"))
        slides = payload.get("outline") or llm.build_ppt_outline(
            payload.get("topic", ""), payload.get("pages", 10), payload.get("source_text", ""))
        now = db.now_utc()
        doc_id = db.uid("pd_")
        db.insert("ppt_document", {
            "id": doc_id, "user_id": job["user_id"], "template_id": tpl["id"],
            "title": payload.get("topic") or "未命名演示文稿",
            "content_json": json.dumps(slides, ensure_ascii=False),
            "page_count": len(slides), "ratio": payload.get("ratio") or tpl["ratio"],
            "style": tpl["style"], "cover_color": tpl["color_a"], "version": 1,
            "created_at": now, "updated_at": now,
        })
        db.ex("UPDATE ppt_template SET use_count = use_count + 1 WHERE id = ?", (tpl["id"],))
        db.update("ppt_job", job_id, {"status": "success", "progress": 100,
                                      "message": "生成完成", "doc_id": doc_id, "updated_at": db.now_utc()})
    except Exception as exc:  # noqa: BLE001
        db.update("ppt_job", job_id, {"status": "failed", "message": f"生成失败：{type(exc).__name__}",
                                      "updated_at": db.now_utc()})


@router.post("/ppt/generate")
def generate(payload: GenerateIn, user: dict = Depends(current_user)):
    if not payload.topic.strip():
        raise ApiError("请输入 PPT 主题")
    if not 5 <= payload.pages <= 30:
        raise ApiError("页数需在 5~30 页之间")
    q = _quota(user["id"])
    if q["remaining"] <= 0:
        raise ApiError("本月免费生成次数已用完（20 次），请下月再试")
    _template(payload.template_id)
    job_id = db.uid("pj_")
    now = db.now_utc()
    db.insert("ppt_job", {
        "id": job_id, "user_id": user["id"], "title": payload.topic, "status": "running",
        "progress": 0, "message": "排队中", "doc_id": None,
        "payload_json": json.dumps(payload.model_dump(), ensure_ascii=False),
        "created_at": now, "updated_at": now,
    })
    threading.Thread(target=_run_job, args=(job_id,), daemon=True).start()
    log_event(user["id"], "generate_ppt", {"job_id": job_id, "pages": payload.pages})
    return ok({"job_id": job_id, "status": "running"})


@router.get("/ppt/jobs/{job_id}")
def job_status(job_id: str, user: dict = Depends(current_user)):
    job = db.q1("SELECT * FROM ppt_job WHERE id = ? AND user_id = ?", (job_id, user["id"]))
    if not job:
        raise ApiError("任务不存在", code=404, status_code=404)
    return ok({"job_id": job_id, "status": job["status"], "progress": job["progress"],
               "message": job["message"], "doc_id": job["doc_id"]})


# ------------------------------------------------------------------ 文档
@router.get("/ppt/documents")
def documents(page: int = Query(1, ge=1), page_size: int = Query(12, ge=1, le=50),
              q: str = "", user: dict = Depends(current_user)):
    where, args = ["user_id = ?"], [user["id"]]
    if q.strip():
        where.append("title LIKE ?")
        args.append(f"%{q.strip()}%")
    cond = " AND ".join(where)
    total = db.q1(f"SELECT COUNT(*) AS n FROM ppt_document WHERE {cond}", args)["n"]
    rows = db.q(f"SELECT * FROM ppt_document WHERE {cond} ORDER BY updated_at DESC LIMIT ? OFFSET ?",
                [*args, page_size, (page - 1) * page_size])
    return ok({**paginate([_doc_out(r, with_slides=False) for r in rows], page, page_size, total),
               "quota": _quota(user["id"])})


@router.post("/ppt/documents")
def create_blank(payload: CreateBlankIn, user: dict = Depends(current_user)):
    tpl = _template(payload.template_id)
    now = db.now_utc()
    doc_id = db.uid("pd_")
    slides = [
        {"title": payload.title, "subtitle": "在此输入副标题", "bullets": [], "notes": "", "layout": "cover"},
        {"title": "目录", "bullets": ["第一部分", "第二部分", "第三部分"], "notes": "", "layout": "content"},
        {"title": "新页面", "bullets": ["点击编辑文本", "支持调整页面顺序"], "notes": "", "layout": "content"},
    ]
    db.insert("ppt_document", {
        "id": doc_id, "user_id": user["id"], "template_id": tpl["id"], "title": payload.title,
        "content_json": json.dumps(slides, ensure_ascii=False), "page_count": len(slides),
        "ratio": payload.ratio, "style": tpl["style"], "cover_color": tpl["color_a"],
        "version": 1, "created_at": now, "updated_at": now,
    })
    return ok(_doc_out(_get_doc(doc_id, user["id"])))


@router.get("/ppt/documents/{doc_id}")
def document_detail(doc_id: str, user: dict = Depends(current_user)):
    return ok(_doc_out(_get_doc(doc_id, user["id"])))


@router.patch("/ppt/documents/{doc_id}")
def patch_document(doc_id: str, payload: DocumentPatch, user: dict = Depends(current_user)):
    doc = _get_doc(doc_id, user["id"])
    data: dict = {"updated_at": db.now_utc()}
    if payload.title is not None:
        data["title"] = payload.title.strip()[:60] or doc["title"]
    if payload.cover_color:
        data["cover_color"] = payload.cover_color
    if payload.template_id:
        tpl = _template(payload.template_id)
        data["template_id"] = tpl["id"]
        data["style"] = tpl["style"]
        data["ratio"] = tpl["ratio"]
        data.setdefault("cover_color", tpl["color_a"])
    if payload.slides is not None:
        if not payload.slides:
            raise ApiError("至少保留一页")
        data["content_json"] = json.dumps(payload.slides, ensure_ascii=False)
        data["page_count"] = len(payload.slides)
        data["version"] = doc["version"] + 1
        db.insert("ppt_version", {
            "id": db.uid("pv_"), "document_id": doc_id, "version": doc["version"],
            "content_json": doc["content_json"], "created_at": db.now_utc(),
        })
        db.ex(
            "DELETE FROM ppt_version WHERE document_id = ? AND id NOT IN "
            "(SELECT id FROM ppt_version WHERE document_id = ? ORDER BY version DESC LIMIT 10)",
            (doc_id, doc_id),
        )
    db.update("ppt_document", doc_id, data)
    return ok(_doc_out(_get_doc(doc_id, user["id"])))


@router.post("/ppt/documents/{doc_id}/duplicate")
def duplicate(doc_id: str, user: dict = Depends(current_user)):
    doc = _get_doc(doc_id, user["id"])
    now = db.now_utc()
    new_id = db.uid("pd_")
    db.insert("ppt_document", {
        **{k: doc[k] for k in ("template_id", "content_json", "page_count", "ratio", "style", "cover_color")},
        "id": new_id, "user_id": user["id"], "title": f"{doc['title']} 副本",
        "version": 1, "created_at": now, "updated_at": now,
    })
    return ok(_doc_out(_get_doc(new_id, user["id"])))


@router.delete("/ppt/documents/{doc_id}")
def delete_document(doc_id: str, user: dict = Depends(current_user)):
    doc = _get_doc(doc_id, user["id"])
    db.ex("DELETE FROM ppt_document WHERE id = ?", (doc["id"],))
    db.ex("DELETE FROM ppt_version WHERE document_id = ?", (doc["id"],))
    return ok({"deleted": True})


@router.get("/ppt/documents/{doc_id}/versions")
def versions(doc_id: str, user: dict = Depends(current_user)):
    _get_doc(doc_id, user["id"])
    rows = db.q("SELECT * FROM ppt_version WHERE document_id = ? ORDER BY version DESC LIMIT 10", (doc_id,))
    return ok([{"id": r["id"], "version": r["version"], "created_at": r["created_at"]} for r in rows])


@router.post("/ppt/documents/{doc_id}/rollback/{version_id}")
def rollback(doc_id: str, version_id: str, user: dict = Depends(current_user)):
    doc = _get_doc(doc_id, user["id"])
    ver = db.q1("SELECT * FROM ppt_version WHERE id = ? AND document_id = ?", (version_id, doc_id))
    if not ver:
        raise ApiError("版本不存在", code=404, status_code=404)
    slides = db.jload(ver["content_json"], [])
    db.update("ppt_document", doc_id, {
        "content_json": ver["content_json"], "page_count": len(slides),
        "version": doc["version"] + 1, "updated_at": db.now_utc(),
    })
    return ok(_doc_out(_get_doc(doc_id, user["id"])))


@router.get("/ppt/documents/{doc_id}/export")
def export(doc_id: str, format: str = "pptx", user: dict = Depends(current_user)):
    doc = _get_doc(doc_id, user["id"])
    fmt = format.lower()
    if fmt not in ("pptx", "pdf", "png"):
        raise ApiError("导出格式仅支持 pptx/pdf/png")
    safe_title = re.sub(r'[\\/:*?"<>|]', "_", doc["title"])[:40] or "presentation"
    stamp = datetime.now().strftime("%Y%m%d%H%M%S")
    path = db.EXPORT_DIR / f"{safe_title}_{stamp}.{fmt}"
    if fmt == "pptx":
        ppt_render.build_pptx(doc, path)
        media = "application/vnd.openxmlformats-officedocument.presentationml.presentation"
    elif fmt == "pdf":
        ppt_render.build_pdf(doc, path)
        media = "application/pdf"
    else:
        ppt_render.build_png(doc, path)
        media = "image/png"
    log_event(user["id"], "export_ppt", {"doc_id": doc_id, "format": fmt})
    return FileResponse(path, filename=path.name, media_type=media)
