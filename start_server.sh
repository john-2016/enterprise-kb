#!/bin/bash
# 企业知识库启动脚本
# 关键：set -a 让 source 的所有变量自动 export 到 env
# 这样 uvicorn python 子进程才能读到 .env 里的 MINIMAX_API_KEY 等
cd /root/enterprise-kb
set -a
# shellcheck disable=SC1091
source .env
set +a
source .venv/bin/activate
exec python -m uvicorn backend.main:app --host 0.0.0.0 --port 8000
