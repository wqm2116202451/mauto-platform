"""真实联网检索：搜索结果抓取 + 网页正文提取。

仅依赖 Python 标准库，无需额外 Key。默认走 360 搜索（中文问句相关性更好），
失败时依次回退搜狗与 Bing。可用环境变量调整：

    MAUTO_SEARCH_ENGINE       auto（默认）/ 360 / sogou / bing
    MAUTO_SEARCH_BASE         Bing 搜索地址，默认 https://cn.bing.com/search
    MAUTO_SEARCH_MAX_RESULTS  最多返回结果数，默认 5
    MAUTO_SEARCH_FETCH_PAGES  实际抓取正文的页面数，默认 5
    MAUTO_SEARCH_TIMEOUT      单次请求超时（秒），默认 6
    MAUTO_SEARCH_CHARS        单页正文最多保留字符数，默认 4000
    MAUTO_SEARCH_DISABLE      设为 1 时禁用联网检索
    MAUTO_CA_BUNDLE           自定义 CA 证书路径（部分 Python 缺少根证书时使用）
"""
from __future__ import annotations

import base64
import gzip
import html
import json
import os
import re
import ssl
import time
import urllib.parse
import urllib.request
import zlib
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone, timedelta

SEARCH_BASE = os.environ.get("MAUTO_SEARCH_BASE", "https://cn.bing.com/search")
MAX_RESULTS = int(os.environ.get("MAUTO_SEARCH_MAX_RESULTS", "5"))
FETCH_PAGES = int(os.environ.get("MAUTO_SEARCH_FETCH_PAGES", "5"))
TIMEOUT = float(os.environ.get("MAUTO_SEARCH_TIMEOUT", "6"))
MAX_CHARS = int(os.environ.get("MAUTO_SEARCH_CHARS", "4000"))
DISABLED = os.environ.get("MAUTO_SEARCH_DISABLE", "") == "1"

UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")
CN_TZ = timezone(timedelta(hours=8))

_CACHE: dict[str, tuple[float, dict]] = {}
_CACHE_TTL = 600.0
_SSL_CONTEXT: ssl.SSLContext | None = None

_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"[\t\r\f\v ]+")
_BLOCK_RE = re.compile(r"(?is)<(script|style|noscript|template|svg|iframe|nav|header|footer|aside|form)[^>]*>.*?</\1>")
_SENT_SPLIT = re.compile(r"(?<=[。！？!?；;])\s*|\n+")


def _ssl_context() -> ssl.SSLContext:
    """部分 Python 安装不带 CA 证书，按候选路径兜底。"""
    global _SSL_CONTEXT
    if _SSL_CONTEXT is not None:
        return _SSL_CONTEXT
    candidates = [os.environ.get("MAUTO_CA_BUNDLE", ""), "/etc/ssl/cert.pem",
                  "/opt/homebrew/etc/openssl@3/cert.pem", "/usr/local/etc/openssl@3/cert.pem"]
    for path in candidates:
        if path and os.path.isfile(path):
            try:
                _SSL_CONTEXT = ssl.create_default_context(cafile=path)
                return _SSL_CONTEXT
            except ssl.SSLError:
                continue
    try:
        import certifi  # type: ignore
        _SSL_CONTEXT = ssl.create_default_context(cafile=certifi.where())
    except Exception:  # noqa: BLE001 - 无 certifi 时使用系统默认
        _SSL_CONTEXT = ssl.create_default_context()
    return _SSL_CONTEXT


def _headers() -> dict:
    return {
        "User-Agent": UA,
        "Accept": "text/html,application/xhtml+xml",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.7",
        "Accept-Encoding": "identity",
    }


def _get(url: str, timeout: float) -> tuple[str, str]:
    """返回 (解码后的文本, 最终 URL)。"""
    req = urllib.request.Request(url, headers=_headers())
    with urllib.request.urlopen(req, timeout=timeout, context=_ssl_context()) as resp:
        raw = resp.read(2_500_000)
        final_url = resp.geturl()
        charset = resp.headers.get_content_charset()
    if raw[:2] == b"\x1f\x8b":
        try:
            raw = gzip.decompress(raw)
        except OSError:
            pass
    elif raw[:1] == b"\x78":
        try:
            raw = zlib.decompress(raw)
        except zlib.error:
            pass
    if not charset:
        head = raw[:2048].decode("ascii", errors="ignore")
        m = re.search(r'charset=["\']?([\w-]+)', head, re.I)
        charset = m.group(1) if m else "utf-8"
    try:
        text = raw.decode(charset, errors="ignore")
    except LookupError:
        text = raw.decode("utf-8", errors="ignore")
    return text, final_url


