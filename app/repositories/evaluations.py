"""doctor_evaluations 테이블 접근 (턴 단위 Upsert)."""

from __future__ import annotations

import psycopg

from app.models import Evaluation, QATurn, build_evaluation_code

_COLUMNS = """
    id, assignment_id, evaluation_code, turn_index, scale_stage,
    ai_question, user_answer, doctor_score, doctor_opinion, updated_at
"""


def _to_evaluation(row: dict | None) -> Evaluation | None:
    if row is None:
        return None
    return Evaluation(
        id=row["id"],
        assignment_id=row["assignment_id"],
        evaluation_code=row["evaluation_code"],
        turn_index=row["turn_index"],
        scale_stage=row["scale_stage"],
        ai_question=row["ai_question"],
        user_answer=row["user_answer"],
        doctor_score=row["doctor_score"],
        doctor_opinion=row["doctor_opinion"],
        updated_at=row.get("updated_at"),
    )


def list_evaluations(conn: psycopg.Connection, assignment_id: int) -> list[Evaluation]:
    rows = conn.execute(
        f"SELECT {_COLUMNS} FROM doctor_evaluations "  # noqa: S608 — 상수 컬럼 목록
        "WHERE assignment_id = %s ORDER BY turn_index",
        (assignment_id,),
    ).fetchall()
    return [e for e in (_to_evaluation(r) for r in rows) if e is not None]


def get_evaluation(
    conn: psycopg.Connection, assignment_id: int, turn_index: int
) -> Evaluation | None:
    row = conn.execute(
        f"SELECT {_COLUMNS} FROM doctor_evaluations "  # noqa: S608
        "WHERE assignment_id = %s AND turn_index = %s",
        (assignment_id, turn_index),
    ).fetchone()
    return _to_evaluation(row)


def sync_turns(
    conn: psycopg.Connection, assignment_id: int, turns: list[QATurn]
) -> None:
    """외부 API 에서 파싱한 Q&A 원본을 턴 행에 반영한다.

    - 없는 턴은 새로 만든다.
    - 이미 있는 턴은 질문/답변만 최신화하고, **전문의가 입력한 점수·의견은 건드리지 않는다.**
    """
    for turn in turns:
        conn.execute(
            """
            INSERT INTO doctor_evaluations
                (assignment_id, turn_index, evaluation_code, ai_question, user_answer)
            VALUES (%s, %s, %s, %s, %s)
            ON CONFLICT (assignment_id, turn_index) DO UPDATE
                SET ai_question = EXCLUDED.ai_question,
                    user_answer = EXCLUDED.user_answer,
                    evaluation_code = COALESCE(
                        doctor_evaluations.evaluation_code, EXCLUDED.evaluation_code
                    ),
                    updated_at = NOW()
            """,
            (
                assignment_id,
                turn.turn_index,
                build_evaluation_code(assignment_id, turn.turn_index),
                turn.ai_question,
                turn.user_answer,
            ),
        )


def save_evaluation(
    conn: psycopg.Connection,
    assignment_id: int,
    turn_index: int,
    *,
    doctor_score: str | None,
    doctor_opinion: str | None,
    scale_stage: str | None = None,
) -> Evaluation | None:
    """전문의 입력 저장 (Upsert). 원본 Q&A 는 덮어쓰지 않는다."""
    row = conn.execute(
        f"""
        INSERT INTO doctor_evaluations
            (assignment_id, turn_index, evaluation_code,
             scale_stage, doctor_score, doctor_opinion)
        VALUES (%s, %s, %s, %s, %s, %s)
        ON CONFLICT (assignment_id, turn_index) DO UPDATE
            SET scale_stage = EXCLUDED.scale_stage,
                doctor_score = EXCLUDED.doctor_score,
                doctor_opinion = EXCLUDED.doctor_opinion,
                updated_at = NOW()
        RETURNING {_COLUMNS}
        """,  # noqa: S608
        (
            assignment_id,
            turn_index,
            build_evaluation_code(assignment_id, turn_index),
            scale_stage,
            doctor_score,
            doctor_opinion,
        ),
    ).fetchone()
    return _to_evaluation(row)


def list_completed_rows(conn: psycopg.Connection) -> list[dict]:
    """CSV 추출용: COMPLETED 상태 할당의 모든 턴을 담당 전문의·완료일시와 함께."""
    return conn.execute(
        """
        SELECT e.evaluation_code,
               e.scale_stage,
               e.ai_question,
               e.user_answer,
               e.doctor_score,
               e.doctor_opinion,
               u.name AS doctor_name,
               a.completed_at
        FROM doctor_evaluations e
        JOIN evaluation_assignments a ON a.id = e.assignment_id
        JOIN users u ON u.user_id = a.doctor_id
        WHERE a.status = 'COMPLETED'
        ORDER BY a.id, e.turn_index
        """
    ).fetchall()
