"""真实资讯数据源：抓取公开 RSS 源，写入 news 表。

仅使用标准库，无需任何 Key。默认聚合以下中文公开 RSS：

    36氪、IT之家、虎嗅、钛媒体、量子位、极客公园、爱范儿、Solidot、
    芥末堆（教育）、少数派、中新网（健康 / 教育 / 财经 / 滚动）

抓取后按标题与摘要中的关键词归类到 10 个行业，保留真实标题、摘要、
来源、发布时间与原文链接；正文优先使用 RSS 内容，详情页再按需抓取原网页补全。

可用环境变量调整：

    MAUTO_NEWS_DISABLE            设为 1 时关闭资讯抓取
    MAUTO_NEWS_REFRESH_MINUTES    自动刷新间隔（分钟），默认 30
    MAUTO_NEWS_TIMEOUT            单个源超时（秒），默认 8
    MAUTO_NEWS_MAX_ITEMS          最多保留条数，默认 240
"""
from __future__ import annotations

import hashlib
import os
import re
import threading
import time
import urllib.parse
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime

from . import db, websearch

DISABLED = os.environ.get("MAUTO_NEWS_DISABLE", "") == "1"
TIMEOUT = float(os.environ.get("MAUTO_NEWS_TIMEOUT", "8"))
MAX_ITEMS = int(os.environ.get("MAUTO_NEWS_MAX_ITEMS", "240"))
REFRESH_MINUTES = int(os.environ.get("MAUTO_NEWS_REFRESH_MINUTES", "30"))
PER_FEED = 40
MAX_CONTENT = 4000

FEEDS = [
    {"id": "36kr", "name": "36氪", "url": "https://www.36kr.com/feed", "default": "ind_ai"},
    {"id": "ithome", "name": "IT之家", "url": "https://www.ithome.com/rss/", "default": "ind_electronics"},
    {"id": "huxiu", "name": "虎嗅", "url": "https://rss.huxiu.com/", "default": ""},
    {"id": "tmtpost", "name": "钛媒体", "url": "https://www.tmtpost.com/rss", "default": "ind_finance"},
    {"id": "qbitai", "name": "量子位", "url": "https://www.qbitai.com/feed", "default": "ind_ai"},
    {"id": "geekpark", "name": "极客公园", "url": "https://www.geekpark.net/rss", "default": "ind_ai"},
    {"id": "ifanr", "name": "爱范儿", "url": "https://www.ifanr.com/feed", "default": "ind_consumer"},
    {"id": "solidot", "name": "Solidot", "url": "https://www.solidot.org/index.rss", "default": "ind_ai"},
    {"id": "jiemodui", "name": "芥末堆", "url": "https://www.jiemodui.com/feed", "default": "ind_edu"},
    {"id": "sspai", "name": "少数派", "url": "https://sspai.com/feed", "default": "ind_consumer"},
    {"id": "cn_health", "name": "中新网·健康", "url": "https://www.chinanews.com.cn/rss/health.xml", "default": "ind_medical"},
    {"id": "cn_edu", "name": "中新网·教育", "url": "https://www.chinanews.com.cn/rss/edu.xml", "default": "ind_edu"},
    {"id": "cn_finance", "name": "中新网·财经", "url": "https://www.chinanews.com.cn/rss/finance.xml", "default": "ind_finance"},
    {"id": "cn_scroll", "name": "中新网", "url": "https://www.chinanews.com.cn/rss/scroll-news.xml", "default": ""},
]

