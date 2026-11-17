"""生产启动入口：uvicorn 多进程 + stdout 日志 + 反向代理头。

用法（项目根目录）：
    PYTHONPATH=src .venv/bin/python -m mauto.serve
环境变量见 README / DEPLOY.md，默认监听 127.0.0.1:8848。
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from mauto.config import configure_logging, settings  # noqa: E402


def main() -> None:
    import uvicorn

    configure_logging()
    print(
        f"[mauto] env={settings.env} listen={settings.host}:{settings.port} "
        f"workers={settings.workers} base_url={settings.base_url or 'same-origin'} "
        f"lan={settings.lan_mode} lan_ip={settings.lan_ip or '-'} "
        f"cors={','.join(settings.cors_origins) or 'same-origin'} "
        f"static_cache={settings.static_cache_seconds}s "
        f"cookie_secure={settings.cookie_secure}",
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
        timeout_keep_alive=15,
        access_log=True,
    )


if __name__ == "__main__":
    main()
