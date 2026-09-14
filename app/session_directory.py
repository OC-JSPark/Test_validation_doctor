"""척도검사(세션) 목록 조회 (읽기 전용).

관리자가 학생을 고르면, **그 학생이 실시한 척도검사 전체**를 할당 대상으로 삼는다.
그 목록을 여기서 가져온다.

외부 대화 API 에는 세션 목록을 주는 엔드포인트가 없다
(`/chat` 은 날짜 하나의 대화만 돌려주고, 날짜를 생략하면 최신 하루치만 준다).
그래서 세션 목록만 DB 에서 읽는다.

- 지금은 로컬에 복원된 `aimie_kids_ai.sessions` 를 본다.
- 추후 인스턴스 DB 로 옮길 때는 `SESSION_SOURCE_DATABASE_URL` 만 바꾸면 된다.

**안전장치**: 커넥션은 `read_only = True` 로 연다 (`student_directory` 와 동일).
"""

from __future__ import annotations

import psycopg
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

from app.config import get_settings
from app.models import ScaleSession

# 스키마가 다른 DB 로 옮길 경우 여기만 고치면 된다.
#
# checkpoints.checkpoint_json 의 channel_values.stage 에 그 세션이 진행한
# 척도가 남는다(stress / depression / severe …). 세션과 1:1 이라 LEFT JOIN 으로
# 붙여서, 관리자가 척도를 손으로 고르지 않아도 되게 한다.
_LIST_SQL = """
    SELECT s.user_id    AS student_id,
           s.session_id AS session_id,
           s.date       AS session_date,
           c.checkpoint_json -> 'channel_values' ->> 'stage' AS stage
    FROM sessions s
    LEFT JOIN checkpoints c
           ON c.user_id = s.user_id
          AND c.date = s.date
          AND c.session_id = s.session_id
    WHERE s.user_id = ANY(%s)
    ORDER BY s.user_id, s.date, s.session_id
"""

_pool: ConnectionPool | None = None


class SessionDirectoryError(RuntimeError):
    """척도검사 목록 조회 실패."""


def _configure_readonly(conn: psycopg.Connection) -> None:
    conn.read_only = True


def get_pool() -> ConnectionPool:
    global _pool
    if _pool is None:
        _pool = ConnectionPool(
            get_settings().session_db_url,
            min_size=1,
            max_size=3,
            open=True,
            configure=_configure_readonly,
            kwargs={"row_factory": dict_row},
        )
    return _pool


def close_pool() -> None:
    global _pool
    if _pool is not None:
        _pool.close()
        _pool = None


def list_sessions(
    student_ids: list[str], conn: psycopg.Connection | None = None
) -> list[ScaleSession]:
    """여러 학생의 척도검사를 한 번에 조회한다 (학생당 쿼리를 돌리지 않는다)."""
    ids = [s for s in dict.fromkeys(student_ids) if s]
    if not ids:
        return []

    try:
        if conn is not None:
            rows = conn.execute(_LIST_SQL, (ids,)).fetchall()
        else:
            with get_pool().connection() as pooled:
                rows = pooled.execute(_LIST_SQL, (ids,)).fetchall()
    except psycopg.Error as exc:
        raise SessionDirectoryError(
            "척도검사 목록 조회에 실패했습니다. "
            "SESSION_SOURCE_DATABASE_URL 과 DB 기동 상태를 확인하세요.\n"
            f"원인: {exc}"
        ) from exc

    settings = get_settings()
    return [
        ScaleSession(
            student_id=row["student_id"],
            session_id=row["session_id"],
            session_date=row["session_date"],
            stage=row.get("stage"),
            scale_stage=settings.scale_for_stage(row.get("stage")),
        )
        for row in rows
    ]


def group_by_student(sessions: list[ScaleSession]) -> dict[str, list[ScaleSession]]:
    """학생별로 묶는다 (순수 함수)."""
    grouped: dict[str, list[ScaleSession]] = {}
    for session in sessions:
        grouped.setdefault(session.student_id, []).append(session)
    return grouped
