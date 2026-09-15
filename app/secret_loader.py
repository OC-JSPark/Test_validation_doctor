"""DB 접속 정보를 AWS SSM Parameter Store 에서 가져온다.

서버에서는 접속 문자열을 `.env` 에 평문으로 두지 않는다. 파일이 유출되면
DB 3개가 한꺼번에 노출되고, 비밀번호를 바꿀 때 서버마다 파일을 고쳐야 한다.
대신 SSM 에서 읽어 그때그때 조립한다.

## 동작 방식

`SECRETS_BACKEND` 환경변수가 결정한다.

| 값 | 동작 | 쓰는 곳 |
| --- | --- | --- |
| `aws` (기본) | SSM 에서 조각을 읽어 접속 문자열을 조립한다 | dev / stg 서버 |
| `env` | `.env` / 환경변수의 `*_DATABASE_URL` 을 그대로 쓴다 | 로컬 개발 |

**기본값이 `aws` 인 이유**: 서버에서 설정을 빠뜨렸을 때 조용히 localhost 로
붙는 것보다, 자격증명이 없다고 크게 실패하는 편이 안전하다. 로컬 개발은
`.env` 에 `SECRETS_BACKEND=env` 를 명시해서 쓴다.

## SSM 파라미터 구조

`ENV` (dev / stg / prod) 가 경로에 들어간다. 서버 하나에 데이터베이스 3개가
있는 구조라, 접속 정보는 공유하고 **DB 이름만 다르다**.

| 파라미터 | 용도 | 기본값 |
| --- | --- | --- |
| `/aimie/{ENV}/DB_HOST` | 공통 호스트 | (필수) |
| `/aimie/{ENV}/DB_PORT` | 공통 포트 | (필수) |
| `/aimie/{ENV}/DB_USER` | 공통 사용자 | (필수) |
| `/aimie/DB_PASS` | 공통 비밀번호 (환경 무관) | (필수) |
| `/aimie/{ENV}/VALIDATION_DB_NAME` | 평가 DB 이름 | `validation_db` |
| `/aimie/{ENV}/DB_NAME` | 학생 명부 DB 이름 | (필수) |
| `/aimie/{ENV}/AI_DB_NAME` | 척도검사 DB 이름 | (필수) |

기본값이 있는 파라미터는 SSM 에 없어도 된다.

**조회 결과는 캐시한다.** Streamlit 은 상호작용마다 스크립트를 재실행하므로,
캐시하지 않으면 화면을 누를 때마다 SSM 을 호출해 요금과 지연이 늘어난다.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache
from urllib.parse import quote_plus

DEFAULT_REGION = "ap-northeast-2"
DEFAULT_VALIDATION_DB_NAME = "validation_db"

# 읽기 전용 소스에는 SELECT 전용 계정을 따로 쓸 수 있다.
# 없으면 공통 계정으로 떨어진다.
_READONLY_USER_PARAM = "DB_RO_USER"
_READONLY_PASS_PARAM = "DB_RO_PASS"


class SecretLoadError(RuntimeError):
    """SSM 조회 실패. 원인을 그대로 담아 배포자가 바로 고칠 수 있게 한다."""


@dataclass(frozen=True)
class DatabaseUrls:
    """세 DB 의 접속 문자열."""

    validation: str
    student: str
    session: str


@dataclass(frozen=True)
class ApiCredentials:
    """외부 대화 API 인증 수단. 셋 다 없을 수도 있다(그 경우 조회가 401 로 실패)."""

    login_id: str | None = None
    password: str | None = None
    token: str | None = None

    @property
    def is_usable(self) -> bool:
        return bool(self.token or (self.login_id and self.password))


def use_aws() -> bool:
    """AWS 에서 읽을지 여부.

    **기본은 AWS 다.** 서버에서 설정을 빠뜨렸을 때 조용히 localhost 로
    붙는 것보다, 자격증명이 없다고 크게 실패하는 편이 안전하다.
    로컬 개발은 `.env` 에 `SECRETS_BACKEND=env` 를 명시해서 쓴다.
    """
    return os.getenv("SECRETS_BACKEND", "aws").strip().lower() != "env"


def _env_name() -> str:
    """SSM 경로에 들어가는 환경 이름."""
    name = (os.getenv("ENV") or "").strip()
    if not name:
        raise SecretLoadError(
            "SECRETS_BACKEND=aws 인데 ENV 가 비어 있다. "
            "ENV=dev 처럼 환경 이름을 지정할 것 (SSM 경로 /aimie/{ENV}/... 에 쓰인다)."
        )
    return name


@lru_cache(maxsize=1)
def _client():
    try:
        import boto3
    except ImportError as exc:  # pragma: no cover - 의존성 누락은 배포 문제다
        raise SecretLoadError(
            "boto3 가 설치돼 있지 않다. uv sync --frozen 을 실행할 것."
        ) from exc

    region = os.getenv("AWS_REGION", DEFAULT_REGION)
    return boto3.client("ssm", region_name=region)


def _is_not_found(exc: Exception) -> bool:
    """'파라미터가 없다' 인지, 권한·네트워크 문제인지 구분한다.

    없는 것은 캐시해도 되지만(선택적 파라미터), 일시적 오류를 '없음' 으로
    캐시하면 조용히 기본값으로 떨어져 엉뚱한 DB 에 붙게 된다.
    """
    return "ParameterNotFound" in f"{type(exc).__name__}{exc}"


@lru_cache(maxsize=64)
def _fetch(path: str) -> str | None:
    """SSM 파라미터 하나. 없으면 None.

    lru_cache 는 예외를 캐시하지 않으므로, '없음' 을 None 으로 **돌려줘야**
    캐시된다. 그러지 않으면 선택적 파라미터를 화면 조작마다 다시 조회한다.
    """
    try:
        response = _client().get_parameter(Name=path, WithDecryption=True)
    except Exception as exc:  # boto3 예외 계층이 넓어 폭넓게 잡는다
        if _is_not_found(exc):
            return None
        raise SecretLoadError(
            f"SSM 파라미터를 읽지 못했다: {path}\n"
            f"    IAM 권한(ssm:GetParameter, kms:Decrypt)과 리전을 확인할 것.\n"
            f"    원인: {type(exc).__name__}: {exc}"
        ) from exc
    return response["Parameter"]["Value"].strip()


def get_parameter(path: str) -> str:
    """필수 파라미터. 없으면 실패한다."""
    value = _fetch(path)
    if value is None:
        raise SecretLoadError(
            f"SSM 파라미터가 없다: {path}\n"
            f"    파라미터 존재 여부와 IAM 권한(ssm:GetParameter, kms:Decrypt)을 확인할 것."
        )
    return value


def get_parameter_or(path: str, default: str) -> str:
    """선택적 파라미터. 없으면 기본값."""
    value = _fetch(path)
    return default if value is None else value


def build_dsn(host: str, port: str, name: str, user: str, password: str) -> str:
    """접속 문자열 조립. 비밀번호에 특수문자가 있어도 깨지지 않게 인코딩한다."""
    return f"postgresql://{user}:{quote_plus(password)}@{host}:{port}/{name}"


def load_database_urls() -> DatabaseUrls:
    """SSM 에서 세 DB 의 접속 문자열을 만든다.

    호출 전에 `use_aws()` 로 AWS 모드인지 확인할 것.
    """
    env = _env_name()
    prefix = f"/aimie/{env}"

    host = get_parameter(f"{prefix}/DB_HOST")
    port = get_parameter(f"{prefix}/DB_PORT")
    user = get_parameter(f"{prefix}/DB_USER")
    password = get_parameter("/aimie/DB_PASS")

    # 읽기 전용 소스는 SELECT 전용 계정이 있으면 그것을 쓴다.
    ro_user = get_parameter_or(f"{prefix}/{_READONLY_USER_PARAM}", user)
    ro_password = get_parameter_or(f"{prefix}/{_READONLY_PASS_PARAM}", password)

    validation_name = get_parameter_or(
        f"{prefix}/VALIDATION_DB_NAME", DEFAULT_VALIDATION_DB_NAME
    )

    # `DB_NAME` 은 이름과 달리 **척도검사(AI) DB** 를 가리킨다.
    # 실제 값이 `aimie_kids_dev_ai` 라, 이름만 보고 학생 명부로 쓰면
    # 명부가 비어 할당을 만들 수 없다. 기존 파라미터라 그대로 두고 여기서 매핑한다.
    session_name = get_parameter(f"{prefix}/DB_NAME")

    # 학생 명부 DB 는 SSM 에 전용 파라미터가 없어 이름을 직접 지정한다.
    # 같은 경로에 파라미터를 만들어 두면 그 값이 우선한다.
    #
    # ⚠️ 이름이 env 를 타지 않는다. stg 에 올릴 때는 이 값(또는 SSM 파라미터)을
    #    그 환경의 DB 이름으로 바꿔야 한다. 그러지 않으면 stg 에서도 dev 명부를 본다.
    #    preflight 의 '명부·세션 정합성' 검사가 교집합 0 으로 잡아낸다.
    student_name = get_parameter_or(f"{prefix}/aimie_kids_dev_app", "aimie_kids_dev_app")

    return DatabaseUrls(
        validation=build_dsn(host, port, validation_name, user, password),
        student=build_dsn(host, port, student_name, ro_user, ro_password),
        session=build_dsn(host, port, session_name, ro_user, ro_password),
    )


def load_api_credentials() -> ApiCredentials:
    """SSM 에서 외부 대화 API 인증 수단을 가져온다.

    DB 접속 정보와 달리 **없어도 앱은 뜬다.** 관리자 화면(할당·CSV)은 외부 API 를
    쓰지 않으므로, 계정이 없다고 전체를 막으면 손해가 크다. 대신 전문의가 대화를
    조회할 때 실패하고, `preflight` 가 배포 전에 미리 잡아낸다.

    호출 전에 `use_aws()` 로 AWS 모드인지 확인할 것.
    """
    prefix = f"/aimie/{_env_name()}"
    return ApiCredentials(
        login_id=get_parameter_or(f"{prefix}/EXTERNAL_API_LOGIN_ID", "") or None,
        password=get_parameter_or(f"{prefix}/EXTERNAL_API_PASSWORD", "") or None,
        token=get_parameter_or(f"{prefix}/EXTERNAL_API_TOKEN", "") or None,
    )


def clear_cache() -> None:
    """캐시를 비운다 (파라미터를 바꾼 뒤 재기동 없이 반영할 때)."""
    _fetch.cache_clear()
    # 테스트에서 _client 를 교체하면 cache_clear 가 없을 수 있다.
    if hasattr(_client, "cache_clear"):
        _client.cache_clear()
