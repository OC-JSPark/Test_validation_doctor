"""로컬/서버 validation_db 초기화.

    uv run python -m scripts.init_db                      # 스키마만
    uv run python -m scripts.init_db --seed               # + 계정 (비밀번호 자동 발급)
    uv run python -m scripts.init_db --seed --prompt      # + 계정 (비밀번호 직접 입력)
    uv run python -m scripts.init_db --seed --force       # 기존 계정 비밀번호 재설정

데이터베이스 자체는 미리 만들어져 있어야 한다:
    psql -h <host> -U <admin> -c "CREATE DATABASE validation_db"

## 비밀번호를 어디에 두나

**어디에도 저장하지 않는다.** 코드에 넣으면 git 에 영구히 남고 저장소 접근자
전원이 보게 된다. `.env` 도 서버에 평문으로 남는다.

그래서 기본 동작은 **계정마다 난수로 발급하고 화면에 한 번만 출력**하는 것이다.
운영자가 그때 받아 적어 각 담당자에게 개별 전달한다.
다시 볼 수 없고, 잊었으면 `--force` 로 재발급한다.

**계정마다 다른 비밀번호를 준다.** 여러 전문의가 같은 값을 쓰면 한 명이 유출되어도
누구 계정인지 추적할 수 없고, 평가 데이터에 담당 전문의가 기록되는 시스템이라
계정 분리가 의미를 잃는다.

| 상황 | 방법 | 계정별 |
| --- | --- | --- |
| 운영/dev 서버 | 인자 없이 `--seed` → 난수 발급, 1회 출력 (권장) | **다름** |
| 운영자가 직접 정하고 싶을 때 | `--seed --prompt` → 계정마다 터미널 입력 | **다름** |
| 로컬 개발 | `SEED_ADMIN_PASSWORD` / `SEED_DOCTOR_PASSWORD` 환경변수 | 같음 |

환경변수는 **로컬 편의용**이다. 서버에서는 쓰지 않는 것을 권한다 —
설정하면 해당 역할의 계정이 전부 같은 비밀번호를 받는다.
"""

from __future__ import annotations

import argparse
import getpass
import os
import secrets
import string
import sys

from app.config import get_settings
from app.db import connection, run_migrations
from app.models import ROLE_ADMIN, ROLE_DOCTOR
from app.repositories import users as users_repo

SEED_DOCTOR_COUNT = int(os.getenv("SEED_DOCTOR_COUNT", "3"))

# 사람이 받아 적기 쉽도록 헷갈리는 글자(0/O, 1/l/I)는 뺀다.
_ALPHABET = (
    "".join(c for c in string.ascii_letters if c not in "lIO")
    + "".join(c for c in string.digits if c not in "01")
    + "!@#$%^&*-_=+"
)
_GENERATED_LENGTH = 20


def generate_password(length: int = _GENERATED_LENGTH) -> str:
    """암호학적 난수로 비밀번호를 만든다."""
    return "".join(secrets.choice(_ALPHABET) for _ in range(length))


def resolve_password(role_label: str, env_name: str, *, prompt: bool) -> tuple[str, str]:
    """비밀번호와 그 출처를 정한다.

    우선순위: --prompt 입력 > 환경변수 > 난수 발급.
    돌려주는 두 번째 값은 화면에 어떻게 안내할지 정하는 출처 표시다.
    """
    if prompt:
        while True:
            first = getpass.getpass(f"{role_label} 비밀번호: ")
            if len(first) < 8:
                print("  8자 이상이어야 합니다.", file=sys.stderr)
                continue
            if first != getpass.getpass(f"{role_label} 비밀번호 확인: "):
                print("  일치하지 않습니다. 다시 입력하세요.", file=sys.stderr)
                continue
            return first, "prompt"

    from_env = os.getenv(env_name)
    if from_env:
        return from_env, "env"

    return generate_password(), "generated"