def fetch_html(url: str, timeout: float | None = None) -> tuple[str, str]:
    """抓取网页原始 HTML，返回 (文本, 最终 URL)。供资讯 RSS 抓取复用。"""
    return _get(url, timeout or TIMEOUT)


def _text_of(fragment: str) -> str:
    return html.unescape(_TAG_RE.sub("", fragment or "")).strip()


def _decode_redirect(url: str) -> str:
    """还原 Bing 的 ck/a 跳转链接。"""
    if "bing.com/ck/a" not in url:
        return url
    try:
        qs = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)
        raw = (qs.get("u") or [""])[0]
        if raw.startswith("a1"):
            raw = raw[2:]
        raw += "=" * (-len(raw) % 4)
        decoded = base64.urlsafe_b64decode(raw).decode("utf-8", errors="ignore")
        if decoded.startswith("http"):
            return decoded
    except Exception:  # noqa: BLE001 - 解析失败时退回原链接
        pass
    return url


def _site_of(url: str) -> str:
    try:
        return urllib.parse.urlparse(url).netloc.removeprefix("www.")
    except ValueError:
        return ""


def _search_360(query: str, limit: int, timeout: float) -> list[dict]:
    """360 搜索：中文长问句的相关性明显好于 Bing 国内版。"""
    url = "https://www.so.com/s?" + urllib.parse.urlencode({"q": query})
    page, _ = _get(url, timeout)
    blocks = re.split(r'<li[^>]*class="[^"]*res-list[^"]*"[^>]*>', page)[1:]
    results: list[dict] = []
    seen: set[str] = set()
    for block in blocks:
        block = block.split("</li>")[0]
        h3 = re.search(r'<h3[^>]*class="[^"]*res-title[^"]*"[^>]*>(.*?)</h3>', block, re.S)
        if not h3:
            continue
        md = re.search(r'data-mdurl="([^"]+)"', h3.group(1))
        href = re.search(r'href="([^"]+)"', h3.group(1))
        link = html.unescape(md.group(1) if md else (href.group(1) if href else ""))
        if not link.startswith("http") or "so.com/link" in link:
            continue
        if any(host in link for host in ("wenku.so.com", "image.so.com", "ai.so.com", "www.so.com")):
            continue
        key = link.split("#")[0]
        if key in seen:
            continue
        seen.add(key)
        desc = re.search(r'<p[^>]*class="[^"]*res-desc[^"]*"[^>]*>(.*?)</p>', block, re.S)
        date = ""
        if desc:
            date_match = re.search(r'(\d{4}年\d{1,2}月\d{1,2}日|\d{4}-\d{2}-\d{2}|\d+\s*(?:天|小时|分钟)前)',
                                   _text_of(desc.group(1)))
            date = date_match.group(1) if date_match else ""
        results.append({
            "title": _text_of(h3.group(1)) or link,
            "url": link,
            "site": _site_of(link),
            "snippet": re.sub(r"\s+", " ", _text_of(desc.group(1)) if desc else "")[:300],
            "date": date,
        })
        if len(results) >= limit:
            break
    return results


