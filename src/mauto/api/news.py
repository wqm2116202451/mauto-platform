"""咨询（行业资讯）接口。"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel

from .. import db, llm, newssource
from ..deps import ApiError, current_user, log_event, ok, paginate

router = APIRouter(tags=["news"])


class ReadIn(BaseModel):
    is_read: bool = True


class SubscribeIn(BaseModel):
    type: str = "industry"  # industry | keyword
    target_id: str = ""
    keyword: str = ""


def _state(user_id: str, news_id: str) -> dict:
    return db.q1("SELECT * FROM news_state WHERE user_id = ? AND news_id = ?", (user_id, news_id)) or {}


@router.get("/news/categories")
def categories(user: dict = Depends(current_user)):
    rows = db.q("SELECT * FROM news_category ORDER BY sort ASC")
    counts = {r["industry_id"]: r["n"] for r in db.q(
        "SELECT industry_id, COUNT(*) AS n FROM news WHERE status='online' GROUP BY industry_id")}
    total = sum(counts.values())
    items = [{"id": "all", "name": "全部", "icon": "🧭", "count": total}]
    items += [{"id": r["id"], "name": r["name"], "icon": r["icon"], "count": counts.get(r["id"], 0)} for r in rows]
    subs = db.q("SELECT * FROM news_subscription WHERE user_id = ?", (user["id"],))
    return ok({"items": items, "subscriptions": subs})


@router.get("/news")
def news_feed(
    industry: str = "all",
    sort: str = "recommend",
    page: int = Query(1, ge=1),
    page_size: int = Query(10, ge=1, le=50),
    q: str = "",
    time_range: str = "all",
    source: str = "",
    only_unread: int = 0,
    user: dict = Depends(current_user),
):
    where = ["n.status = 'online'"]
    args: list = []
    if industry and industry != "all":
        where.append("n.industry_id = ?")
        args.append(industry)
    if q.strip():
        where.append("(n.title LIKE ? OR n.summary LIKE ? OR n.content LIKE ?)")
        kw = f"%{q.strip()}%"
        args += [kw, kw, kw]
    if time_range in ("today", "3d", "7d", "30d"):
        days = {"today": 1, "3d": 3, "7d": 7, "30d": 30}[time_range]
        where.append("n.publish_at >= ?")
        args.append(db.iso(datetime.now(timezone.utc) - timedelta(days=days)))
    if source:
        where.append("n.source = ?")
        args.append(source)
    if only_unread:
        where.append("NOT EXISTS (SELECT 1 FROM news_state s WHERE s.news_id = n.id AND s.user_id = ? AND s.is_read = 1)")
        args.append(user["id"])
    cond = " AND ".join(where)
    total = db.q1(f"SELECT COUNT(*) AS n FROM news n WHERE {cond}", args)["n"]
    if sort == "recommend":
        # 推荐：各行业最新内容轮流穿插，避免单一时段/行业刷屏
        rows = db.q(
            "SELECT * FROM (SELECT n.*, c.name AS industry_name, c.icon AS industry_icon, "
            "ROW_NUMBER() OVER (PARTITION BY n.industry_id ORDER BY n.publish_at DESC) AS rn "
            f"FROM news n LEFT JOIN news_category c ON c.id = n.industry_id WHERE {cond}) "
            "WHERE rn <= ? ORDER BY rn ASC, publish_at DESC LIMIT ? OFFSET ?",
            [*args, 30, page_size, (page - 1) * page_size],
        )
    else:
        order = {"latest": "n.publish_at DESC",
                 "hot": "n.read_count DESC, n.publish_at DESC"}.get(sort, "n.publish_at DESC")
        rows = db.q(
            f"SELECT n.*, c.name AS industry_name, c.icon AS industry_icon FROM news n "
            f"LEFT JOIN news_category c ON c.id = n.industry_id WHERE {cond} "
            f"ORDER BY {order} LIMIT ? OFFSET ?",
            [*args, page_size, (page - 1) * page_size],
        )
    states = {r["news_id"]: r for r in db.q(
        "SELECT * FROM news_state WHERE user_id = ?", (user["id"],))}
    items = []
    for r in rows:
        st = states.get(r["id"], {})
        items.append({
            "id": r["id"], "title": r["title"], "summary": r["summary"],
            "source": r["source"], "industry_id": r["industry_id"],
            "industry_name": r["industry_name"], "industry_icon": r["industry_icon"],
            "publish_at": r["publish_at"], "heat": r["heat"], "read_count": r["read_count"],
            "is_read": bool(st.get("is_read")), "is_favorite": bool(st.get("is_favorite")),
        })
    return ok({**paginate(items, page, page_size, total), "sources": _sources(),
               "sync": newssource.status()})


def _sources() -> list[str]:
    return [r["source"] for r in db.q("SELECT DISTINCT source FROM news WHERE status='online' ORDER BY source")]


@router.post("/news/refresh")
def refresh_news(user: dict = Depends(current_user)):
    """手动重新抓取真实资讯（RSS 源），返回同步结果。"""
    result = newssource.sync(force=True)
    return ok(result)


@router.get("/news/status")
def news_status(user: dict = Depends(current_user)):
    return ok(newssource.status())


@router.get("/news/{news_id}")
def news_detail(news_id: str, user: dict = Depends(current_user)):
    row = db.q1(
        "SELECT n.*, c.name AS industry_name, c.icon AS industry_icon FROM news n "
        "LEFT JOIN news_category c ON c.id = n.industry_id WHERE n.id = ?", (news_id,)
    )
    if not row:
        raise ApiError("资讯不存在或已下架", code=404, status_code=404)
    # RSS 摘要过短时，按需抓取原网页补全正文；失败则保留 RSS 内容
    if row.get("url") and len((row.get("content") or "").strip()) < 700:
        newssource.ensure_content(news_id, row["url"])
        row["content"] = (db.q1("SELECT content FROM news WHERE id = ?", (news_id,)) or {}).get(
            "content", row["content"])
    ai = db.jload(row.get("ai_points_json"), {})
    if not ai:
        ai = llm.news_ai_summary({**row, "industry_name": row.get("industry_name", "")})
        db.ex("UPDATE news SET ai_points_json = ? WHERE id = ?", (json.dumps(ai, ensure_ascii=False), news_id))
    db.ex("UPDATE news SET read_count = read_count + 1 WHERE id = ?", (news_id,))
    row["read_count"] = (row.get("read_count") or 0) + 1
    now = db.now_utc()
    st = _state(user["id"], news_id)
    if st:
        db.ex("UPDATE news_state SET is_read = 1 WHERE user_id = ? AND news_id = ?", (user["id"], news_id))
    else:
        db.insert("news_state", {"user_id": user["id"], "news_id": news_id, "is_read": 1,
                                 "is_favorite": 0, "created_at": now})
    related = db.q(
        "SELECT id, title, source, publish_at, heat FROM news WHERE industry_id = ? AND id != ? "
        "AND status='online' ORDER BY publish_at DESC LIMIT 5", (row["industry_id"], news_id),
    )
    log_event(user["id"], "click_news", {"news_id": news_id})
    return ok({
        "id": row["id"], "title": row["title"], "summary": row["summary"], "content": row["content"],
        "source": row["source"], "publish_at": row["publish_at"], "heat": row["heat"],
        "read_count": row["read_count"], "industry_id": row["industry_id"],
        "industry_name": row["industry_name"], "industry_icon": row["industry_icon"],
        "url": row.get("url", ""),
        "ai": ai, "related": related, "is_favorite": bool(st.get("is_favorite")),
        "is_read": True,
    })


@router.post("/news/{news_id}/favorite")
def toggle_favorite(news_id: str, user: dict = Depends(current_user)):
    if not db.q1("SELECT id FROM news WHERE id = ?", (news_id,)):
        raise ApiError("资讯不存在", code=404, status_code=404)
    st = _state(user["id"], news_id)
    if st:
        new_val = 0 if st.get("is_favorite") else 1
        db.ex("UPDATE news_state SET is_favorite = ? WHERE user_id = ? AND news_id = ?",
              (new_val, user["id"], news_id))
    else:
        new_val = 1
        db.insert("news_state", {"user_id": user["id"], "news_id": news_id, "is_read": 0,
                                 "is_favorite": 1, "created_at": db.now_utc()})
    return ok({"is_favorite": bool(new_val)})


@router.get("/news/favorites/mine")
def my_favorites(page: int = Query(1, ge=1), page_size: int = Query(10, ge=1, le=50),
                 user: dict = Depends(current_user)):
    total = db.q1(
        "SELECT COUNT(*) AS n FROM news_state s JOIN news n ON n.id = s.news_id "
        "WHERE s.user_id = ? AND s.is_favorite = 1", (user["id"],)
    )["n"]
    rows = db.q(
        "SELECT n.*, c.name AS industry_name FROM news_state s JOIN news n ON n.id = s.news_id "
        "LEFT JOIN news_category c ON c.id = n.industry_id "
        "WHERE s.user_id = ? AND s.is_favorite = 1 ORDER BY s.created_at DESC LIMIT ? OFFSET ?",
        (user["id"], page_size, (page - 1) * page_size),
    )
    items = [{
        "id": r["id"], "title": r["title"], "summary": r["summary"], "source": r["source"],
        "publish_at": r["publish_at"], "industry_name": r["industry_name"],
        "heat": r["heat"], "is_favorite": True,
    } for r in rows]
    return ok(paginate(items, page, page_size, total))


@router.post("/news/{news_id}/read")
def mark_read(news_id: str, payload: ReadIn, user: dict = Depends(current_user)):
    st = _state(user["id"], news_id)
    val = 1 if payload.is_read else 0
    if st:
        db.ex("UPDATE news_state SET is_read = ? WHERE user_id = ? AND news_id = ?", (val, user["id"], news_id))
    else:
        db.insert("news_state", {"user_id": user["id"], "news_id": news_id, "is_read": val,
                                 "is_favorite": 0, "created_at": db.now_utc()})
    return ok({"is_read": bool(val)})


@router.get("/news-subscriptions")
def list_subscriptions(user: dict = Depends(current_user)):
    rows = db.q("SELECT * FROM news_subscription WHERE user_id = ? ORDER BY created_at DESC", (user["id"],))
    return ok(rows)


@router.post("/news-subscriptions")
def add_subscription(payload: SubscribeIn, user: dict = Depends(current_user)):
    if payload.type == "keyword" and not payload.keyword.strip():
        raise ApiError("请输入订阅关键词")
    if payload.type == "industry" and not payload.target_id:
        raise ApiError("请选择订阅行业")
    existing = db.q1(
        "SELECT * FROM news_subscription WHERE user_id = ? AND type = ? AND target_id = ? AND keyword = ?",
        (user["id"], payload.type, payload.target_id, payload.keyword.strip()),
    )
    if existing:
        return ok(existing)
    row = {"id": db.uid("sub_"), "user_id": user["id"], "type": payload.type,
           "target_id": payload.target_id, "keyword": payload.keyword.strip(), "created_at": db.now_utc()}
    db.insert("news_subscription", row)
    return ok(row)


@router.delete("/news-subscriptions/{sub_id}")
def delete_subscription(sub_id: str, user: dict = Depends(current_user)):
    db.ex("DELETE FROM news_subscription WHERE id = ? AND user_id = ?", (sub_id, user["id"]))
    return ok({"deleted": True})
