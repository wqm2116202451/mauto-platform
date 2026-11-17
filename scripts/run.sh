#!/usr/bin/env bash
# MAuto 智能助手平台启动脚本（本地开发 / 单机生产均可用）
# 兼容旧变量 HOST / PORT，也支持 MAUTO_HOST / MAUTO_PORT 等部署配置。
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

PYTHON="${PYTHON:-python3}"
export MAUTO_LAN="${MAUTO_LAN:-0}"
if [ -z "${MAUTO_HOST:-}" ]; then
  if [ -n "${HOST:-}" ]; then
    export MAUTO_HOST="$HOST"
  elif [ "$MAUTO_LAN" = "1" ]; then
    export MAUTO_HOST="0.0.0.0"
  else
    export MAUTO_HOST="127.0.0.1"
  fi
fi
export MAUTO_PORT="${MAUTO_PORT:-${PORT:-8848}}"
export MAUTO_ENV="${MAUTO_ENV:-development}"
export PYTHONPATH="${PYTHONPATH:-$ROOT/src}"

if [ ! -x .venv/bin/python ]; then
  echo "[mauto] 创建虚拟环境..."
  "$PYTHON" -m venv .venv
  ./.venv/bin/python -m pip install --upgrade pip >/dev/null
  ./.venv/bin/python -m pip install -r requirements.txt
fi

echo "[mauto] 启动服务：http://${MAUTO_HOST}:${MAUTO_PORT} (env=${MAUTO_ENV})"
exec ./.venv/bin/python -m mauto.serve
