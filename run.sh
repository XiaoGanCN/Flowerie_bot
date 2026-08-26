#!/usr/bin/env bash
# 花璃守护脚本：进程意外退出/崩溃后自动重启（配合上下文的周期备份，重启自动恢复）
# 用法：bash run.sh   （或 chmod +x run.sh && ./run.sh）
cd "$(dirname "$0")"
mkdir -p logs
while true; do
  echo "[run.sh] $(date '+%F %T') 启动花璃..."
  python3 main.py
  code=$?
  echo "[run.sh] $(date '+%F %T') 花璃退出 (code=$code)，5 秒后自动重启..." >> logs/run.log
  sleep 5
done
