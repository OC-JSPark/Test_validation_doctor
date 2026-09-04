"""척도검사(세션) 목록 조회."""

from __future__ import annotations

from datetime import date

import psycopg
import pytest
from psycopg.rows import dict_row

from app.config import get_settings
from app.models import ScaleSession
from app.session_directory import (
    SessionDirectoryError,
    group_by_student,
    list_sessions,
)


# --- 순수 로직 -------------------------------------------------------------


def test_검사일이_외부API_날짜형식으로_바뀐다():
    assert ScaleSession("s1", "a", date(2026, 8, 6)).chat_date == "26.08.06"
    assert ScaleSession("s1", "a", date(2026, 12, 31)).chat_date == "26.12.31"


def test_학생별로_묶인다():
    sessions = [
        ScaleSession("s1", "a", date(2026, 8, 6)),
        ScaleSession("s2", "b", date(2026, 8, 7)),
        ScaleSession("s1", "c", date(2026, 8, 8)),
    ]

    grouped = group_by_student(sessions)

    assert set(grouped) == {"s1", "s2"}
    assert [s.session_id for s in grouped["s1"]] == ["a", "c"]


def test_빈_목록은_빈_딕셔너리():
    assert group_by_student([]) == {}


# --- 실제 세션 DB (aimie_kids_ai) -------------------------------------------


@pytest.fixture
def session_conn():
    url = get_settings().session_db_url
    try:
        conn = psycopg.connect(url, row_factory=dict_row)
    except psycopg.OperationalError as exc:
        pytest.fail(
            "척도검사 DB(aimie_kids_ai) 에 연결할 수 없습니다. "
            "docker compose up -d test-db 로 컨테이너를 기동하세요.\n"
            f"원인: {exc}",
            pytrace=False,
        )
    conn.read_only = True
    try:
        yield conn
    finally:
        conn.rollback()
        conn.close()


@pytest.fixture
def sample_student_id(session_conn) -> str:
    """검사 이력이 가장 많은 학생 하나."""
    row = session_conn.execute(
        "SELECT user_id FROM sessions GROUP BY user_id ORDER BY COUNT(*) DESC LIMIT 1"
    ).fetchone()
    if row is None:
        pytest.fail("sessions 테이블이 비어 있습니다.", pytrace=False)
    return row["user_id"]


@pytest.mark.db
def test_학생의_척도검사를_전부_가져온다(session_conn, sample_student_id):
    sessions = list_sessions([sample_student_id], session_conn)

    assert sessions, "검사 이력이 조회되지 않았습니다."
    assert all(s.student_id == sample_student_id for s in sessions)
    assert all(isinstance(s.session_date, date) for s in sessions)
    # 날짜 오름차순
    assert [s.session_date for s in sessions] == sorted(s.session_date for s in sessions)


@pytest.mark.db
def test_여러_학생을_한_번에_조회한다(session_conn):
    rows = session_conn.execute(
        "SELECT DISTINCT user_id FROM sessions LIMIT 2"
    ).fetchall()
    ids = [r["user_id"] for r in rows]

    sessions = list_sessions(ids, session_conn)

    assert {s.student_id for s in sessions} <= set(ids)
    assert len(group_by_student(sessions)) == len(ids)


@pytest.mark.db
def test_검사_이력이_없는_학생은_결과에_없다(session_conn):
    sessions = list_sessions(["존재하지-않는-학생-id"], session_conn)
    assert sessions == []


@pytest.mark.db
def test_빈_입력은_쿼리하지_않고_빈_목록(session_conn):
    assert list_sessions([], session_conn) == []
    assert list_sessions(["", None], session_conn) == []  # type: ignore[list-item]


@pytest.mark.db
def test_중복_학생ID는_한_번만_조회한다(session_conn, sample_student_id):
    once = list_sessions([sample_student_id], session_conn)
    twice = list_sessions([sample_student_id, sample_student_id], session_conn)

    assert len(once) == len(twice)


@pytest.mark.db
def test_읽기전용_커넥션은_쓰기를_거부한다(session_conn):
    with pytest.raises(psycopg.errors.ReadOnlySqlTransaction):
        session_conn.execute("DELETE FROM sessions WHERE false")


@pytest.mark.db
def test_조회_실패는_SessionDirectoryError_로_감싼다():
    broken = psycopg.connect(get_settings().session_db_url, row_factory=dict_row)
    broken.close()

    with pytest.raises(SessionDirectoryError, match="SESSION_SOURCE_DATABASE_URL"):
        list_sessions(["any"], broken)
