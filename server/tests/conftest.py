"""테스트 픽스처 — ASGITransport 기반 httpx AsyncClient.

테스트 DB 는 임시 파일을 사용하도록 환경변수를 미리 세팅한다(레포 더럽힘 방지).
"""

from __future__ import annotations

import os
import tempfile
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
import pytest_asyncio

# 앱 import 전에 DB 경로/설정을 임시 디렉터리로 고정
_TMP_DIR = tempfile.mkdtemp(prefix="hanbit-test-")
os.environ.setdefault("HANBIT_DB_PATH", str(Path(_TMP_DIR) / "test.db"))
os.environ.setdefault("HANBIT_ALLOW_LIVE", "false")
os.environ.setdefault("HANBIT_LOG_LEVEL", "WARNING")

from httpx import ASGITransport, AsyncClient  # noqa: E402

from app.main import create_app  # noqa: E402


@pytest.fixture(scope="session")
def app():
    return create_app()


@pytest_asyncio.fixture
async def client(app) -> AsyncIterator[AsyncClient]:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        # lifespan(init_db) 을 명시적으로 돌린다.
        async with app.router.lifespan_context(app):
            yield ac
