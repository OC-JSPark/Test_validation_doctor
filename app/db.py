"""신규 로컬 PostgreSQL(validation_db) 접속.

- 커넥션 풀은 프로세스당 1개만 만든다. Streamlit 은 상호작용마다 스크립트를
  재실행하므로, 캐시하지 않으면 매번 새 커넥션이 생긴다 (CLAUDE.md).
- 리포지토리 계층은 절대 commit 하지 않는다. 커밋은 이 모듈의
  `connection()` 컨텍스트 매니저가 담당하고, 테스트는 롤백으로 격리한다.
"""

from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

import psycopg
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

from app.config import get_settings

SQL_DIR = Path(__file__).resolve().parent.parent / "sql"

_pool: ConnectionPool | None = None


def _create_pool() -> ConnectionPool:
    settings = get_settings()
    return ConnectionPool(
        settings.database_url,
        min_size=1,
        max_size=5,
        open=True,
        kwargs={"row_factory": dict_row},
    )


def get_pool() -> ConnectionPool:
    """프로세스 전역 커넥션 풀. Streamlit 에서는 `st.cache_resource` 로 한 번 더 감싼다."""
    global _pool
    if _pool is None:
        _pool = _create_pool()
    return _pool


def close_pool() -> None:
    global _pool
    if _pool is not None:
        _pool.close()
        _pool = None


@contextmanager
def connection() -> Iterator[psycopg.Connection]:
    """트랜잭션 1개를 여는 컨텍스트 매니저. 정상 종료 시 커밋, 예외 시 롤백."""
    with get_pool().connection() as conn:
        yield conn


def migration_files() -> list[Path]:
    return sorted(SQL_DIR.glob("*.sql"))


def run_migrations(conn: psycopg.Connection) -> list[str]:
    """`sql/` 아래 마이그레이션을 순서대로 실행한다 (전부 멱등).

    적용한 파일 이름 목록을 돌려준다.
    """
    applied: list[str] = []
    for path in migration_files():
        conn.execute(path.read_text(encoding="utf-8"))
        applied.append(path.name)
    return applied
