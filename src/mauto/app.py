"""MAuto 智能助手平台服务入口。

本地开发：
    ./scripts/run.sh
    python -m mauto.app
生产运行（多进程、stdout 日志、代理头）：
    python -m mauto.serve
"""
from __future__ import annotations

import logging
import sys
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.trustedhost import TrustedHostMiddleware

BASE_DIR = Path(__file__).resolve().parent.parent.parent
STATIC_DIR = BASE_DIR / "static"

from . import db, newssource  # noqa: E402
from .api import chat, misc, news, plugins, ppt, projects, tasks  # noqa: E402
from .config import configure_logging, settings  # noqa: E402
from .deps import ApiError  # noqa: E402
from .scheduler import start_scheduler  # noqa: E402
from .seed import seed_all  # noqa: E402

configure_logging()
logger = logging.getLogger("mauto.app")

app = FastAPI(title="MAuto 智能助手平台", version="1.0.0", docs_url="/api/docs", redoc_url=None)

if settings.allowed_hosts != ("*",):
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=list(settings.allowed_hosts))

if settings.cors_origins:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=list(settings.cors_origins),
        allow_credentials=settings.cors_allow_credentials,
        allow_methods=["*"],
        allow_headers=["*"],
    )


@app.middleware("http")
async def cache_headers(request: Request, call_next):
    response = await call_next(request)
    path = request.url.path
    if path.startswith("/api/") or path in ("/healthz", "/readyz"):
        response.headers["Cache-Control"] = "no-store"
    elif path.startswith("/static/"):
        if settings.static_cache_seconds > 0:
            response.headers["Cache-Control"] = (
                f"public, max-age={settings.static_cache_seconds}, immutable"
            )
        else:
            response.headers["Cache-Control"] = "no-cache, must-revalidate"
    else:
        # index.html 与 SPA 回退页面始终校验，保证发布后立即生效
        response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    return response


@app.exception_handler(ApiError)
async def api_error_handler(request: Request, exc: ApiError):
    return JSONResponse(status_code=exc.status_code,
                        content={"code": exc.code, "message": exc.message, "data": None})


@app.exception_handler(RequestValidationError)
async def validation_handler(request: Request, exc: RequestValidationError):
    first = exc.errors()[0] if exc.errors() else {}
    field = ".".join(str(x) for x in first.get("loc", [])[1:]) or "参数"
    return JSONResponse(status_code=422, content={
        "code": 422, "message": f"参数错误：{field} {first.get('msg', '')}".strip(), "data": None,
    })


@app.exception_handler(Exception)
async def unhandled_handler(request: Request, exc: Exception):
    return JSONResponse(status_code=500,
                        content={"code": 500, "message": f"服务异常：{type(exc).__name__}", "data": None})


@app.on_event("startup")
def on_startup() -> None:
    logger.info(
        "启动配置 env=%s base_url=%s host=%s port=%s workers=%s lan=%s lan_ip=%s data_dir=%s",
        settings.env, settings.base_url or "(same-origin)", settings.host,
        settings.port, settings.workers, settings.lan_mode, settings.lan_ip or "-", db.DATA_DIR,
    )
    # 多 worker 同时启动时串行执行种子数据，避免唯一键冲突
    seed_lock = db.acquire_lock("seed")
    try:
        seed_all()
    finally:
        db.release_lock(seed_lock)
    # 资讯首次抓取只由拿到锁的进程执行，其余进程跳过并依赖定时刷新
    news_lock = db.acquire_lock("news-bootstrap", blocking=False)
    if news_lock is not None:
        try:
            newssource.bootstrap()
        finally:
            db.release_lock(news_lock)
    start_scheduler()
    db.ex("DELETE FROM ppt_job WHERE status = 'running' AND created_at < ?", (db.days_ago(1),))


@app.get("/healthz", include_in_schema=False)
def healthz():
    """容器/负载均衡健康检查：进程存活 + 数据库可读。"""
    db_status = "ok"
    try:
        db.q1("SELECT 1 AS ok")
    except Exception as exc:  # noqa: BLE001
        db_status = f"error: {type(exc).__name__}"
    healthy = db_status == "ok"
    return JSONResponse(status_code=200 if healthy else 503, content={
        "status": "ok" if healthy else "degraded",
        "service": "mauto-platform",
        "version": app.version,
        "env": settings.env,
        "base_url": settings.base_url or None,
        "db": db_status,
        "time": db.now_utc(),
    })


API_PREFIX = "/api/v1"
for module in (chat, tasks, news, plugins, ppt, projects, misc):
    app.include_router(module.router, prefix=API_PREFIX)

if STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


@app.get("/")
def index():
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/favicon.svg")
def favicon():
    return FileResponse(STATIC_DIR / "favicon.svg")


@app.get("/{full_path:path}")
def spa_fallback(full_path: str):
    candidate = STATIC_DIR / full_path
    if candidate.is_file():
        return FileResponse(candidate)
    return FileResponse(STATIC_DIR / "index.html")


def main() -> None:
    import uvicorn

    configure_logging()
    print(
        f"MAuto 智能助手平台已启动： http://{settings.host}:{settings.port} "
        f"(env={settings.env}, base_url={settings.base_url or 'same-origin'})",
        flush=True,
    )
    uvicorn.run(
        "mauto.app:app",
        host=settings.host,
        port=settings.port,
        workers=settings.workers,
        log_config=None,
        log_level=settings.log_level,
        proxy_headers=settings.proxy_headers,
        forwarded_allow_ips=settings.forwarded_allow_ips,
    )


if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    main()
