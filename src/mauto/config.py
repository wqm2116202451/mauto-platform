"""集中配置：所有与部署环境相关的开关统一从环境变量读取。

本地开发的默认值与原始启动方式保持一致（127.0.0.1:8848）；
设置 MAUTO_ENV=production 后切换到更保守的默认值（同源 CORS、HTTPS Cookie、
静态资源长缓存、stdout 结构化日志），域名与托管平台通过环境变量注入，
代码中不写死任何主机、端口或域名。
"""
from __future__ import annotations

import logging
import os
import socket
import sys
from dataclasses import dataclass
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent.parent
TOKEN_COOKIE_NAME = "mauto_token"
_TRUE_VALUES = {"1", "true", "yes", "on"}
_SAMESITE_VALUES = {"lax", "strict", "none"}


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


def _bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    return default if raw is None else raw.strip().lower() in _TRUE_VALUES


def _int(name: str, default: int) -> int:
    try:
        return int(_env(name, str(default)))
    except ValueError:
        return default


def _tuple(name: str, default: tuple[str, ...] = ()) -> tuple[str, ...]:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    return tuple(item.strip() for item in raw.split(",") if item.strip())


def _base_url(env: str, host: str, port: int, lan_mode: bool, lan_ip: str) -> str:
    explicit = _env("MAUTO_BASE_URL").rstrip("/")
    if explicit:
        return explicit
    if lan_mode and lan_ip:
        return f"http://{lan_ip}:{port}"
    if env != "production" and host not in ("", "0.0.0.0", "::"):
        return f"http://{host}:{port}"
    return ""


def _detect_lan_ip() -> str:
    """探测本机局域网 IPv4（不产生真实流量），失败返回空字符串。"""
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            sock.connect(("192.0.2.1", 80))  # TEST-NET-1 保留地址
            ip = sock.getsockname()[0]
        finally:
            sock.close()
    except OSError:
        return ""
    return "" if ip.startswith("127.") or ip == "0.0.0.0" else ip


@dataclass(frozen=True)
class Settings:
    env: str
    host: str
    port: int
    base_url: str
    lan_mode: bool
    lan_ip: str
    data_dir: Path
    cors_origins: tuple[str, ...]
    allowed_hosts: tuple[str, ...]
    cookie_secure: bool
    cookie_samesite: str
    cookie_domain: str
    static_cache_seconds: int
    workers: int
    log_level: str
    forwarded_allow_ips: str
    proxy_headers: bool
    enable_scheduler: bool

    @property
    def is_production(self) -> bool:
        return self.env == "production"

    @property
    def cors_allow_credentials(self) -> bool:
        return self.cors_origins != ("*",)


def load_settings() -> Settings:
    env = (_env("MAUTO_ENV", "development") or "development").lower()
    production = env == "production"
    lan_mode = _bool("MAUTO_LAN", False)
    host = _env("MAUTO_HOST") or ("0.0.0.0" if lan_mode else "127.0.0.1")
    port = _int("MAUTO_PORT", 8848)
    expose_lan = lan_mode or host in ("0.0.0.0", "::")
    lan_ip = _detect_lan_ip() if expose_lan else ""
    base_url = _base_url(env, host, port, lan_mode, lan_ip)

    origins = _tuple("MAUTO_CORS_ORIGINS", () if production else ("*",))
    if origins != ("*",) and lan_mode:
        lan_origins = [
            f"http://{lan_ip}:{port}" if lan_ip else "",
            f"http://localhost:{port}",
            f"http://127.0.0.1:{port}",
            f"http://mauto.local:{port}",
        ]
        origins = tuple(dict.fromkeys((*origins, *(o for o in lan_origins if o))))

    allowed_hosts = _tuple("MAUTO_ALLOWED_HOSTS", ("*",))
    if allowed_hosts != ("*",):
        # 容器/本机健康检查与局域网访问需要保留这些 Host
        extra_hosts = ["127.0.0.1", "localhost"]
        if lan_ip:
            extra_hosts.append(lan_ip)
        if lan_mode:
            extra_hosts.extend(["mauto.local", "*.local"])
        allowed_hosts = tuple(dict.fromkeys((*allowed_hosts, *extra_hosts)))

    # 局域网 HTTP 场景不能默认 Secure，否则浏览器不会保存登录 Cookie
    secure_default = base_url.startswith("https://") if base_url else production
    cookie_secure = _bool("MAUTO_COOKIE_SECURE", secure_default)
    samesite = (_env("MAUTO_COOKIE_SAMESITE", "lax") or "lax").lower()
    if samesite not in _SAMESITE_VALUES:
        samesite = "lax"
    # 浏览器要求 SameSite=None 必须配合 Secure
    if samesite == "none":
        cookie_secure = True

    log_level = (_env("MAUTO_LOG_LEVEL", "info") or "info").lower()
    if log_level not in ("critical", "error", "warning", "info", "debug", "trace"):
        log_level = "info"

    return Settings(
        env=env,
        host=host,
        port=port,
        base_url=base_url,
        lan_mode=lan_mode,
        lan_ip=lan_ip,
        data_dir=Path(_env("MAUTO_DATA_DIR", str(BASE_DIR / "data"))).resolve(),
        cors_origins=origins,
        allowed_hosts=allowed_hosts,
        cookie_secure=cookie_secure,
        cookie_samesite=samesite,
        cookie_domain=_env("MAUTO_COOKIE_DOMAIN"),
        static_cache_seconds=max(0, _int("MAUTO_STATIC_CACHE_SECONDS", 86400 if production else 0)),
        workers=max(1, _int("MAUTO_WORKERS", 1)),
        log_level=log_level,
        forwarded_allow_ips=_env("MAUTO_FORWARDED_ALLOW_IPS", "127.0.0.1"),
        proxy_headers=_bool("MAUTO_PROXY_HEADERS", True),
        enable_scheduler=_bool("MAUTO_ENABLE_SCHEDULER", True),
    )


settings = load_settings()


def configure_logging() -> None:
    """统一输出到 stdout，容器与 systemd 可直接采集。"""
    level = getattr(logging, settings.log_level.upper(), logging.INFO)
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(logging.Formatter(
        "%(asctime)s %(levelname)s %(name)s %(message)s", "%Y-%m-%dT%H:%M:%S%z",
    ))
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(level)
    # uvicorn 自带 logger 在 log_config=None 时无 handler，交给 root 统一输出
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        logger = logging.getLogger(name)
        logger.handlers[:] = []
        logger.propagate = True
        logger.setLevel(level)