KEYWORDS: dict[str, list[str]] = {
    "ind_chip": ["芯片", "半导体", "晶圆", "制程", "光刻", "封装", "存储", "内存", "gpu", "cpu",
                 "台积电", "中芯", "英伟达", "高通", "联发科", "dram", "hbm", "集成电路", "算力卡"],
    "ind_electronics": ["面板", "显示屏", "折叠屏", "传感器", "pcb", "消费电子", "手机", "耳机",
                        "眼镜", "摄像头", "电视", "oled", "mini led", "影像", "硬件"],
    "ind_ai": ["人工智能", "大模型", "ai ", "ai，", "ai。", "智能体", "机器学习", "生成式", "chatgpt",
               "算法", "多模态", "推理", "开源模型", "deepseek", "agi", "具身智能"],
    "ind_medical": ["医疗", "医院", "医药", "疫苗", "诊疗", "患者", "医生", "健康", "医保", "器械",
                    "生物", "癌症", "临床", "制药", "疾病", "手术", "阿尔茨海默"],
    "ind_edu": ["教育", "学校", "学生", "教师", "课程", "高校", "考试", "培训", "留学", "招生",
                "职业", "课堂", "教学", "高考"],
    "ind_energy": ["能源", "光伏", "储能", "风电", "核电", "氢能", "电力", "电网", "电池", "充电",
                   "锂", "新能源", "发电", "碳", "油气"],
    "ind_auto": ["汽车", "车企", "车型", "智驾", "自动驾驶", "特斯拉", "比亚迪", "蔚来", "小鹏",
                 "理想汽车", "座舱", "充电桩", "电动车", "小米汽车", "问界", "车展"],
    "ind_finance": ["金融", "银行", "券商", "保险", "基金", "股票", "债券", "利率", "汇率", "支付",
                    "理财", "信贷", "央行", "证监会", "股市", "上市", "融资", "投资"],
    "ind_manufacture": ["制造", "工厂", "产线", "工业", "供应链", "机器人", "设备", "产能", "数控",
                        "机床", "自动化", "智能制造", "车间"],
    "ind_consumer": ["消费", "零售", "电商", "品牌", "美妆", "食品", "饮料", "宠物", "会员店",
                     "直播带货", "折扣", "门店", "用户", "价格", "双11", "促销"],
}

_BOILERPLATE = re.compile(
    r"^(本文来自微信公众号[：:]?[^。]{0,80}[。]?|本文来自[^。]{0,60}[。]?|"
    r"作者[：:][^。]{0,30}[。]?|编辑[：:][^。]{0,30}[。]?|校对[：:][^。]{0,30}[。]?)\s*")
_NAV_PIPE = re.compile(r"(?:[\u4e00-\u9fa5]{2,8}\s*[|｜]\s*){3,}[\u4e00-\u9fa5]{2,8}")
_SYNC_LOCK = threading.Lock()
_RUNNING = False


def _norm_url(url: str) -> str:
    try:
        p = urllib.parse.urlparse(url.strip())
    except ValueError:
        return url.strip()
    path = p.path.rstrip("/") or "/"
    return f"{p.scheme}://{p.netloc}{path}"


def _news_id(url: str) -> str:
    return "news_" + hashlib.md5(_norm_url(url).encode("utf-8")).hexdigest()[:14]


def _text_of(fragment: str, max_chars: int = MAX_CONTENT) -> str:
    return websearch.extract_text(fragment or "", max_chars).strip()


def _parse_time(raw: str) -> str:
    raw = (raw or "").strip()
    if not raw:
        return db.now_utc()
    try:
        dt = parsedate_to_datetime(raw)
    except (TypeError, ValueError):
        dt = None
        for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
            try:
                dt = datetime.strptime(raw[:19], fmt)
                break
            except ValueError:
                continue
        if dt is None:
            try:
                dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
            except ValueError:
                return db.now_utc()
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone(timedelta(hours=8)))
    return db.iso(dt)


def _classify(title: str, text: str, default: str) -> str:
    hay_title = (title or "").lower()
    hay_text = (text or "").lower()[:1200]
    best, best_score = default, 0
    for industry_id, words in KEYWORDS.items():
        score = 0
        for w in words:
            if w in hay_title:
                score += 3
            elif w in hay_text:
                score += 1
        if score >= 2 and score > best_score:
            best, best_score = industry_id, score
    return best


def _child(node: ET.Element, *names: str) -> str:
    for name in names:
        for child in node:
            if child.tag.split("}")[-1].lower() == name:
                return (child.text or "").strip()
    return ""


