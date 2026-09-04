"""evaluation_assignments 테이블 접근."""

from __future__ import annotations

import psycopg

from app.models import (
    STATUS_COMPLETED,
    STATUS_IN_PROGRESS,
    STATUS_PENDING,
    Assignment,
)

_COLUMNS = """
    a.id, a.doctor_id, a.student_id, a.session_id, a.chat_date,
    a.total_turns, a.completed_turns, a.status,
    a.created_at, a.updated_at, a.completed_at
"""


def _to_assignment(row: dict | None) -> Assignment | None:
    if row is None:
        return None
    return Assignment(
        id=row["id"],
        doctor_id=row["doctor_id"],
        student_id=row["student_id"],
        session_id=row["session_id"],
        chat_date=row["chat_date"],
        total_turns=row["total_turns"],
        completed_turns=row["completed_turns"] or 0,
        status=row["status"],
        created_at=row.get("created_at"),
        updated_at=row.get("updated_at"),
        completed_at=row.get("completed_at"),
        doctor_name=row.get("doctor_name"),
    )


def create_assignment(
    conn: psycopg.Connection,
    doctor_id: str,
    student_id: str,
    session_id: str,
    chat_date: str = "",
) -> Assignment | None:
    """할당 생성. 이미 같은 (전문의, 학생, 세션, 날짜) 할당이 있으면 None.

    관리자가 일괄 등록할 때 중복분만 조용히 건너뛰기 위한 설계다.
    """
    row = conn.execute(
        """
        INSERT INTO evaluation_assignments
            (doctor_id, student_id, session_id, chat_date, status)
        VALUES (%s, %s, %s, %s, %s)
        ON CONFLICT (doctor_id, student_id, session_id, chat_date) DO NOTHING
        RETURNING id, doctor_id, student_id, session_id, chat_date,
                  total_turns, completed_turns, status,
                  created_at, updated_at, completed_at
        """,
        (doctor_id, student_id, session_id, chat_date, STATUS_PENDING),
    ).fetchone()
    return _to_assignment(row)


def get_assignment(conn: psycopg.Connection, assignment_id: int) -> Assignment | None:
    row = conn.execute(
        f"""
        SELECT {_COLUMNS}, u.name AS doctor_name
        FROM evaluation_assignments a
        JOIN users u ON u.user_id = a.doctor_id
        WHERE a.id = %s
        """,  # noqa: S608 — _COLUMNS 는 상수, 사용자 입력이 아니다
        (assignment_id,),
    ).fetchone()
    return _to_assignment(row)


def list_assignments(
    conn: psycopg.Connection, *, doctor_id: str | None = None
) -> list[Assignment]:
    """할당 목록. doctor_id 를 주면 해당 전문의 것만."""
    sql = f"""
        SELECT {_COLUMNS}, u.name AS doctor_name
        FROM evaluation_assignments a
        JOIN users u ON u.user_id = a.doctor_id
    """  # noqa: S608
    params: tuple = ()
    if doctor_id is not None:
        sql += " WHERE a.doctor_id = %s"
        params = (doctor_id,)
    sql += " ORDER BY a.id"
    rows = conn.execute(sql, params).fetchall()
    return [a for a in (_to_assignment(r) for r in rows) if a is not None]


def update_total_turns(
    conn: psycopg.Connection, assignment_id: int, total_turns: int
) -> None:
    conn.execute(
        """
        UPDATE evaluation_assignments
        SET total_turns = %s, updated_at = NOW()
        WHERE id = %s AND total_turns IS DISTINCT FROM %s
        """,
        (total_turns, assignment_id, total_turns),
    )


def refresh_progress(conn: psycopg.Connection, assignment_id: int) -> Assignment | None:
    """완료 턴 수를 다시 세고 상태를 재계산한다.

    점수와 판단 이유가 모두 채워진 턴만 '완료'로 센다.
    이미 COMPLETED 인 건은 상태를 건드리지 않는다 (수정 잠금 유지).
    """
    conn.execute(
        """
        UPDATE evaluation_assignments a
        SET completed_turns = sub.done,
            status = CASE
                WHEN a.status = %s THEN %s
                WHEN sub.done = 0 THEN %s
                ELSE %s
            END,
            updated_at = NOW()
        FROM (
            SELECT COUNT(*) AS done
            FROM doctor_evaluations
            WHERE assignment_id = %s
              AND COALESCE(TRIM(doctor_score), '') <> ''
              AND COALESCE(TRIM(doctor_opinion), '') <> ''
        ) AS sub
        WHERE a.id = %s
        """,
        (
            STATUS_COMPLETED,
            STATUS_COMPLETED,
            STATUS_PENDING,
            STATUS_IN_PROGRESS,
            assignment_id,
            assignment_id,
        ),
    )
    return get_assignment(conn, assignment_id)


def mark_completed(conn: psycopg.Connection, assignment_id: int) -> Assignment | None:
    conn.execute(
        """
        UPDATE evaluation_assignments
        SET status = %s, completed_at = NOW(), updated_at = NOW()
        WHERE id = %s
        """,
        (STATUS_COMPLETED, assignment_id),
    )
    return get_assignment(conn, assignment_id)


def reopen(conn: psycopg.Connection, assignment_id: int) -> Assignment | None:
    """최종 완료된 건의 수정 잠금을 해제한다."""
    conn.execute(
        """
        UPDATE evaluation_assignments
        SET status = %s, completed_at = NULL, updated_at = NOW()
        WHERE id = %s
        """,
        (STATUS_IN_PROGRESS, assignment_id),
    )
    return refresh_progress(conn, assignment_id)


def delete_assignment(conn: psycopg.Connection, assignment_id: int) -> None:
    conn.execute("DELETE FROM evaluation_assignments WHERE id = %s", (assignment_id,))
