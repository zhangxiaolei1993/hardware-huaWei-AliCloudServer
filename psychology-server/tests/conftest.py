"""pytest 全局 fixture：使用独立 SQLite 测试库，不污染开发数据。"""
import os
import sys
import tempfile
from pathlib import Path

# 必须在导入 app 之前设置测试数据库
_TEST_DB_PATH = os.path.join(tempfile.gettempdir(), "psychology_test.db")
if os.path.exists(_TEST_DB_PATH):
    os.remove(_TEST_DB_PATH)
os.environ["DATABASE_URL"] = f"sqlite:///{_TEST_DB_PATH}"
os.environ["LOG_FILE"] = os.path.join(tempfile.gettempdir(), "psychology_test.log")

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.db.database import SessionLocal  # noqa: E402
from app.main import app  # noqa: E402


@pytest.fixture(scope="session")
def client() -> TestClient:
    with TestClient(app) as c:
        yield c


@pytest.fixture(scope="session")
def db_session():
    """直接操作数据库用（如心跳超时模拟）。"""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
