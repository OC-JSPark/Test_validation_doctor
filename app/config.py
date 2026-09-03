"""환경설정 로딩.

접속 문자열·API 주소·비밀값은 전부 환경변수에서 읽는다 (CLAUDE.md 보안 규칙).
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache

from dotenv import load_dotenv

load_dotenv(override=False)

# 신규 로컬 DB 기본 접속 문자열 (docker-compose.yml 의 test-db 기준).
# 기존 서비스 DB(aimie_kids_app / aimie_kids_ai) 와 분리된 별도 데이터베이스다.
DEFAULT_DATABASE_URL = "postgresql://aimieapi:aimieapi@localhost:15432/validation_db"
DEFAULT_API_BASE_URL = "https://dev.aimie-m.com"

# 전문의 점수/조치 선택지. 실제 척도가 확정되면 .env 의 DOCTOR_SCORE_OPTIONS 로 덮어쓴다.
DEFAULT_SCORE_OPTIONS = (
    "Not at all (1점)",
    "Slightly (2점)",
    "Moderately (3점)",
    "Very (4점)",
    "Extremely (5점)",
)

# 진단 단계(척도) 선택지. 마찬가지로 SCALE_STAGE_OPTIONS 로 덮어쓸 수 있다.
DEFAULT_SCALE_STAGES = (
    "1단계 KIDSCREEN-10",
    "2단계 PHQ-9",
    "3단계 GAD-7",
)


def _split(value: str | None, fallback: tuple[str, ...]) -> tuple[str, ...]:
    """콤마로 구분된 환경변수를 튜플로. 비어 있으면 기본값을 쓴다."""
    if not value:
        return fallback
    items = tuple(part.strip() for part in value.split(",") if part.strip())
    return items or fallback


@dataclass(frozen=True)
class Settings:
    database_url: str
    api_base_url: str
    api_token: str | None
    api_login_id: str | None
    api_password: str | None
    api_timeout: float
    score_options: tuple[str, ...]
    scale_stages: tuple[str, ...]

    @classmethod
    def from_env(cls) -> "Settings":
        return cls(
            # 루트 .env 의 DATABASE_URL 은 기존 서비스용이라 쓰지 않는다.
            # 이 프로젝트는 VALIDATION_DATABASE_URL 만 본다.
            database_url=os.getenv("VALIDATION_DATABASE_URL", DEFAULT_DATABASE_URL),
            api_base_url=os.getenv("EXTERNAL_API_BASE_URL", DEFAULT_API_BASE_URL).rstrip("/"),
            api_token=os.getenv("EXTERNAL_API_TOKEN") or None,
            api_login_id=os.getenv("EXTERNAL_API_LOGIN_ID") or None,
            api_password=os.getenv("EXTERNAL_API_PASSWORD") or None,
            api_timeout=float(os.getenv("EXTERNAL_API_TIMEOUT", "10")),
            score_options=_split(os.getenv("DOCTOR_SCORE_OPTIONS"), DEFAULT_SCORE_OPTIONS),
            scale_stages=_split(os.getenv("SCALE_STAGE_OPTIONS"), DEFAULT_SCALE_STAGES),
        )


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings.from_env()
