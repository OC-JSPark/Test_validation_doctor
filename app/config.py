"""환경설정 로딩.

접속 문자열·API 주소·비밀값은 전부 환경변수에서 읽는다 (CLAUDE.md 보안 규칙).
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from functools import lru_cache

from dotenv import load_dotenv

load_dotenv(override=False)

# 신규 로컬 DB 기본 접속 문자열 (docker-compose.yml 의 test-db 기준).
# 기존 서비스 DB(aimie_kids_app / aimie_kids_ai) 와 분리된 별도 데이터베이스다.
DEFAULT_DATABASE_URL = "postgresql://aimieapi:aimieapi@localhost:15432/validation_db"

# 학생 명부 조회용 DB (읽기 전용). 지금은 로컬에 복원된 aimie_kids_app 을 보고,
# 추후 인스턴스 DB 로 옮길 때는 이 접속 문자열만 바꾸면 된다.
DEFAULT_STUDENT_DB_URL = "postgresql://aimieapi:aimieapi@localhost:15432/aimie_kids_dev_app"

# 척도검사(세션) 목록 조회용 DB (읽기 전용). 학생 명부와 다른 DB 에 있다.
DEFAULT_SESSION_DB_URL = "postgresql://aimieapi:aimieapi@localhost:15432/aimie_kids_dev_ai"
DEFAULT_API_BASE_URL = "https://admin-dev.aimie-m.com"

# 외부 API 경로. 게이트웨이 프리픽스가 환경마다 달라질 수 있어 환경변수로 덮어쓸 수 있게 둔다.
DEFAULT_LOGIN_PATH = "/api-kids/adm/login"
DEFAULT_CHAT_PATH = "/api-kids/risk-students/student/chat"

# 전문의 점수/조치 기본 선택지. .env 의 DOCTOR_SCORE_OPTIONS 로 덮어쓴다.
DEFAULT_SCORE_OPTIONS = (
    "Not at all (0점)",
    "Slightly (1점)",
    "Moderately (2점)",
    "Very (3점)",
    "Extremely (4점)",
)

# KIDSCREEN-10 은 1점 항목이 'Never' 다 (나머지 척도는 기본 선택지와 동일).
KIDSCREEN_SCORE_OPTIONS = (
    "Never (1점)",
    "Slightly (2점)",
    "Moderately (3점)",
    "Very (4점)",
    "Extremely (5점)",
)

# 진단 단계(척도) 선택지. SCALE_STAGE_OPTIONS 로 덮어쓸 수 있다.
DEFAULT_SCALE_STAGES = (
    "1단계 KIDSCREEN-10",
    "2단계 PHQ-2",
    "3단계 PHQ-A",
)

# 척도별 점수 선택지. 여기에 없는 척도는 DEFAULT_SCORE_OPTIONS 를 쓴다.
# 키는 척도명에 포함된 문자열로 매칭하므로 '1단계 ' 같은 접두사가 붙어도 동작한다.
SCALE_SCORE_OPTIONS: dict[str, tuple[str, ...]] = {
    "KIDSCREEN": KIDSCREEN_SCORE_OPTIONS,
}

# AI 대화 엔진의 stage 값 → 척도명 매핑.
# 근거: aimie_kids_dev_ai.checkpoints 의 channel_values.stage / scores 세부 문항
#   stress            → KIDSCREEN-10 문항 (felt_sad, felt_lonely, got_on_well_at_school …)
#   early_depression  → 선별 단계 (PHQ-2)
#   depression        → PHQ 우울 문항 9개
#
# 척도가 아닌 진행 상태(opening / continue / finish)는 매핑하지 않는다 — 판별 안 함.
# `severe`(신체증상: back_pain, dizziness, chest_pain …) 는 위 3단계에 속하지 않아
# 아직 매핑하지 않았다. 별도 척도로 다룰지 확정되면 여기에 추가한다.
DEFAULT_STAGE_TO_SCALE: dict[str, str] = {
    "stress": "1단계 KIDSCREEN-10",
    "early_depression": "2단계 PHQ-2",
    "depression": "3단계 PHQ-A",
}


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
    student_db_url: str = DEFAULT_STUDENT_DB_URL
    session_db_url: str = DEFAULT_SESSION_DB_URL
    api_login_path: str = DEFAULT_LOGIN_PATH
    api_chat_path: str = DEFAULT_CHAT_PATH
    api_login_type: str = "TEACHER"
    scale_score_options: dict[str, tuple[str, ...]] = field(
        default_factory=lambda: dict(SCALE_SCORE_OPTIONS)
    )
    stage_to_scale: dict[str, str] = field(
        default_factory=lambda: dict(DEFAULT_STAGE_TO_SCALE)
    )

    def score_options_for(self, scale_stage: str | None) -> tuple[str, ...]:
        """척도에 맞는 점수 선택지. 매칭되는 척도가 없으면 기본 선택지.

        KIDSCREEN-10 은 1점이 'Never', 나머지는 'Not at all' 로 시작한다.
        """
        if scale_stage:
            for keyword, options in self.scale_score_options.items():
                if keyword.lower() in scale_stage.lower():
                    return options
        return self.score_options

    def scale_for_stage(self, stage: str | None) -> str | None:
        """AI 대화 엔진의 stage 값을 척도명으로 바꾼다. 모르는 stage 는 None."""
        if not stage:
            return None
        return self.stage_to_scale.get(stage.strip().lower())

    @classmethod
    def from_env(cls) -> "Settings":
        return cls(
            # 루트 .env 의 DATABASE_URL 은 기존 서비스용이라 쓰지 않는다.
            # 이 프로젝트는 VALIDATION_DATABASE_URL 만 본다.
            database_url=os.getenv("VALIDATION_DATABASE_URL", DEFAULT_DATABASE_URL),
            student_db_url=os.getenv("STUDENT_SOURCE_DATABASE_URL", DEFAULT_STUDENT_DB_URL),
            session_db_url=os.getenv("SESSION_SOURCE_DATABASE_URL", DEFAULT_SESSION_DB_URL),
            api_base_url=os.getenv("EXTERNAL_API_BASE_URL", DEFAULT_API_BASE_URL).rstrip("/"),
            api_token=os.getenv("EXTERNAL_API_TOKEN") or None,
            api_login_id=os.getenv("EXTERNAL_API_LOGIN_ID") or None,
            api_password=os.getenv("EXTERNAL_API_PASSWORD") or None,
            api_timeout=float(os.getenv("EXTERNAL_API_TIMEOUT", "10")),
            score_options=_split(os.getenv("DOCTOR_SCORE_OPTIONS"), DEFAULT_SCORE_OPTIONS),
            scale_stages=_split(os.getenv("SCALE_STAGE_OPTIONS"), DEFAULT_SCALE_STAGES),
            api_login_path=os.getenv("EXTERNAL_API_LOGIN_PATH") or DEFAULT_LOGIN_PATH,
            api_chat_path=os.getenv("EXTERNAL_API_CHAT_PATH") or DEFAULT_CHAT_PATH,
            api_login_type=os.getenv("EXTERNAL_API_LOGIN_TYPE") or "TEACHER",
        )


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings.from_env()
