"""SQLite 연결 + 마이그레이션 러너.

aiosqlite 로 비동기 연결을 열고, migrations/*.sql 을 파일명 순으로 적용한다.
이미 적용된 마이그레이션은 schema_migrations 테이블로 추적해 재적용하지 않는다.
"""

from __future__ import annotations

from pathlib import Path

import aiosqlite

from app.logging_setup import get_logger

logger = get_logger(__name__)

# server/ 루트 기준 migrations 폴더
_MIGRATIONS_DIR = Path(__file__).resolve().parents[2] / "migrations"

_SCHEMA_MIGRATIONS_DDL = """
CREATE TABLE IF NOT EXISTS schema_migrations (
    filename    TEXT PRIMARY KEY,
    applied_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);
"""


async def _apply_pragmas(db: aiosqlite.Connection) -> None:
    """WAL 모드 등 PRAGMA 설정."""
    await db.execute("PRAGMA journal_mode=WAL;")
    await db.execute("PRAGMA foreign_keys=ON;")
    await db.commit()


def _migration_files() -> list[Path]:
    """migrations 폴더의 .sql 파일을 이름 순으로 반환."""
    if not _MIGRATIONS_DIR.is_dir():
        return []
    return sorted(_MIGRATIONS_DIR.glob("*.sql"), key=lambda p: p.name)


async def _already_applied(db: aiosqlite.Connection) -> set[str]:
    async with db.execute("SELECT filename FROM schema_migrations") as cur:
        rows = await cur.fetchall()
    return {row[0] for row in rows}


async def init_db(db_path: str) -> None:
    """DB 파일을 보장하고 미적용 마이그레이션을 순서대로 적용한다."""
    path = Path(db_path)
    if path.parent and not path.parent.exists():
        path.parent.mkdir(parents=True, exist_ok=True)

    async with aiosqlite.connect(db_path) as db:
        await _apply_pragmas(db)
        await db.executescript(_SCHEMA_MIGRATIONS_DDL)
        await db.commit()

        applied = await _already_applied(db)
        files = _migration_files()
        pending = [f for f in files if f.name not in applied]

        if not pending:
            logger.info("DB up to date — %d migration(s) already applied", len(applied))
            return

        for f in pending:
            sql = f.read_text(encoding="utf-8")
            logger.info("applying migration %s", f.name)
            await db.executescript(sql)
            await db.execute(
                "INSERT INTO schema_migrations (filename) VALUES (?)", (f.name,)
            )
            await db.commit()

        logger.info("applied %d new migration(s)", len(pending))


def db_connection(db_path: str) -> aiosqlite.Connection:
    """원자적 작업용 aiosqlite 연결 컨텍스트매니저를 반환한다(M1+에서 사용)."""
    return aiosqlite.connect(db_path)
