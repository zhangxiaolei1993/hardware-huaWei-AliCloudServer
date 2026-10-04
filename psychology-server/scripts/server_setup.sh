#!/usr/bin/env bash
# 服务器端环境初始化：venv、依赖、.env。可重复运行。
# 日志：/opt/psychology-server/setup.log
set -euo pipefail

APP_DIR=/opt/psychology-server
VENV=$APP_DIR/.venv
PIP_MIRROR=https://mirrors.aliyun.com/pypi/simple/

cd "$APP_DIR"

echo "===== [1/4] venv ====="
if [ ! -x "$VENV/bin/python" ]; then
    python3 -m venv "$VENV" || {
        apt-get update -qq
        apt-get install -y -qq python3-venv python3-pip
        python3 -m venv "$VENV"
    }
fi

echo "===== [2/4] pip install ====="
"$VENV/bin/python" -m pip install -q --upgrade pip -i "$PIP_MIRROR"
"$VENV/bin/python" -m pip install -q \
    -r requirements.txt -r requirements-dev.txt -i "$PIP_MIRROR"

echo "===== [3/4] .env ====="
if [ ! -f "$APP_DIR/.env" ]; then
    SECRET=$("$VENV/bin/python" -c "import secrets; print(secrets.token_hex(32))")
    cat > "$APP_DIR/.env" <<EOF
APP_NAME=psychology-server
ENV=prod
HOST=0.0.0.0
PORT=8000
DATABASE_URL=sqlite:////opt/psychology-server/psychology.db
SECRET_KEY=$SECRET
LOG_LEVEL=INFO
LOG_FILE=/opt/psychology-server/logs/app.log
DEVICE_OFFLINE_AFTER_SECONDS=90
EOF
    chmod 600 "$APP_DIR/.env"
    echo ".env created"
else
    echo ".env already exists, keep it"
fi

mkdir -p "$APP_DIR/logs"

echo "===== [4/4] versions ====="
"$VENV/bin/python" --version
"$VENV/bin/pip" list | grep -Ei 'fastapi|uvicorn|sqlalchemy|pydantic|dotenv|httpx|pytest'

echo "===== SETUP DONE ====="
