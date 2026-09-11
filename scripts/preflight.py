"""배포 전 점검 CLI.

    uv run python -m scripts.preflight           # 전체 점검
    uv run python -m scripts.preflight --no-api  # 외부 API 호출 없이

종료 코드: 0 = 통과(경고는 있을 수 있음), 1 = 치명적 문제.
배포 파이프라인에서 게이트로 쓸 수 있다.
"""

from __future__ import annotations

import argparse
import sys

from app.config import get_settings
from app.external_api import ChatAPIClient, ExternalAPIError
from app.preflight import (
    CheckResult,
    check_api_settings,
    check_defaults,
    check_roster_session_match,
    check_seed_passwords,
    check_stage_coverage,
    check_validation_db,
    check_readonly_source,
    collect_stage_counts,
    summarize,
)

_MARK = {"ok": "✅", "warn": "⚠️ ", "fail": "❌"}


def _print(result: CheckResult) -> None:
    print(f"  {_MARK[result.status]} {result.name:16} {result.detail}")


def _section(title: str) -> None:
    print(f"\n[{title}]")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="배포 전 점검")
    parser.add_argument("--no-api", action="store_true", help="외부 API 호출 생략")
    parser.add_argument("--student", help="대화 조회까지 확인할 studentId")
    args = parser.parse_args(argv)

    settings = get_settings()
    results: list[CheckResult] = []

    _section("1. 설정")
    for r in check_defaults(settings) + check_api_settings(settings):
        _print(r)
        results.append(r)

    import os

    r = check_seed_passwords(
        os.getenv("SEED_ADMIN_PASSWORD", "admin1234"),
        os.getenv("SEED_DOCTOR_PASSWORD", "doctor1234"),
    )
    _print(r)
    results.append(r)

    _section("2. 평가 DB (읽기/쓰기)")
    for r in check_validation_db(settings):
        _print(r)
        results.append(r)

    _section("3. 읽기 전용 소스")
    for r in check_readonly_source(
        "학생 명부", settings.student_db_url, "SELECT 1 FROM t_user LIMIT 1"
    ):
        _print(r)
        results.append(r)
    for r in check_readonly_source(
        "세션 DB", settings.session_db_url, "SELECT 1 FROM sessions LIMIT 1"
    ):
        _print(r)
        results.append(r)

    _section("4. 데이터 정합성")
    try:
        from app import session_directory, student_directory

        students = student_directory.list_students()
        sessions = session_directory.list_sessions([s.student_id for s in students])
        matched = len(session_directory.group_by_student(sessions))
        r = check_roster_session_match(len(students), matched)
        _print(r)
        results.append(r)

        r = check_stage_coverage(collect_stage_counts(settings), settings)
        _print(r)
        results.append(r)
    except Exception as exc:  # 연결 실패는 위에서 이미 잡혔다
        r = CheckResult("정합성 확인", "fail", f"{type(exc).__name__}: {exc}")
        _print(r)
        results.append(r)

    if not args.no_api:
        _section("5. 외부 API")
        client = ChatAPIClient()
        try:
            if client.ensure_token():
                r = CheckResult("API 로그인", "ok", "accessToken 획득")
            else:
                r = CheckResult("API 로그인", "fail", "인증 수단이 없다")
        except ExternalAPIError as exc:
            r = CheckResult("API 로그인", "fail", str(exc))
        _print(r)
        results.append(r)

        if args.student and r.status == "ok":
            try:
                turns = client.fetch_turns(args.student)
                filled = sum(1 for t in turns if t.ai_question.strip())
                status = "ok" if turns and filled else "warn"
                r2 = CheckResult(
                    "대화 조회", status, f"{len(turns)}턴 (AI질문 있는 턴 {filled}개)"
                )
            except ExternalAPIError as exc:
                r2 = CheckResult("대화 조회", "fail", str(exc))
            _print(r2)
            results.append(r2)

    ok, warn, fail = summarize(results)
    print(f"\n{'─' * 60}")
    print(f"통과 {ok} · 경고 {warn} · 실패 {fail}")
    if fail:
        print("\n❌ 치명적 문제가 있다. 위 항목을 해결한 뒤 배포할 것.")
        return 1
    if warn:
        print("\n⚠️  경고가 있다. 의도한 것인지 확인할 것.")
    else:
        print("\n✅ 배포 가능.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
