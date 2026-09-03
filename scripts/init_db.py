"""로컬 validation_db 초기화.

    uv run python -m scripts.init_db                 # 스키마만 생성
    uv run python -m scripts.init_db --seed          # 데모 계정까지 생성
    uv run python -m scripts.init_db --seed --force  # 기존 계정 비밀번호 덮어쓰기

데이터베이스 자체는 미리 만들어져 있어야 한다:
    docker exec local-postgres psql -U aimieapi -d postgres -c "CREATE DATABASE validation_db"
"""

from __future__ import annotations

import argparse
import os
import sys

from app.config import get_settings
from app.db import connection, run_migrations
from app.models import ROLE_ADMIN, ROLE_DOCTOR
from app.repositories import users as users_repo

# 데모 계정. 비밀번호는 환경변수로 덮어쓸 수 있고, 운영에서는 반드시 바꿔야 한다.
SEED_ADMIN_PASSWORD = os.getenv("SEED_ADMIN_PASSWORD", "admin1234")
SEED_DOCTOR_PASSWORD = os.getenv("SEED_DOCTOR_PASSWORD", "doctor1234")
SEED_DOCTOR_COUNT = int(os.getenv("SEED_DOCTOR_COUNT", "3"))


def seed_users(conn, *, force: bool = False) -> list[str]:
    """데모 계정 생성. 이미 있으면 --force 없이는 건드리지 않는다."""
    created: list[str] = []

    def _ensure(user_id: str, name: str, role: str, password: str) -> None:
        if not force and users_repo.get_user(conn, user_id) is not None:
            return
        users_repo.upsert_user(conn, user_id, name, role, password)
        created.append(user_id)

    _ensure("admin", "관리자", ROLE_ADMIN, SEED_ADMIN_PASSWORD)
    for i in range(1, SEED_DOCTOR_COUNT + 1):
        _ensure(f"doctor{i:02d}", f"전문의 {i:02d}", ROLE_DOCTOR, SEED_DOCTOR_PASSWORD)
    return created


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="validation_db 초기화")
    parser.add_argument("--seed", action="store_true", help="데모 계정 생성")
    parser.add_argument("--force", action="store_true", help="기존 계정 덮어쓰기")
    args = parser.parse_args(argv)

    settings = get_settings()
    # 접속 문자열에 비밀번호가 들어 있으므로 전체를 출력하지 않는다.
    target = settings.database_url.rsplit("@", 1)[-1]
    print(f"대상 DB: {target}")

    with connection() as conn:
        applied = run_migrations(conn)
        print(f"마이그레이션 적용: {', '.join(applied) or '(없음)'}")
        if args.seed:
            created = seed_users(conn, force=args.force)
            if created:
                print(f"계정 생성/갱신: {', '.join(created)}")
            else:
                print("계정 생성 없음 (이미 존재 — 덮어쓰려면 --force)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