def _search_sogou(query: str, limit: int, timeout: float) -> list[dict]:
    url = "https://www.sogou.com/web?" + urllib.parse.urlencode({"query": query})
    page, _ = _get(url, timeout)
    blocks = re.split(r'<div class="vrwrap"', page)[1:]
    results: list[dict] = []
    seen: set[str] = set()
    for block in blocks:
        item: dict = {}
        raw = re.search(r'<script[^>]*type="application/json"[^>]*>(.*?)</script>', block, re.S)
        if raw:
            try:
                data = json.loads(raw.group(1))
                item = {
                    "url": data.get("url") or "",
                    "title": _text_of(data.get("title") or ""),
                    "snippet": _text_of(data.get("content") or ""),
                    "date": data.get("date") or "",
                }
            except json.JSONDecodeError:
                item = {}
        if not item.get("url"):
            h3 = re.search(r'<h3[^>]*>.*?<a[^>]*href="([^"]+)"[^>]*>(.*?)</a>', block, re.S)
            if not h3:
                continue
            item = {"url": html.unescape(h3.group(1)), "title": _text_of(h3.group(2)),
                    "snippet": "", "date": ""}
        link = item["url"]
        if link.startswith("//"):
            link = "https:" + link
        elif link.startswith("/"):
            link = "https://www.sogou.com" + link
        if not link.startswith("http") or "sogou.com/web" in link:
            continue
        key = link.split("#")[0]
        if key in seen:
            continue
        seen.add(key)
        results.append({
            "title": item.get("title") or link,
            "url": link,
            "site": _site_of(link),
            "snippet": re.sub(r"\s+", " ", item.get("snippet") or "")[:300],
            "date": item.get("date") or "",
        })
        if len(results) >= limit:
            break
    return results


def _search_bing(query: str, limit: int, timeout: float) -> list[dict]:
    params = urllib.parse.urlencode({
        "q": query, "setlang": "zh-CN", "ensearch": "0", "count": max(limit + 3, 10),
    })
    page, _ = _get(f"{SEARCH_BASE}?{params}", timeout)
    blocks = re.split(r'<li class="b_algo"', page)[1:]
    results: list[dict] = []
    seen: set[str] = set()
    for block in blocks:
        block = block.split("</li>")[0]
        href = re.search(r'<h2[^>]*>\s*<a[^>]*href="([^"]+)"', block)
        title = re.search(r'<h2[^>]*>\s*<a[^>]*>(.*?)</a>', block, re.S)
        if not href or not title:
            continue
        url = _decode_redirect(html.unescape(href.group(1)))
        if not url.startswith("http"):
            continue
        key = url.split("#")[0]
        if key in seen:
            continue
        seen.add(key)
        snippet = ""
        sn = (re.search(r'<p[^>]*class="b_lineclamp[^"]*"[^>]*>(.*?)</p>', block, re.S)
              or re.search(r'<p[^>]*>(.*?)</p>', block, re.S))
        if sn:
            snippet = _text_of(sn.group(1))
        results.append({
            "title": _text_of(title.group(1)) or url,
            "url": url,
            "site": _site_of(url),
            "snippet": re.sub(r"\s+", " ", snippet)[:300],
            "date": "",
        })
        if len(results) >= limit:
            break
    return results


def search(query: str, limit: int | None = None, timeout: float | None = None) -> tuple[list[dict], str]:
    """抓取搜索结果列表，返回 (结果, 实际使用的引擎)。默认搜狗，失败时回退 Bing。"""
    limit = limit or MAX_RESULTS
    timeout = timeout or TIMEOUT
    engine = os.environ.get("MAUTO_SEARCH_ENGINE", "auto").lower()
    order = ["360", "sogou", "bing"] if engine in ("auto", "") else [engine]
    errors: list[str] = []
    for name in order:
        try:
            if name == "360":
                results = _search_360(query, limit, timeout)
            elif name == "sogou":
                results = _search_sogou(query, limit, timeout)
            else:
                results = _search_bing(query, limit, timeout)
        except Exception as exc:  # noqa: BLE001 - 单个引擎失败可切换
            errors.append(f"{name}:{type(exc).__name__}")
            continue
        if results:
            return results, name
        errors.append(f"{name}:empty")
    if errors:
        raise RuntimeError("；".join(errors))
    return [], "none"


