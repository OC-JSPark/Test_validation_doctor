"""AWS SSM 에서 DB 접속 문자열 가져오기.

실제 AWS 를 호출하지 않는다. boto3 클라이언트를 가짜로 바꿔 검증한다
(테스트가 네트워크·자격증명에 의존하면 안 된다 — CLAUDE.md).
"""

from __future__ import annotations

import pytest

from app import secret_loader
from app.secret_loader import (
    DEFAULT_VALIDATION_DB_NAME,
    SecretLoadError,
    build_dsn,
    load_database_urls,
    use_aws,
)


class FakeSSM:
    """SSM 흉내. 없는 파라미터는 boto3 처럼 예외를 던진다."""

    def __init__(self, params: dict[str, str]) -> None:
        self.params = params
        self.calls: list[str] = []

    def get_parameter(self, Name: str, WithDecryption: bool = False):  # noqa: N803
        self.calls.append(Name)
        if Name not in self.params:
            raise RuntimeError(f"ParameterNotFound: {Name}")
        return {"Parameter": {"Value": self.params[Name]}}


_BASE_PARAMS = {
    "/aimie/dev/DB_HOST": "dev-db.internal",
    "/aimie/dev/DB_PORT": "5432",
    "/aimie/dev/DB_USER": "app_user",
    "/aimie/DB_PASS": "p@ss word/2026",
    "/aimie/dev/DB_NAME": "aimie_kids_dev_app",
    "/aimie/dev/AI_DB_NAME": "aimie_kids_dev_ai",
}


@pytest.fixture
def fake_ssm(monkeypatch):
    """SSM 클라이언트를 가짜로 바꾸고 캐시를 비운다."""

    def _install(params: dict[str, str]) -> FakeSSM:
        client = FakeSSM(params)
        secret_loader.clear_cache()
        monkeypatch.setattr(secret_loader, "_client", lambda: client)
        monkeypatch.setenv("SECRETS_BACKEND", "aws")
        monkeypatch.setenv("ENV", "dev")
        return client

    yield _install
    secret_loader.clear_cache()


# --- 모드 선택 --------------------------------------------------------------


def test_기본은_aws_모드다(monkeypatch):
    """서버에서 설정을 빠뜨렸을 때 조용히 localhost 로 붙으면 안 된다."""
    monkeypatch.delenv("SECRETS_BACKEND", raising=False)
    assert use_aws() is True


def test_env_는_명시해야_켜진다(monkeypatch):
    """로컬 개발은 .env 에 SECRETS_BACKEND=env 를 적어야 한다."""
    monkeypatch.setenv("SECRETS_BACKEND", "env")
    assert use_aws() is False
    monkeypatch.setenv("SECRETS_BACKEND", "ENV")  # 대소문자 무관
    assert use_aws() is False


def test_오타나_빈_값은_aws_로_떨어진다(monkeypatch):
    """'enviroment' 같은 오타로 조용히 localhost 를 쓰게 두지 않는다."""
    for value in ("", "aws", "enviroment", "vault"):
        monkeypatch.setenv("SECRETS_BACKEND", value)
        assert use_aws() is True, f"{value!r} 에서 env 모드로 떨어졌다"


# --- 접속 문자열 조립 -------------------------------------------------------


def test_비밀번호의_특수문자를_인코딩한다():
    """@ 나 / 가 들어가면 인코딩하지 않을 경우 접속 문자열이 깨진다."""
    dsn = build_dsn("h", "5432", "db", "u", "p@ss word/2026")

    assert "p%40ss+word%2F2026" in dsn
    assert dsn.count("@") == 1  # 호스트 구분자만 남는다


def test_세_DB_를_모두_만든다(fake_ssm):
    fake_ssm(_BASE_PARAMS)

    urls = load_database_urls()

    assert urls.validation.endswith(f"/{DEFAULT_VALIDATION_DB_NAME}")
    assert urls.student.endswith("/aimie_kids_dev_app")
    assert urls.session.endswith("/aimie_kids_dev_ai")
    assert all("dev-db.internal:5432" in u for u in (urls.validation, urls.student, urls.session))


def test_평가DB_이름을_SSM_에서_덮어쓸_수_있다(fake_ssm):
    fake_ssm({**_BASE_PARAMS, "/aimie/dev/VALIDATION_DB_NAME": "validation_dev"})

    assert load_database_urls().validation.endswith("/validation_dev")


def test_평가DB_이름은_없으면_기본값(fake_ssm):
    """선택적 파라미터는 SSM 에 없어도 동작해야 한다."""
    fake_ssm(_BASE_PARAMS)

    assert load_database_urls().validation.endswith("/validation_db")


# --- 읽기 전용 계정 ---------------------------------------------------------


def test_읽기전용_계정이_있으면_소스DB_에_쓴다(fake_ssm):
    fake_ssm(
        {
            **_BASE_PARAMS,
            "/aimie/dev/DB_RO_USER": "readonly_user",
            "/aimie/dev/DB_RO_PASS": "ro-secret",
        }
    )

    urls = load_database_urls()

    assert "readonly_user" in urls.student
    assert "readonly_user" in urls.session
    assert "app_user" in urls.validation  # 평가 DB 는 쓰기가 필요하다


def test_읽기전용_계정이_없으면_공통_계정으로_떨어진다(fake_ssm):
    fake_ssm(_BASE_PARAMS)

    urls = load_database_urls()

    assert "app_user" in urls.student
    assert "app_user" in urls.session


# --- 환경 분리 --------------------------------------------------------------


def test_ENV_가_경로에_들어간다(fake_ssm, monkeypatch):
    client = fake_ssm(
        {
            "/aimie/stg/DB_HOST": "stg-db.internal",
            "/aimie/stg/DB_PORT": "5432",
            "/aimie/stg/DB_USER": "u",
            "/aimie/DB_PASS": "p",
            "/aimie/stg/DB_NAME": "app",
            "/aimie/stg/AI_DB_NAME": "ai",
        }
    )
    monkeypatch.setenv("ENV", "stg")

    urls = load_database_urls()

    assert "stg-db.internal" in urls.validation
    assert all(p.startswith("/aimie/stg/") or p == "/aimie/DB_PASS" for p in client.calls)


def test_ENV_가_비면_친절하게_실패한다(fake_ssm, monkeypatch):
    fake_ssm(_BASE_PARAMS)
    monkeypatch.setenv("ENV", "")

    with pytest.raises(SecretLoadError, match="ENV"):
        load_database_urls()


# --- 실패 처리 --------------------------------------------------------------


def test_필수_파라미터가_없으면_실패한다(fake_ssm):
    """조용히 기본값으로 떨어지면 엉뚱한 DB 에 붙는다. 뜨지 않는 편이 낫다."""
    missing = {k: v for k, v in _BASE_PARAMS.items() if k != "/aimie/dev/DB_HOST"}
    fake_ssm(missing)

    with pytest.raises(SecretLoadError) as excinfo:
        load_database_urls()

    assert "DB_HOST" in str(excinfo.value)
    assert "IAM" in str(excinfo.value)  # 원인 힌트를 준다


def test_같은_파라미터를_두_번_읽지_않는다(fake_ssm):
    """Streamlit 은 상호작용마다 재실행된다. 캐시가 없으면 SSM 호출이 폭증한다."""
    client = fake_ssm(_BASE_PARAMS)

    load_database_urls()
    first_round = len(client.calls)
    load_database_urls()

    assert len(client.calls) == first_round, "캐시가 동작하지 않는다"
