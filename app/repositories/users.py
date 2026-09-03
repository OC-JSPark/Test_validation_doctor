"""users 테이블 접근."""

from __future__ import annotations

import psycopg

from app.models import ROLE_DOCTOR, User
from app.security import hash_password


def _to_user(row: dict | None) -> User | None:
    if row is None:
        return None
    return User(user_id=row["user_id"], name=row["name"], role=row["role"])


def get_user(conn: psycopg.Connection, user_id: str) -> User | None:
    row = conn.execute(
        "SELECT user_id, name, role FROM users WHERE user_id = %s",
        (user_id,),
    ).fetchone()
    return _to_user(row)


def get_password_hash(conn: psycopg.Connection, user_id: str) -> str | None:
    row = conn.execute(
        "SELECT password_hash FROM users WHERE user_id = %s",
        (user_id,),
    ).fetchone()
    return row["password_hash"] if row else None


def list_users(conn: psycopg.Connection, role: str | None = None) -> list[User]:
    if role is None:
        rows = conn.execute(
            "SELECT user_id, name, role FROM users ORDER BY role, user_id"
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT user_id, name, role FROM users WHERE role = %s ORDER BY user_id",
            (role,),
        ).fetchall()
    return [u for u in (_to_user(r) for r in rows) if u is not None]


def list_doctors(conn: psycopg.Connection) -> list[User]:
    return list_users(conn, ROLE_DOCTOR)


def upsert_user(
    conn: psycopg.Connection, user_id: str, name: str, role: str, password: str
) -> User:
    """계정 생성/갱신. 비밀번호는 항상 해시해서 저장한다."""
    row = conn.execute(
        """
        INSERT INTO users (user_id, name, role, password_hash)
        VALUES (%s, %s, %s, %s)
        ON CONFLICT (user_id) DO UPDATE
            SET name = EXCLUDED.name,
                role = EXCLUDED.role,
                password_hash = EXCLUDED.password_hash
        RETURNING user_id, name, role
        """,
        (user_id, name, role, hash_password(password)),
    ).fetchone()
    user = _to_user(row)
    assert user is not None  # RETURNING 은 항상 한 행을 준다
    return user