def extract_text(page: str, max_chars: int | None = None) -> str:
    """从 HTML 中提取可读正文（优先 <p> 段落，其次 article / main）。"""
    max_chars = max_chars or MAX_CHARS
    page = _BLOCK_RE.sub(" ", page)
    # 多数新闻站正文由 <p> 组成，优先取段落可避开顶部导航/频道列表
    paras: list[str] = []
    for m in re.finditer(r"(?is)<p[^>]*>(.*?)</p>", page):
        text = html.unescape(_TAG_RE.sub(" ", m.group(1)))
        text = _WS_RE.sub(" ", text).strip()
        if len(text) >= 20:
            paras.append(text)
    joined = "\n".join(paras)
    if len(joined) >= 400:
        return joined[:max_chars]
    scoped = page
    for tag in ("article", "main"):
        m = re.search(rf"(?is)<{tag}[^>]*>(.*?)</{tag}>", page)
        if m and len(_TAG_RE.sub(" ", m.group(1))) > 400:
            scoped = m.group(1)
            break
    if scoped is page:
        m = re.search(r"(?is)<body[^>]*>(.*?)</body>", page)
        if m:
            scoped = m.group(1)
    text = _TAG_RE.sub(" ", scoped)
    text = html.unescape(text)
    text = _WS_RE.sub(" ", text)
    text = re.sub(r"\n\s*\n+", "\n", text)
    text = re.sub(r"(\s*\n\s*)+", "\n", text).strip()
    text = re.sub(r" {2,}", " ", text)
    return text[:max_chars]


def fetch_page(url: str, timeout: float | None = None, max_chars: int | None = None) -> tuple[str, str]:
    """抓取并提取单页正文，返回 (正文, 错误说明)。"""
    try:
        page, _ = _get(url, timeout or TIMEOUT)
        text = extract_text(page, max_chars)
        if len(text) < 120:
            return "", "正文过短（可能是脚本渲染页面）"
        return text, ""
    except Exception as exc:  # noqa: BLE001 - 单页失败不影响整体
        return "", f"{type(exc).__name__}: {exc}"[:120]


def retrieve(query: str, limit: int | None = None, pages: int | None = None,
             timeout: float | None = None) -> dict:
    """一次完整检索：搜索 + 并发抓取正文。返回 {results, error, fetched_at, engine}。"""
    started = time.time()
    now = datetime.now(CN_TZ).strftime("%Y-%m-%d %H:%M")
    base = {"query": query, "engine": "auto", "fetched_at": now,
            "elapsed_ms": 0, "results": [], "error": None}
    if DISABLED:
        base["error"] = "联网检索已被服务端禁用（MAUTO_SEARCH_DISABLE=1）"
        return base
    key = f"{query}|{limit}|{pages}"
    hit = _CACHE.get(key)
    if hit and time.time() - hit[0] < _CACHE_TTL:
        cached = dict(hit[1])
        cached["cached"] = True
        return cached
    try:
        results, engine_name = search(query, limit=limit, timeout=timeout)
    except Exception as exc:  # noqa: BLE001 - 搜索引擎不可达
        base["error"] = f"搜索请求失败（{type(exc).__name__}）"
        base["elapsed_ms"] = int((time.time() - started) * 1000)
        return base
    base["engine"] = engine_name
    if not results:
        base["error"] = "搜索引擎未返回有效结果（可能被限流或该问题不适合检索）"
        base["elapsed_ms"] = int((time.time() - started) * 1000)
        return base

    fetch_n = min(pages or FETCH_PAGES, len(results))
    with ThreadPoolExecutor(max_workers=max(1, fetch_n)) as pool:
        fetched = list(pool.map(lambda r: fetch_page(r["url"], timeout), results[:fetch_n]))
    for item, (text, err) in zip(results, fetched):
        item["text"] = text
        item["fetch_error"] = err
    base["results"] = results
    base["elapsed_ms"] = int((time.time() - started) * 1000)
    if not any(r.get("text") for r in results):
        base["error"] = "搜索结果网页均无法读取正文，仅保留标题与摘要"
    _CACHE[key] = (time.time(), base)
    return base


def to_citations(results: list[dict]) -> list[dict]:
    """转换为前端引用卡片结构（可点击跳转）。"""
    out = []
    for r in results:
        out.append({
            "title": r.get("title") or r.get("url", ""),
            "url": r.get("url", ""),
            "site": r.get("site", ""),
            "time": (f"发布于 {r['date']}" if r.get("date") else
                     f"抓取于 {datetime.now(CN_TZ).strftime('%m-%d %H:%M')}"),
            "snippet": (r.get("text") or r.get("snippet") or "")[:180],
        })
    return out


def split_sentences(text: str) -> list[str]:
    parts = [p.strip() for p in _SENT_SPLIT.split(text or "") if p and p.strip()]
    return [p for p in parts if len(p) >= 12]
