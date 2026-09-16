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

    # 완료 필터와 무관하게 '한 번에 조회' 되는지가 관심사다
    sessions = list_sessions(ids, session_conn, completed_only=False)

    assert {s.student_id for s in sessions} <= set(ids)
    assert len(group_by_student(sessions)) == len(ids)


@pytest.mark.db
def test_기본은_완료된_검사만_돌려준다(session_conn):
    """중간에 이탈한 세션은 평가할 대화가 부족하거나 아예 없다.

    특히 답변 0건인 세션을 할당하면 평가 대상 턴이 0개라
    전문의가 [최종 완료] 를 영영 누를 수 없다.
    """
    rows = session_conn.execute(
        "SELECT DISTINCT user_id FROM sessions LIMIT 5"
    ).fetchall()
    ids = [r["user_id"] for r in rows]

    completed = list_sessions(ids, session_conn)
    everything = list_sessions(ids, session_conn, completed_only=False)

    assert len(completed) <= len(everything)
    assert all(s.is_completed for s in completed)


@pytest.mark.db
def test_완료된_세션은_평가할_대화가_반드시_있다(session_conn):
    """완료 판정(분석 레포트 존재)이 실제로 대화량을 보장하는지 확인한다."""
    rows = session_conn.execute(
        """
        SELECT s.user_id, s.session_id,
               COUNT(m.*) FILTER (WHERE m.role = 'user') AS answers
        FROM sessions s
        JOIN report r
          ON r.user_id = s.user_id AND r.date = s.date AND r.session_id = s.session_id
        LEFT JOIN chat_messages_vector m ON m.session_id = s.session_id
        GROUP BY 1, 2
        HAVING COUNT(m.*) FILTER (WHERE m.role = 'user') = 0
        LIMIT 1
        """
    ).fetchone()

    assert rows is None, "레포트가 있는데 학생 답변이 0건인 세션이 있다"


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
