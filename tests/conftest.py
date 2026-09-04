"""공용 픽스처.

DB 테스트는 실제 Docker PostgreSQL(validation_db) 에 붙는다 (CLAUDE.md).
각 테스트는 트랜잭션을 열고 끝나면 무조건 롤백하므로 데이터가 남지 않는다.
"""

from __future__ import annotations

import uuid
from typing import Iterator

import psycopg
import pytest
from psycopg.rows import dict_row

from app.config import get_settings
from app.db import run_migrations
from app.models import ROLE_ADMIN, ROLE_DOCTOR
from app.repositories import users as users_repo


@pytest.fixture(scope="session")
def database_url() -> str:
    return get_settings().database_url


@pytest.fixture(scope="session", autouse=True)
def _ensure_schema(database_url: str) -> None:
    """스키마가 없으면 만들어 둔다 (마이그레이션은 멱등)."""
    try:
        with psycopg.connect(database_url, row_factory=dict_row) as conn:
            run_migrations(conn)
            conn.commit()
    except psycopg.OperationalError as exc:
        pytest.fail(
            "validation_db 에 연결할 수 없습니다. Docker 컨테이너를 먼저 기동하세요:\n"
            "  docker compose up -d test-db\n"
            "  docker exec local-postgres psql -U aimieapi -d postgres "
            '-c "CREATE DATABASE validation_db"\n'
            f"원인: {exc}",
            pytrace=False,
        )


@pytest.fixture
def conn(database_url: str) -> Iterator[psycopg.Connection]:
    """테스트용 커넥션. 끝나면 롤백해 DB 를 원상태로 되돌린다."""
    connection = psycopg.connect(database_url, row_factory=dict_row)
    try:
        yield connection
    finally:
        connection.rollback()
        connection.close()


@pytest.fixture
def doctor(conn: psycopg.Connection):
    """테스트 전용 전문의 계정 (매번 다른 ID)."""
    user_id = f"test_doctor_{uuid.uuid4().hex[:8]}"
    return users_repo.upsert_user(conn, user_id, "테스트 전문의", ROLE_DOCTOR, "pw1234")


@pytest.fixture
def admin(conn: psycopg.Connection):
    user_id = f"test_admin_{uuid.uuid4().hex[:8]}"
    return users_repo.upsert_user(conn, user_id, "테스트 관리자", ROLE_ADMIN, "pw1234")


@pytest.fixture
def chat_payload() -> dict:
    """SPEC.md §3 의 응답 예시를 확장한 4턴짜리 대화."""
    return {
        "success": True,
        "data": {
            "messages": [
                {"sender": "teacher", "text": "요즘 학원 생활은 어때?", "date": "26.08.31"},
                {"sender": "student", "text": "학원 숙제가 너무 많아요.", "date": "26.08.31"},
                {"sender": "teacher", "text": "많이 힘들었겠다.", "date": "26.08.31"},
                {"sender": "student", "text": "네 좀 지쳐요.", "date": "26.08.31"},
                {"sender": "teacher", "text": "친구들과는 어떠니?", "date": "26.08.31"},
                {"sender": "student", "text": "친구들은 괜찮아요.", "date": "26.08.31"},
                {"sender": "teacher", "text": "다행이네. 잠은 잘 자?", "date": "26.08.31"},
                {"sender": "student", "text": "요즘 잘 못 자요.", "date": "26.08.31"},
            ]
        },
    }


class FakeChatClient:
    """외부 API 를 대신하는 fake (네트워크 호출 없음)."""

    def __init__(self, payload: dict) -> None:
        from app.parsing import parse_chat_payload

        self._turns = parse_chat_payload(payload)
        self.calls: list[tuple[str, str | None, str | None]] = []

    def fetch_turns(self, student_id, *, date=None, session_id=None):
        self.calls.append((student_id, date, session_id))
        return list(self._turns)


@pytest.fixture
def fake_client(chat_payload: dict) -> FakeChatClient:
    return FakeChatClient(chat_payload)
