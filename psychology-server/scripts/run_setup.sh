#!/usr/bin/env bash
# 等待旧的 pip 进程结束，再执行环境初始化（避免并发写同一个 venv）。
while pgrep -f "pip install -q -r requirements" >/dev/null 2>&1; do
    sleep 5
done
cd /opt/psychology-server
bash scripts/server_setup.sh
