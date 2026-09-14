"""인증 서비스."""

from __future__ import annotations

import psycopg

from app.models import User
from app.repositories import users as users_repo
from app.security import verify_password


def login(conn: psycopg.Connection, user_id: str, password: str) -> User | None:
    """POST /api/auth/login — 성공 시 User, 실패 시 None.

    존재하지 않는 계정과 비밀번호 불일치를 구분해서 알려주지 않는다.
    """
    stored = users_repo.get_password_hash(conn, user_id)
    if not verify_password(password, stored):
        return None
    return users_repo.get_user(conn, user_id)
