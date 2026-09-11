"""배포 전 점검.

이 프로젝트에서 실제로 막혔던 것들은 대부분 **조용히** 실패했다.
환경변수를 빠뜨려도 앱은 뜨고, 명부와 세션 DB 가 다른 환경이어도 화면은
정상으로 보이고, stage 매핑이 어긋나도 척도가 그냥 비어 있었다.

여기서 그것들을 미리, 크게 실패시킨다. 배포 파이프라인에서
`uv run python -m scripts.preflight` 로 게이트를 걸 수 있도록
치명적 문제가 있으면 0 이 아닌 종료 코드를 낸다.

검사 로직은 화면·출력과 분리된 순수 함수로 두어 테스트한다.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from typing import Literal

import psycopg
from psycopg.rows import dict_row

from app.config import (
    DEFAULT_DATABASE_URL,
    DEFAULT_SESSION_DB_URL,
    DEFAULT_STUDENT_DB_URL,
    Settings,
)

Status = Literal["ok", "warn", "fail"]

# 배포 시 반드시 바꿔야 하는 시드 비밀번호 (scripts/init_db.py 의 기본값)
_DEMO_PASSWORDS = {"admin1234", "doctor1234"}

_REQUIRED_TABLES = {"users", "evaluation_assignments", "doctor_evaluations"}


@dataclass(frozen=True)
class CheckResult:
    name: str
    status: Status
    detail: str

    @property
    def is_fatal(self) -> bool:
        return self.status == "fail"


def mask_dsn(dsn: str) -> str:
    """접속 문자열에서 비밀번호를 가린다. 로그·화면에 그대로 남기지 않는다."""
    if "://" not in dsn:
        return dsn
    scheme, rest = dsn.split("://", 1)
    if "@" not in rest:
        return dsn
    credentials, host = rest.rsplit("@", 1)
    user = credentials.split(":", 1)[0]
    return f"{scheme}://{user}:***@{host}"


# --- 순수 판정 함수 (DB 없이 테스트 가능) -----------------------------------


def check_defaults(settings: Settings) -> list[CheckResult]:
    """접속 문자열이 코드 기본값(localhost) 그대로인지.

    stg 에서 환경변수를 빠뜨리면 앱이 에러 없이 localhost 로 붙으러 간다.
    가장 자주 겪은 함정이라 가장 먼저 본다.
    """
    targets = [
        ("평가 DB", settings.database_url, DEFAULT_DATABASE_URL, "VALIDATION_DATABASE_URL"),
        ("학생 명부 DB", settings.student_db_url, DEFAULT_STUDENT_DB_URL, "STUDENT_SOURCE_DATABASE_URL"),
        ("세션 DB", settings.session_db_url, DEFAULT_SESSION_DB_URL, "SESSION_SOURCE_DATABASE_URL"),
    ]
    results = []
    for label, actual, default, env_name in targets:
        if actual == default:
            results.append(
                CheckResult(
                    f"{label} 주소",
                    "warn",
                    f"코드 기본값(localhost)을 쓰고 있다. 로컬이 아니라면 {env_name} 를 설정할 것 — {mask_dsn(actual)}",
                )
            )
        else:
            results.append(CheckResult(f"{label} 주소", "ok", mask_dsn(actual)))
    return results


def check_api_settings(settings: Settings) -> list[CheckResult]:
    """API 호스트·계정이 채워져 있는지 (호출은 하지 않는다)."""
    results = []
    if not settings.api_base_url.startswith("https://"):
        results.append(
            CheckResult("API 호스트", "warn", f"HTTPS 가 아니다: {settings.api_base_url}")
        )
    else:
        results.append(CheckResult("API 호스트", "ok", settings.api_base_url))

    if settings.api_token:
        results.append(CheckResult("API 인증", "ok", "EXTERNAL_API_TOKEN 사용"))
    elif settings.api_login_id and settings.api_password:
        results.append(
            CheckResult("API 인증", "ok", f"로그인 계정 사용 ({settings.api_login_id})")
        )
    else:
        results.append(
            CheckResult(
                "API 인증",
                "fail",
                "토큰도 로그인 계정도 없다. 대화 조회가 전부 401 로 실패한다.",
            )
        )
    return results


def check_seed_passwords(admin_pw: str, doctor_pw: str) -> CheckResult:
    """데모 비밀번호가 그대로인지."""
    leftover = _DEMO_PASSWORDS & {admin_pw, doctor_pw}
    if leftover:
        return CheckResult(
            "시드 비밀번호",
            "fail",
            "데모 비밀번호가 그대로다. SEED_ADMIN_PASSWORD / SEED_DOCTOR_PASSWORD 를 바꿀 것.",
        )
    return CheckResult("시드 비밀번호", "ok", "기본값에서 변경됨")


def check_stage_coverage(
    stage_counts: dict[str, int], settings: Settings
) -> CheckResult:
    """세션 DB 의 실제 stage 값이 척도 매핑에 들어 있는지.

    stage 이름은 환경마다 다를 수 있다. dev 에서 `early_depression` 을
    발견하기 전까지 2단계가 영영 판별되지 않았다.
    """
    # 척도가 아닌 진행 상태는 매핑하지 않는 것이 정상이다.
    not_a_scale = {"opening", "continue", "finish", ""}
    unmapped = {
        stage: n
        for stage, n in stage_counts.items()
        if stage and stage.lower() not in not_a_scale
        and settings.scale_for_stage(stage) is None
    }
    if not stage_counts:
        return CheckResult("stage 매핑", "warn", "세션에 stage 값이 하나도 없다.")
    if unmapped:
        listed = ", ".join(f"{s}({n}건)" for s, n in sorted(unmapped.items()))
        return CheckResult(
            "stage 매핑",
            "warn",
            f"매핑되지 않은 stage: {listed} — 이 세션들은 척도가 비어 전문의가 직접 골라야 한다. "
            "app/config.py 의 DEFAULT_STAGE_TO_SCALE 을 확인할 것.",
        )
    mapped = sum(n for s, n in stage_counts.items() if settings.scale_for_stage(s))
    return CheckResult("stage 매핑", "ok", f"척도 판별 가능 {mapped}건")


def check_roster_session_match(
    student_count: int, matched_count: int
) -> CheckResult:
    """명부 학생 중 척도검사가 있는 비율.

    명부와 세션 DB 가 서로 다른 환경 덤프면 교집합이 0 이 되고,
    학생은 보이는데 할당을 만들 수 없다.
    """
    if student_count == 0:
        return CheckResult("명부·세션 정합성", "fail", "학생 명부가 비어 있다.")
    if matched_count == 0:
        return CheckResult(
            "명부·세션 정합성",
            "fail",
            f"명부 {student_count}명 중 척도검사가 있는 학생이 0명이다. "
            "명부 DB 와 세션 DB 가 서로 다른 환경일 가능성이 높다.",
        )
    return CheckResult(
        "명부·세션 정합성",
        "ok",
        f"명부 {student_count}명 중 {matched_count}명에게 척도검사가 있다",
    )


# --- DB 를 실제로 만져 보는 검사 --------------------------------------------


def check_validation_db(settings: Settings) -> list[CheckResult]:
    """평가 DB 접속 + 스키마 존재 여부."""
    try:
        with psycopg.connect(settings.database_url, row_factory=dict_row) as conn:
            rows = conn.execute(
                "SELECT tablename FROM pg_tables WHERE schemaname = 'public'"
            ).fetchall()
    except psycopg.Error as exc:
        return [
            CheckResult(
                "평가 DB 연결",
                "fail",
                f"{mask_dsn(settings.database_url)} 에 붙지 못했다. "
                f"DB 를 먼저 만들었는지(CREATE DATABASE) 확인할 것.\n    원인: {exc}",
            )
        ]

    found = {r["tablename"] for r in rows}
    missing = _REQUIRED_TABLES - found
    if missing:
        return [
            CheckResult("평가 DB 연결", "ok", mask_dsn(settings.database_url)),
            CheckResult(
                "평가 DB 스키마",
                "fail",
                f"테이블이 없다: {', '.join(sorted(missing))} — "
                "uv run python -m scripts.init_db 를 실행할 것.",
            ),
        ]
    return [
        CheckResult("평가 DB 연결", "ok", mask_dsn(settings.database_url)),
        CheckResult("평가 DB 스키마", "ok", f"테이블 {len(_REQUIRED_TABLES)}개 확인"),
    ]


def check_readonly_source(label: str, dsn: str, probe_sql: str) -> list[CheckResult]:
    """읽기 전용 소스 DB 접속 + 쓰기가 실제로 막히는지."""
    results = []
    try:
        conn = psycopg.connect(dsn, row_factory=dict_row)
    except psycopg.Error as exc:
        return [
            CheckResult(f"{label} 연결", "fail", f"{mask_dsn(dsn)} 에 붙지 못했다.\n    원인: {exc}")
        ]

    try:
        conn.execute(probe_sql).fetchone()
        results.append(CheckResult(f"{label} 연결", "ok", mask_dsn(dsn)))
    except psycopg.Error as exc:
        results.append(
            CheckResult(
                f"{label} 연결",
                "fail",
                f"조회에 실패했다. 스키마가 다른지 확인할 것.\n    원인: {exc}",
            )
        )
        conn.close()
        return results

    # 계정 권한으로도 쓰기가 막히는지 확인한다 (앱의 read_only 와 별개의 방어선)
    conn.rollback()
    try:
        conn.read_only = True
        conn.execute("CREATE TEMP TABLE _preflight_probe (x int)")
        results.append(
            CheckResult(
                f"{label} 읽기전용",
                "warn",
                "읽기 전용 트랜잭션에서 쓰기가 거부되지 않았다.",
            )
        )
    except psycopg.errors.ReadOnlySqlTransaction:
        results.append(CheckResult(f"{label} 읽기전용", "ok", "쓰기가 DB 단계에서 거부됨"))
    except psycopg.Error as exc:
        results.append(CheckResult(f"{label} 읽기전용", "warn", f"확인 불가: {exc}"))
    finally:
        conn.rollback()
        conn.close()
    return results


def collect_stage_counts(settings: Settings) -> dict[str, int]:
    """세션 DB 의 stage 값 분포."""
    sql = """
        SELECT checkpoint_json -> 'channel_values' ->> 'stage' AS stage, COUNT(*) AS n
        FROM checkpoints GROUP BY 1
    """
    with psycopg.connect(settings.session_db_url, row_factory=dict_row) as conn:
        conn.read_only = True
        return {(r["stage"] or ""): r["n"] for r in conn.execute(sql).fetchall()}


def summarize(results: list[CheckResult]) -> tuple[int, int, int]:
    """(ok, warn, fail) 개수."""
    counts = Counter(r.status for r in results)
    return counts["ok"], counts["warn"], counts["fail"]
