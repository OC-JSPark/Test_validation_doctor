"""학생 명부 조회 (외부 학생 DB, **읽기 전용**).

관리자가 작업을 할당할 때 고를 학생 목록을 가져온다.

- 지금은 로컬에 복원된 `aimie_kids_app` 을 본다.
- 추후 인스턴스 DB 로 옮길 때는 `STUDENT_SOURCE_DATABASE_URL` 만 바꾸면 되고,
  이 모듈 밖은 손댈 필요가 없다.

**안전장치**: 이 커넥션은 `read_only = True` 로 열린다. 실수로 INSERT/UPDATE 가
섞여 들어가도 PostgreSQL 이 트랜잭션 단계에서 거부한다. 이 시스템이 만드는
데이터는 여전히 `validation_db` 에만 저장된다.
"""

from __future__ import annotations

import psycopg
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

from app.config import get_settings
from app.models import Student

# 학생 명부 쿼리. 스키마가 다른 DB 로 옮길 경우 여기만 고치면 된다.
_LIST_SQL = """
    SELECT u.user_uuid   AS student_id,
           s.nickname    AS nickname,
           s.school_name AS school_name,
           s.grade       AS grade,
           s.class_name  AS class_name
    FROM t_user u
    JOIN t_student s ON s.user_seq = u.user_seq
    WHERE u.is_deleted = 0
    ORDER BY s.school_name NULLS LAST, s.grade, s.class_name, s.nickname
"""

_pool: ConnectionPool | None = None


class StudentDirectoryError(RuntimeError):
    """학생 명부 DB 조회 실패."""


def _configure_readonly(conn: psycopg.Connection) -> None:
    conn.read_only = True


def get_pool() -> ConnectionPool:
    global _pool
    if _pool is None:
        settings = get_settings()
        _pool = ConnectionPool(
            settings.student_db_url,
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


def _to_student(row: dict) -> Student:
    return Student(
        student_id=(row["student_id"] or "").strip(),
        nickname=row.get("nickname"),
        school_name=row.get("school_name"),
        grade=row.get("grade"),
        class_name=row.get("class_name"),
    )


def list_students(conn: psycopg.Connection | None = None) -> list[Student]:
    """전체 학생 목록. `conn` 을 주면 그 커넥션으로 조회한다(테스트용)."""
    try:
        if conn is not None:
            rows = conn.execute(_LIST_SQL).fetchall()
        else:
            with get_pool().connection() as pooled:
                rows = pooled.execute(_LIST_SQL).fetchall()
    except psycopg.Error as exc:
        raise StudentDirectoryError(
            "학생 명부 DB 조회에 실패했습니다. "
            "STUDENT_SOURCE_DATABASE_URL 과 DB 기동 상태를 확인하세요.\n"
            f"원인: {exc}"
        ) from exc

    return [s for s in (_to_student(r) for r in rows) if s.student_id]


def filter_students(students: list[Student], query: str) -> list[Student]:
    """이름/학교/ID 기준 검색 (순수 함수)."""
    return [s for s in students if s.matches(query)]