def seed_users(
    conn,
    *,
    force: bool = False,
    prompt: bool = False,
    doctor_count: int = SEED_DOCTOR_COUNT,
) -> tuple[list[str], dict[str, tuple[str, str]]]:
    """계정 생성. 이미 있으면 `force` 없이는 건드리지 않는다.

    **비밀번호는 계정마다 따로 발급한다.** 여러 전문의가 같은 비밀번호를 쓰면
    한 명이 유출되어도 누구 계정인지 추적할 수 없고, 평가 데이터에 담당 전문의가
    기록되는 시스템이라 계정 분리가 의미를 잃는다.

    (만들어진 계정 목록, {계정ID: (비밀번호, 출처)}) 를 돌려준다.
    비밀번호는 호출부가 한 번 출력하고 버린다 — 어디에도 저장하지 않는다.
    """
    targets: list[tuple[str, str, str, str]] = [
        ("admin", "관리자", ROLE_ADMIN, "관리자"),
    ]
    for i in range(1, doctor_count + 1):
        targets.append((f"doctor{i:02d}", f"전문의 {i:02d}", ROLE_DOCTOR, "전문의"))

    # 실제로 만들 계정이 있을 때만 비밀번호를 정한다 (불필요한 프롬프트 방지).
    pending = [
        t for t in targets if force or users_repo.get_user(conn, t[0]) is None
    ]
    if not pending:
        return [], {}

    secrets_by_user: dict[str, tuple[str, str]] = {}
    created: list[str] = []
    for user_id, name, role, role_label in pending:
        env_name = (
            "SEED_ADMIN_PASSWORD" if role == ROLE_ADMIN else "SEED_DOCTOR_PASSWORD"
        )
        # 계정마다 호출한다 — 난수는 매번 다른 값이 나온다.
        # (환경변수를 설정한 경우에만 같은 값이 공유된다. 로컬 개발용.)
        password, source = resolve_password(
            f"{role_label} [{user_id}]", env_name, prompt=prompt
        )
        users_repo.upsert_user(conn, user_id, name, role, password)
        secrets_by_user[user_id] = (password, source)
        created.append(user_id)

    return created, secrets_by_user


def _announce(secrets_by_user: dict[str, tuple[str, str]]) -> None:
    """발급된 비밀번호를 한 번만 출력한다. 계정마다 다른 값이다."""
    generated = {u: p for u, (p, src) in secrets_by_user.items() if src == "generated"}
    if not generated:
        sources = {src for _, src in secrets_by_user.values()}
        if "env" in sources:
            print("  비밀번호: 환경변수 값을 사용했습니다 (로컬 개발용, 계정 공통).")
        if "prompt" in sources:
            print("  비밀번호: 입력하신 값으로 설정했습니다.")
        return

    width = 64
    print()
    print("=" * width)
    print("  발급된 비밀번호 — 지금 받아 적으세요. 다시 볼 수 없습니다.")
    print("  계정마다 다른 값입니다. 각 담당자에게 개별 전달하세요.")
    print("=" * width)
    print(f"  {'계정':<12} {'비밀번호'}")
    print("  " + "-" * (width - 4))
    for user_id, password in generated.items():
        print(f"  {user_id:<12} {password}")
    print("=" * width)
    print("  분실하면 --seed --force 로 재발급해야 합니다.")
    print()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="validation_db 초기화")
    parser.add_argument("--seed", action="store_true", help="계정 생성")
    parser.add_argument(
        "--force", action="store_true", help="기존 계정 비밀번호를 재설정한다"
    )
    parser.add_argument(
        "--prompt", action="store_true", help="비밀번호를 직접 입력한다 (화면에 안 찍힘)"
    )
    args = parser.parse_args(argv)

    settings = get_settings()
    # 접속 문자열에 비밀번호가 들어 있으므로 전체를 출력하지 않는다.
    target = settings.database_url.rsplit("@", 1)[-1]
    print(f"대상 DB: {target}")

    with connection() as conn:
        applied = run_migrations(conn)
        print(f"마이그레이션 적용: {', '.join(applied) or '(없음)'}")

        if args.seed:
            created, secrets_by_role = seed_users(
                conn, force=args.force, prompt=args.prompt
            )
            if created:
                print(f"계정 생성/갱신: {', '.join(created)}")
                _announce(secrets_by_role)
            else:
                print("계정 생성 없음 (이미 존재 — 재설정하려면 --force)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
