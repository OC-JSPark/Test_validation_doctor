"""환경설정 로딩.

접속 문자열·API 주소·비밀값은 전부 환경변수에서 읽는다 (CLAUDE.md 보안 규칙).
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from functools import lru_cache

from dotenv import load_dotenv

from app.secret_loader import load_api_credentials, load_database_urls, use_aws

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

# AI 테스트 데이터 조회 경로 (API 정의서 4.n.n "AI 데이터 확인").
# AI DB 에만 있는 생성 데이터를 보기 위한 dev 전용 엔드포인트다.
# 대화(하루톡) 조회는 실제 데이터와 똑같이 DEFAULT_CHAT_PATH 를 쓴다.
DEFAULT_AI_STUDENTS_PATH = "/api-kids/dev/ai-preview/students"
DEFAULT_AI_LATEST_DATE_PATH = "/api-kids/dev/ai-preview/latest-date"
DEFAULT_AI_REPORT_PATH = "/api-kids/dev/ai-preview/report"

# 학생의 날짜별 활동 이력 (= 척도검사 목록).
# 정의서의 ai-preview 3종에는 없지만, 운영 관리자 화면(#teacher/ai-data-preview)이
# 날짜별 이력을 그릴 때 쓰는 경로다. 응답 각 행이 날짜와 **실제 sessionId** 를
# 들고 있어, 실제 사용자 데이터처럼 세션 단위로 할당할 수 있다.
# ai-preview 전용이 아니라 위험학생 화면과 공용이다.
DEFAULT_AI_ACTIVITY_PATH = "/api-kids/risk-students/student/activity"

# 전문의 점수/조치 기본 선택지. .env 의 DOCTOR_SCORE_OPTIONS 로 덮어쓴다.
DEFAULT_SCORE_OPTIONS = (
    "Not at all (0점)",
    "Slightly (1점)",
    "Moderately (2점)",
    "Very (3점)",
    "Extremely (4점)",
)

# PHQ-stress 는 3점 척도다 (다른 척도는 5점 기본 선택지).
PHQ_STRESS_SCORE_OPTIONS = (
    "Not at all (0점)",
    "Bothered a little (1점)",
    "Bothered a lot (2점)",
)

# 진단 단계(척도) 선택지. SCALE_STAGE_OPTIONS 로 덮어쓸 수 있다.
DEFAULT_SCALE_STAGES = (
    "1단계 PHQ-stress",
    "2단계 PHQ-2",
    "3단계 PHQ-A",
)

# 척도별 점수 선택지. 여기에 없는 척도는 DEFAULT_SCORE_OPTIONS 를 쓴다.
# 키는 척도명에 포함된 문자열로 매칭하므로 '1단계 ' 같은 접두사가 붙어도 동작한다.
SCALE_SCORE_OPTIONS: dict[str, tuple[str, ...]] = {
    "PHQ-stress": PHQ_STRESS_SCORE_OPTIONS,
}

# AI 대화 엔진의 stage 값 → 척도명 매핑.
# 근거: aimie_kids_dev_ai.checkpoints 의 channel_values.stage / scores 세부 문항
#   stress            → PHQ-stress 문항 (felt_sad, felt_lonely, got_on_well_at_school …)
#   early_depression  → 선별 단계 (PHQ-2)
#   depression        → PHQ 우울 문항 9개
#
# 척도는 이 3단계가 전부다. 아래 stage 는 척도가 아니라 매핑하지 않는다.
#   opening / continue / finish — 대화 진행 상태
#   severe                      — 위험 신호 분기 (평가 대상 아님)
DEFAULT_STAGE_TO_SCALE: dict[str, str] = {
    "stress": "1단계 PHQ-stress",
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
    api_ai_students_path: str = DEFAULT_AI_STUDENTS_PATH
    api_ai_latest_date_path: str = DEFAULT_AI_LATEST_DATE_PATH
    api_ai_report_path: str = DEFAULT_AI_REPORT_PATH
    api_ai_activity_path: str = DEFAULT_AI_ACTIVITY_PATH
    scale_score_options: dict[str, tuple[str, ...]] = field(
        default_factory=lambda: dict(SCALE_SCORE_OPTIONS)
    )
    stage_to_scale: dict[str, str] = field(
        default_factory=lambda: dict(DEFAULT_STAGE_TO_SCALE)
    )

    def score_options_for(self, scale_stage: str | None) -> tuple[str, ...]:
        """척도에 맞는 점수 선택지. 매칭되는 척도가 없으면 기본 선택지.

        PHQ-stress 는 0~2점 3점 척도, 나머지는 0~4점 5점 척도다.
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

    def scale_for_level(self, level: int | None) -> str | None:
        """활동 이력의 `level` 을 척도명으로 바꾼다 (1부터 시작하는 단계 번호).

        실측값: 1='스트레스', 3='우울증', 0='미분류'.
        0 은 아직 척도가 정해지지 않은 검사라 판별하지 않는다 — 전문의가 고른다.
        """
        if not level or level < 1 or level > len(self.scale_stages):
            return None
        return self.scale_stages[level - 1]

    @classmethod
    def from_env(cls) -> "Settings":
        # 서버(SECRETS_BACKEND=aws)에서는 접속 문자열을 .env 가 아니라 SSM 에서 가져온다.
        # 실패하면 조용히 localhost 로 떨어지지 않고 예외를 올린다 — 잘못된 DB 에
        # 붙는 것보다 뜨지 않는 편이 낫다.
        db_urls = load_database_urls() if use_aws() else None
        # API 계정도 같은 경로로 가져온다. 서버 `.env` 에 비밀값을 남기지 않는다.
        api = load_api_credentials() if use_aws() else None

        return cls(
            # 루트 .env 의 DATABASE_URL 은 기존 서비스용이라 쓰지 않는다.
            # 이 프로젝트는 VALIDATION_DATABASE_URL 만 본다.
            database_url=(
                db_urls.validation
                if db_urls
                else os.getenv("VALIDATION_DATABASE_URL", DEFAULT_DATABASE_URL)
            ),
            student_db_url=(
                db_urls.student
                if db_urls
                else os.getenv("STUDENT_SOURCE_DATABASE_URL", DEFAULT_STUDENT_DB_URL)
            ),
            session_db_url=(
                db_urls.session
                if db_urls
                else os.getenv("SESSION_SOURCE_DATABASE_URL", DEFAULT_SESSION_DB_URL)
            ),
            api_base_url=os.getenv("EXTERNAL_API_BASE_URL", DEFAULT_API_BASE_URL).rstrip("/"),
            api_token=(api.token if api else os.getenv("EXTERNAL_API_TOKEN") or None),
            api_login_id=(
                api.login_id if api else os.getenv("EXTERNAL_API_LOGIN_ID") or None
            ),
            api_password=(
                api.password if api else os.getenv("EXTERNAL_API_PASSWORD") or None
            ),
            api_timeout=float(os.getenv("EXTERNAL_API_TIMEOUT", "10")),
            score_options=_split(os.getenv("DOCTOR_SCORE_OPTIONS"), DEFAULT_SCORE_OPTIONS),
            scale_stages=_split(os.getenv("SCALE_STAGE_OPTIONS"), DEFAULT_SCALE_STAGES),
            api_login_path=os.getenv("EXTERNAL_API_LOGIN_PATH") or DEFAULT_LOGIN_PATH,
            api_chat_path=os.getenv("EXTERNAL_API_CHAT_PATH") or DEFAULT_CHAT_PATH,
            api_login_type=os.getenv("EXTERNAL_API_LOGIN_TYPE") or "TEACHER",
            api_ai_students_path=(
                os.getenv("EXTERNAL_API_AI_STUDENTS_PATH") or DEFAULT_AI_STUDENTS_PATH
            ),
            api_ai_latest_date_path=(
                os.getenv("EXTERNAL_API_AI_LATEST_DATE_PATH")
                or DEFAULT_AI_LATEST_DATE_PATH
            ),
            api_ai_report_path=(
                os.getenv("EXTERNAL_API_AI_REPORT_PATH") or DEFAULT_AI_REPORT_PATH
            ),
            api_ai_activity_path=(
                os.getenv("EXTERNAL_API_AI_ACTIVITY_PATH") or DEFAULT_AI_ACTIVITY_PATH
            ),
        )


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings.from_env()
