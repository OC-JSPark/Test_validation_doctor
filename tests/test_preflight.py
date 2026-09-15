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


# --- 비밀번호 발급 (scripts/init_db) ----------------------------------------


def test_난수_비밀번호는_매번_다르고_충분히_길다():
    from scripts.init_db import generate_password

    passwords = {generate_password() for _ in range(50)}
    assert len(passwords) == 50
    assert all(len(p) >= 20 for p in passwords)


def test_헷갈리는_글자는_쓰지_않는다():
    """받아 적을 때 0/O, 1/l/I 를 혼동하지 않도록."""
    from scripts.init_db import generate_password

    combined = "".join(generate_password() for _ in range(50))
    for ch in "0O1lI":
        assert ch not in combined


def test_환경변수가_있으면_그_값을_쓴다(monkeypatch):
    from scripts.init_db import resolve_password

    monkeypatch.setenv("TEST_SEED_PW", "from-env-value")
    password, source = resolve_password("관리자", "TEST_SEED_PW", prompt=False)

    assert password == "from-env-value"
    assert source == "env"


def test_환경변수가_없으면_난수로_발급한다(monkeypatch):
    """서버 기본 동작. 비밀번호가 설정 파일에 남지 않는다."""
    from scripts.init_db import resolve_password

    monkeypatch.delenv("TEST_SEED_PW", raising=False)
    password, source = resolve_password("관리자", "TEST_SEED_PW", prompt=False)

    assert source == "generated"
    assert len(password) >= 20


@pytest.mark.db
def test_계정마다_다른_비밀번호가_발급된다(conn):
    """같은 값을 공유하면 유출 시 누구 계정인지 추적할 수 없다."""
    from app.services import auth
    from scripts.init_db import seed_users

    created, secrets_by_user = seed_users(conn, force=True, doctor_count=5)
    passwords = [pw for pw, _ in secrets_by_user.values()]

    assert len(created) == 6  # admin + 전문의 5
    assert len(set(passwords)) == len(passwords), "비밀번호가 중복 발급됐다"
    assert all(src == "generated" for _, src in secrets_by_user.values())


@pytest.mark.db
def test_남의_비밀번호로는_로그인되지_않는다(conn):
    from app.services import auth
    from scripts.init_db import seed_users

    _, secrets_by_user = seed_users(conn, force=True, doctor_count=2)

    for user_id, (password, _) in secrets_by_user.items():
        assert auth.login(conn, user_id, password), f"{user_id} 자기 비밀번호 로그인 실패"

    other_pw = secrets_by_user["doctor01"][0]
    assert auth.login(conn, "doctor02", other_pw) is None


@pytest.mark.db
def test_이미_있는_계정은_건드리지_않는다(conn):
    """SEED_DOCTOR_COUNT 를 늘려 전문의를 추가할 때 기존 계정이 유지돼야 한다.

    공용 DB 에 이미 doctor01~03 이 있을 수 있으므로, 어떤 계정이 미리
    존재하는지 먼저 확인하고 그 기준으로 검증한다.
    """
    from app.repositories import users as users_repo
    from scripts.init_db import seed_users

    seed_users(conn, force=True, doctor_count=2)
    before = users_repo.get_password_hash(conn, "doctor01")
    existing = {
        f"doctor{i:02d}"
        for i in range(1, 5)
        if users_repo.get_user(conn, f"doctor{i:02d}") is not None
    }

    created, _ = seed_users(conn, force=False, doctor_count=4)

    # 없던 계정만 만들어지고, 있던 계정은 목록에 없다
    assert set(created) == {f"doctor{i:02d}" for i in range(1, 5)} - existing
    assert not (set(created) & existing)
    # 기존 계정의 비밀번호는 그대로
    assert users_repo.get_password_hash(conn, "doctor01") == before


# --- DB 의 약한 비밀번호 감지 ------------------------------------------------


@pytest.mark.db
def test_약한_비밀번호를_쓰는_계정을_찾아낸다(conn):
    """환경변수가 아니라 저장된 해시에 직접 대입해 확인한다."""
    from app.preflight import _WEAK_PASSWORDS
    from app.repositories import users as users_repo
    from app.security import verify_password

    users_repo.upsert_user(conn, "weak_acct", "약한 계정", "DOCTOR", "admin1234")
    users_repo.upsert_user(conn, "strong_acct", "강한 계정", "DOCTOR", "Sf69ZszBRjMzJ5pp")

    weak_hash = users_repo.get_password_hash(conn, "weak_acct")
    strong_hash = users_repo.get_password_hash(conn, "strong_acct")

    assert any(verify_password(pw, weak_hash) for pw in _WEAK_PASSWORDS)
    assert not any(verify_password(pw, strong_hash) for pw in _WEAK_PASSWORDS)


# --- 집계 ------------------------------------------------------------------


def test_상태별_개수를_센다():
    results = [
        check_roster_session_match(9, 4),           # ok
        check_roster_session_match(2, 0),           # fail
        check_stage_coverage({}, get_settings()),   # warn
    ]
    assert summarize(results) == (1, 1, 1)