def _parse_feed(xml_text: str, feed: dict) -> list[dict]:
    xml_text = xml_text.lstrip("\ufeff").strip()
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        cleaned = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", "", xml_text)
        root = ET.fromstring(cleaned)
    items: list[dict] = []
    for node in root.iter():
        if node.tag.split("}")[-1].lower() not in ("item", "entry"):
            continue
        title = _text_of(_child(node, "title"), 200)
        link = _child(node, "link")
        if not link:
            for child in node:
                if child.tag.split("}")[-1].lower() == "link":
                    link = (child.get("href") or "").strip()
                    break
        if not title or not link.startswith("http"):
            continue
        raw_desc = _child(node, "encoded", "description", "summary", "content")
        content = _text_of(raw_desc, MAX_CONTENT)
        for _ in range(2):
            content = _BOILERPLATE.sub("", content).strip()
        content = re.sub(r"\s{2,}", " ", _NAV_PIPE.sub(" ", content)).strip()
        if len(content) < 30:
            content = ""
        summary = content[:180] if content else title
        published = _parse_time(_child(node, "pubdate", "date", "published", "updated"))
        industry = _classify(title, content or summary, feed["default"])
        if not industry:
            continue  # 综合新闻源中与 10 个行业无关的条目直接丢弃
        items.append({
            "id": _news_id(link),
            "title": title,
            "summary": summary,
            "content": content,
            "source": feed["name"],
            "industry_id": industry,
            "publish_at": published,
            "url": link.strip(),
        })
        if len(items) >= PER_FEED:
            break
    return items


def _fetch_feed(feed: dict) -> tuple[list[dict], str]:
    try:
        page, _ = websearch.fetch_html(feed["url"], TIMEOUT)
        items = _parse_feed(page, feed)
        if not items:
            return [], f"{feed['id']}:empty"
        return items, ""
    except Exception as exc:  # noqa: BLE001 - 单个源失败不影响整体
        return [], f"{feed['id']}:{type(exc).__name__}"


def _sort_key(item: dict) -> str:
    return item["publish_at"]


def _collect() -> tuple[list[dict], list[str]]:
    items: list[dict] = []
    errors: list[str] = []
    with ThreadPoolExecutor(max_workers=8) as pool:
        for fetched, err in pool.map(_fetch_feed, FEEDS):
            items.extend(fetched)
            if err:
                errors.append(err)

    unique: dict[str, dict] = {}
    for item in items:
        key = _norm_url(item["url"])
        prev = unique.get(key)
        if prev is None:
            unique[key] = item
            continue
        # 同一链接出现在多个源时，保留正文更长、时间更早的版本
        if len(item["content"]) > len(prev["content"]):
            item["source"] = prev["source"]
            unique[key] = item
        elif item["publish_at"] < prev["publish_at"]:
            prev["publish_at"] = item["publish_at"]

    ranked = sorted(unique.values(), key=_sort_key, reverse=True)[:MAX_ITEMS]
    total = len(ranked)
    for idx, item in enumerate(ranked):
        # 热度指数：按发布时间排序生成，仅用于「热榜」排序展示
        item["heat"] = max(1000, 100000 - int(idx * (88000 / max(total - 1, 1))))
    return ranked, errors


def _persist(items: list[dict]) -> tuple[int, int]:
    db.ex("DELETE FROM news WHERE url = ''")  # 清理历史演示数据
    db.exm(
        "INSERT INTO news (id,title,summary,content,source,industry_id,publish_at,heat,status,"
        "ai_points_json,url,cover,read_count) VALUES (?,?,?,?,?,?,?,?, 'online', '[]', ?, '', 0) "
        "ON CONFLICT(id) DO UPDATE SET title=excluded.title, summary=excluded.summary, "
        "source=excluded.source, industry_id=excluded.industry_id, publish_at=excluded.publish_at, "
        "heat=excluded.heat, status='online', "
        "content=CASE WHEN length(excluded.content) > length(news.content) "
        "THEN excluded.content ELSE news.content END, "
        "ai_points_json=CASE WHEN excluded.content != news.content "
        "THEN '[]' ELSE news.ai_points_json END",
        [(i["id"], i["title"], i["summary"], i["content"], i["source"], i["industry_id"],
          i["publish_at"], i["heat"], i["url"]) for i in items],
    )
    db.ex("DELETE FROM news WHERE url != '' AND publish_at < ?", (db.days_ago(45),))
    total = db.q1("SELECT COUNT(*) AS n FROM news WHERE url != ''")["n"]
    return len(items), total


