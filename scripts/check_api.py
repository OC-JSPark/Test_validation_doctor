"""외부 API 연동 점검.

로그인 → accessToken 확보 → 대화 조회 → Q&A 턴 파싱까지 한 번에 확인한다.

    uv run python -m scripts.check_api --student <studentId> --date 26.08.31
    uv run python -m scripts.check_api --student <studentId> --session <sessionId>
    uv run python -m scripts.check_api --login-only

계정/토큰은 `.env` 에서 읽는다 (EXTERNAL_API_LOGIN_ID / EXTERNAL_API_PASSWORD
또는 EXTERNAL_API_TOKEN). 인자로 넘기면 그쪽이 우선한다.
"""

from __future__ import annotations

import argparse
import sys

from app.config import get_settings
from app.external_api import ChatAPIClient, ExternalAPIError
from app.parsing import parse_chat_payload


def _mask(token: str) -> str:
    """토큰 전체를 출력하지 않는다."""
    return f"{token[:8]}…{token[-4:]} (길이 {len(token)})" if len(token) > 16 else "(짧은 토큰)"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="외부 API 연동 점검")
    parser.add_argument("--student", help="studentId")
    parser.add_argument("--date", help="조회 날짜 (YY.MM.DD)")
    parser.add_argument("--session", help="sessionId (선택)")
    parser.add_argument("--login-id", help=".env 대신 쓸 로그인 ID")
    parser.add_argument("--password", help=".env 대신 쓸 비밀번호")
    parser.add_argument("--login-only", action="store_true", help="로그인까지만 확인")
    args = parser.parse_args(argv)

    settings = get_settings()
    print(f"BASE   : {settings.api_base_url}")
    print(f"LOGIN  : {settings.api_login_path}")
    print(f"CHAT   : {settings.api_chat_path}")

    client = ChatAPIClient(settings)

    # 1) 인증
    login_id = args.login_id or settings.api_login_id
    password = args.password or settings.api_password
    if settings.api_token:
        print("\n[1/2] 인증 — .env 의 EXTERNAL_API_TOKEN 사용")
    elif login_id and password:
        print(f"\n[1/2] 인증 — {settings.api_login_path} 로 로그인 ({login_id})")
        try:
            token = client.login(login_id, password)
        except ExternalAPIError as exc:
            print(f"  ❌ 로그인 실패: {exc}")
            return 1
        print(f"  ✅ accessToken 획득: {_mask(token)}")
    else:
        print("\n  ❌ 인증 정보가 없습니다. .env 에 EXTERNAL_API_TOKEN 또는")
        print("     EXTERNAL_API_LOGIN_ID / EXTERNAL_API_PASSWORD 를 채우세요.")
        return 1

    if args.login_only:
        return 0
    if not args.student:
        print("\n  ℹ️  대화 조회를 하려면 --student <studentId> 를 넘기세요.")
        return 0

    # 2) 대화 조회 + 파싱
    print(f"\n[2/2] 대화 조회 — studentId={args.student} date={args.date} session={args.session}")
    try:
        payload = client.fetch_chat(args.student, date=args.date, session_id=args.session)
    except ExternalAPIError as exc:
        print(f"  ❌ 조회 실패: {exc}")
        return 1

    turns = parse_chat_payload(payload)
    if not turns:
        print("  ⚠️  응답은 받았지만 파싱된 Q&A 턴이 0개입니다.")
        print(f"     응답 키: {list(payload)}")
        return 1

    print(f"  ✅ {len(turns)}개 턴 파싱 완료\n")
    for turn in turns[:3]:
        print(f"  [턴 {turn.turn_index + 1}] ({turn.date})")
        print(f"    🤖 {turn.ai_question[:60]}")
        print(f"    👤 {turn.user_answer[:60]}")
    if len(turns) > 3:
        print(f"  … 외 {len(turns) - 3}개 턴")
    return 0


if __name__ == "__main__":
    sys.exit(main())
