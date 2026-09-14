"""배포 전 점검 판정 로직 (DB·네트워크 없이)."""

from __future__ import annotations

import pytest

from app.config import (
    DEFAULT_DATABASE_URL,
    DEFAULT_SESSION_DB_URL,
    DEFAULT_STUDENT_DB_URL,
    Settings,
    get_settings,
)
from app.preflight import (
    check_api_settings,
    check_defaults,
    check_roster_session_match,
    check_seed_passwords,
    check_stage_coverage,
    mask_dsn,
    summarize,
)


def _settings(**overrides) -> Settings:
    base = dict(
        database_url="postgresql://u:p@stg-db:5432/validation_db",
        student_db_url="postgresql://u:p@stg-db:5432/app",
        session_db_url="postgresql://u:p@stg-db:5432/ai",
        api_base_url="https://admin-stg.example.com",
        api_token=None,
        api_login_id="id",
        api_password="pw",
        api_timeout=10.0,
        score_options=("a",),
        scale_stages=("1단계 PHQ-stress",),
    )
    base.update(overrides)
    return Settings(**base)


# --- 비밀번호 마스킹 --------------------------------------------------------


def test_접속문자열의_비밀번호를_가린다():
    masked = mask_dsn("postgresql://aimieapi:secret@localhost:15432/db")
    assert "secret" not in masked
    assert "aimieapi" in masked and "localhost:15432/db" in masked


def test_비밀번호가_없는_문자열도_깨지지_않는다():
    assert mask_dsn("postgresql://localhost/db") == "postgresql://localhost/db"
    assert mask_dsn("not-a-dsn") == "not-a-dsn"


# --- 기본값(localhost) 감지 — 가장 자주 겪은 함정 ---------------------------


def test_기본값을_그대로_쓰면_경고한다():
    s = _settings(
        database_url=DEFAULT_DATABASE_URL,
        student_db_url=DEFAULT_STUDENT_DB_URL,
        session_db_url=DEFAULT_SESSION_DB_URL,
    )
    results = check_defaults(s)

    assert all(r.status == "warn" for r in results)
    assert any("VALIDATION_DATABASE_URL" in r.detail for r in results)


def test_stg_주소로_바꾸면_통과한다():
    results = check_defaults(_settings())
    assert all(r.status == "ok" for r in results)
    # 경고 문구에 비밀번호가 새지 않는다
    assert all("p@" not in r.detail for r in results)


# --- API 설정 ---------------------------------------------------------------


def test_인증수단이_없으면_치명적():
    results = check_api_settings(_settings(api_login_id=None, api_password=None))
    auth = next(r for r in results if r.name == "API 인증")
    assert auth.status == "fail"


def test_토큰만_있어도_통과한다():
    results = check_api_settings(
        _settings(api_token="tok", api_login_id=None, api_password=None)
    )
    assert next(r for r in results if r.name == "API 인증").status == "ok"


def test_HTTPS_가_아니면_경고한다():
    results = check_api_settings(_settings(api_base_url="http://insecure.example.com"))
    assert next(r for r in results if r.name == "API 호스트").status == "warn"


# --- 시드 비밀번호 ----------------------------------------------------------


def test_데모_비밀번호가_남아있으면_치명적():
    assert check_seed_passwords("admin1234", "doctor1234").status == "fail"
    assert check_seed_passwords("admin1234", "바꿈").status == "fail"


def test_둘_다_바꾸면_통과():
    assert check_seed_passwords("s3cret!", "an0ther!").status == "ok"


# --- stage 매핑 커버리지 ----------------------------------------------------


def test_매핑되지_않은_stage_를_경고한다():
    """dev 에서 early_depression 을 못 찾아 2단계가 영영 비었던 회귀."""
    result = check_stage_coverage({"stress": 10, "정체불명단계": 3}, get_settings())

    assert result.status == "warn"
    assert "정체불명단계" in result.detail


def test_척도가_아닌_stage_는_경고하지_않는다():
    """진행 상태(opening·finish·continue)와 위험 신호 분기(severe)는 척도가 아니다."""
    result = check_stage_coverage(
        {"stress": 10, "opening": 5, "finish": 2, "continue": 1, "severe": 9},
        get_settings(),
    )
    assert result.status == "ok"


def test_척도는_3단계가_전부다():
    settings = get_settings()
    assert settings.scale_for_stage("stress") == "1단계 PHQ-stress"
    assert settings.scale_for_stage("early_depression") == "2단계 PHQ-2"
    assert settings.scale_for_stage("depression") == "3단계 PHQ-A"
    assert settings.scale_for_stage("severe") is None  # 척도 아님


def test_stage_가_하나도_없으면_경고():
    assert check_stage_coverage({}, get_settings()).status == "warn"


# --- 명부·세션 정합성 -------------------------------------------------------


def test_교집합이_0이면_치명적():
    """명부와 세션 DB 가 다른 환경 덤프일 때 실제로 겪은 상황."""
    result = check_roster_session_match(student_count=2, matched_count=0)

    assert result.status == "fail"
    assert "다른 환경" in result.detail


def test_명부가_비어도_치명적():
    assert check_roster_session_match(0, 0).status == "fail"


def test_일부라도_겹치면_통과():
    assert check_roster_session_match(9, 4).status == "ok"


# --- 집계 ------------------------------------------------------------------


def test_상태별_개수를_센다():
    results = [
        check_seed_passwords("a", "b"),          # ok
        check_seed_passwords("admin1234", "b"),  # fail
        check_stage_coverage({}, get_settings()),  # warn
    ]
    assert summarize(results) == (1, 1, 1)