def sync(force: bool = False) -> dict:
    """抓取并写入真实资讯。force=False 时 10 分钟内不重复抓取。"""
    global _RUNNING
    if DISABLED:
        return {**status(), "error": "资讯抓取已被 MAUTO_NEWS_DISABLE 关闭"}
    with _SYNC_LOCK:
        if _RUNNING:
            return status()
        last = db.q1("SELECT * FROM news_sync WHERE id = 'latest'") or {}
        if not force and last.get("finished_at"):
            finished = db.parse_iso(last["finished_at"])
            if finished and datetime.now(timezone.utc) - finished < timedelta(minutes=10):
                return status()
        _RUNNING = True
        started = db.now_utc()
        try:
            items, errors = _collect()
            if len(items) < 5:
                raise RuntimeError("抓取到的真实资讯不足：" + ("；".join(errors[:4]) or "所有源均无结果"))
            _inserted, total = _persist(items)
            finished = db.now_utc()
            error = ("部分来源失败：" + "；".join(errors[:4])) if errors else ""
            db.ex(
                "INSERT INTO news_sync (id, started_at, finished_at, item_count, feed_ok, feed_total, error) "
                "VALUES ('latest', ?, ?, ?, ?, ?, ?) ON CONFLICT(id) DO UPDATE SET "
                "started_at=excluded.started_at, finished_at=excluded.finished_at, "
                "item_count=excluded.item_count, feed_ok=excluded.feed_ok, "
                "feed_total=excluded.feed_total, error=excluded.error",
                (started, finished, total, len(FEEDS) - len(errors), len(FEEDS), error),
            )
        except Exception as exc:  # noqa: BLE001 - 失败需如实记录
            db.ex(
                "INSERT INTO news_sync (id, started_at, finished_at, item_count, feed_ok, feed_total, error) "
                "VALUES ('latest', ?, '', 0, 0, ?, ?) ON CONFLICT(id) DO UPDATE SET "
                "started_at=excluded.started_at, error=excluded.error",
                (started, len(FEEDS), f"{type(exc).__name__}: {exc}"[:300]),
            )
        finally:
            _RUNNING = False
        return status()


def status() -> dict:
    row = db.q1("SELECT * FROM news_sync WHERE id = 'latest'") or {}
    real = db.q1("SELECT COUNT(*) AS n FROM news WHERE url != ''")["n"]
    by_industry = {r["industry_id"]: r["n"] for r in db.q(
        "SELECT industry_id, COUNT(*) AS n FROM news WHERE url != '' GROUP BY industry_id")}
    return {
        "last_sync": row.get("finished_at") or "",
        "running": _RUNNING,
        "items": real,
        "feed_ok": row.get("feed_ok", 0),
        "feed_total": row.get("feed_total", len(FEEDS)),
        "error": row.get("error", ""),
        "by_industry": by_industry,
        "sources": [f["name"] for f in FEEDS],
    }


def ensure_content(news_id: str, url: str, min_chars: int = 400) -> str:
    """详情页按需补全正文：RSS 内容过短时抓取原网页。"""
    if not url.startswith("http"):
        return ""
    text, _err = websearch.fetch_page(url, timeout=6, max_chars=6000)
    if not text or len(text) < min_chars:
        return ""
    text = _BOILERPLATE.sub("", text).strip()
    text = re.sub(r"\s{2,}", " ", _NAV_PIPE.sub(" ", text)).strip()
    db.ex("UPDATE news SET content = ?, summary = CASE WHEN length(summary) < 40 THEN ? ELSE summary END, "
          "ai_points_json = '[]' WHERE id = ?", (text, text[:180], news_id))
    return text


_auto_started = False


def _auto_loop() -> None:
    while True:
        time.sleep(max(REFRESH_MINUTES, 5) * 60)
        try:
            last = db.q1("SELECT finished_at FROM news_sync WHERE id = 'latest'") or {}
            finished = db.parse_iso(last.get("finished_at"))
            if not finished or datetime.now(timezone.utc) - finished > timedelta(minutes=REFRESH_MINUTES):
                sync(force=True)
        except Exception:  # noqa: BLE001 - 后台线程不能退出
            pass


def bootstrap() -> None:
    """启动时准备真实资讯：无数据则同步抓取，有数据则后台按间隔刷新。"""
    global _auto_started
    if DISABLED:
        return
    if not db.q1("SELECT 1 AS x FROM news WHERE url != '' LIMIT 1"):
        sync(force=True)
    if not _auto_started:
        _auto_started = True
        threading.Thread(target=_auto_loop, name="mauto-news-refresh", daemon=True).start()
