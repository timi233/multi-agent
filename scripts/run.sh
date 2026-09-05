#!/bin/sh
# Pi 平台 MVP 一键脚本（实验配置）
set -eu
cd "$(dirname "$0")/.."

VENV=.venv
PY=$VENV/bin/python

cmd="${1:-help}"
case "$cmd" in
  setup)
    python3 -m venv "$VENV"
    "$VENV/bin/pip" install -q -r requirements.txt
    echo "[setup] done"
    ;;
  migrate)
    "$PY" -m app.migrate
    ;;
  serve)
    port="${2:-8990}"
    echo "[serve] http://127.0.0.1:$port （Web 界面 / 后台 worker 自动启动）"
    exec "$PY" -m app.main serve --host 127.0.0.1 --port "$port"
    ;;
  test)
    "$VENV/bin/pip" install -q pytest
    "$PY" -m pytest tests/ -q
    ;;
  submit)
    shift
    "$PY" -m app.main submit "$@"
    ;;
  status)
    "$PY" -m app.main status "${2:?need task_id}"
    ;;
  list)
    "$PY" -m app.main list
    ;;
  *)
    echo "用法: $0 {setup|migrate|serve [port]|test|submit <prompt>|status <id>|list}"
    ;;
esac