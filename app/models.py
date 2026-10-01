"""도메인 모델 (dataclass).

DB 행과 외부 API 응답을 그대로 dict 로 들고 다니지 않기 위한 얇은 레이어.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime

ROLE_ADMIN = "ADMIN"
ROLE_DOCTOR = "DOCTOR"


class _Unset:
    """'이 필드는 건드리지 말라'는 뜻의 센티널.

    저장 시 `None`(값을 비운다) 과 '전달되지 않음'(기존 값 유지) 을 구분하기 위해 쓴다.
    Streamlit 위젯 상태가 유실됐을 때 멀쩡한 컬럼이 NULL 로 덮어써지는 것을 막는다.
    """

    _instance = None

    def __new__(cls) -> "_Unset":
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __repr__(self) -> str:  # pragma: no cover - 디버깅 편의용
        return "UNSET"

    def __bool__(self) -> bool:
        return False


UNSET = _Unset()

STATUS_PENDING = "PENDING"
STATUS_IN_PROGRESS = "IN_PROGRESS"
STATUS_COMPLETED = "COMPLETED"

# 할당의 데이터 출처.
#   SERVICE    — 실제 사용자 데이터. 학생 명부 DB + 척도검사 DB 에서 고른다.
#   AI_PREVIEW — AI DB 에만 있는 테스트용 생성 데이터.
#                세션 목록 API 가 없어 날짜를 미리 알 수 없고, 전문의가 열 때
#                `/api-kids/dev/ai-preview/latest-date` 로 최신 날짜를 받아 쓴다.
SOURCE_SERVICE = "SERVICE"
SOURCE_AI_PREVIEW = "AI_PREVIEW"


@dataclass(frozen=True)
class User:
    user_id: str
    name: str
    role: str

    @property
    def is_admin(self) -> bool:
        return self.role == ROLE_ADMIN


@dataclass(frozen=True)
class Student:
    """학생 명부 한 명 (외부 학생 DB 에서 읽기 전용으로 가져온 값)."""

    student_id: str  # 외부 API 의 studentId (t_user.user_uuid)
    nickname: str | None = None
    school_name: str | None = None
    grade: int | None = None
    class_name: str | None = None
    name: str | None = None  # 실명 (t_user.name). 닉네임과 다른 값이다

    @property
    def display_name(self) -> str:
        """화면에 쓸 사람 이름. 실명 → 닉네임 → ID 앞자리 순으로 떨어진다."""
        for candidate in (self.name, self.nickname):
            if candidate and candidate.strip():
                return candidate.strip()
        return f"(이름없음) {self.student_id[:8]}…"

    @property
    def label(self) -> str:
        """체크박스에 표시할 문자열.

        관리자는 실명으로 학생을 식별하지만, 앱 화면과 대조할 때는 닉네임도
        필요해서 둘 다 보여준다 (같으면 한 번만).
        """
        head = self.display_name
        nickname = (self.nickname or "").strip()
        if nickname and nickname != head:
            head = f"{head} ({nickname})"

        parts = [head]
        if self.school_name:
            school = self.school_name
            if self.grade:
                school += f" {self.grade}학년"
                if self.class_name:
                    school += f" {self.class_name}반"
            parts.append(school)
        return " · ".join(parts)

    def matches(self, query: str) -> bool:
        """검색어가 실명/닉네임/학교/ID 중 하나에 포함되면 True (대소문자 무시)."""
        needle = query.strip().lower()
        if not needle:
            return True
        haystack = " ".join(
            str(v).lower()
            for v in (
                self.student_id,
                self.name,
                self.nickname,
                self.school_name,
                self.class_name,
            )
            if v
        )
        return needle in haystack


@dataclass(frozen=True)
class AIStudent:
    """AI DB 에만 있는 테스트용 학생 1명.

    `GET /api-kids/dev/ai-preview/students` 응답 한 건에 대응한다.

    이 API 는 실제 사용자가 아니라 AI 가 만든 데이터라서 `name` 이 해시값이거나
    `loginId` 가 `null` 인 경우가 많다. 그대로 목록에 띄우면 일반 학생과
    구분이 안 되므로, 표시용 일련번호(`[AIuser01]`)를 붙여 쓴다.
    번호는 API 가 준 `id` 순서로 매겨 다시 불러와도 같은 학생이 같은 번호를 받는다.
    """

    student_id: str  # studentId (= AI DB 의 user_id). 할당에 저장되는 값
    seq: int  # 화면 표시용 일련번호 (1부터)
    source_id: int | None = None  # API 응답의 id. 번호를 매기는 기준
    name: str | None = None
    login_id: str | None = None
    grade: int | None = None
    class_name: str | None = None
    level: int | None = None
    level_text: str | None = None

    @property
    def tag(self) -> str:
        """말머리. 두 자리로 맞추되 100명이 넘으면 자릿수를 늘린다."""
        return f"[AIuser{self.seq:02d}]"

    @property
    def display_name(self) -> str:
        """사람이 읽을 이름. 해시값이면 앞자리만 잘라 쓴다.

        이 API 의 `name` 은 studentId 와 같은 해시가 그대로 오는 경우가 많다.
        전체를 늘어놓으면 목록이 해시로 도배되므로 그때는 이름으로 취급하지 않는다.
        """
        name = (self.name or "").strip()
        if name and name != self.student_id:
            return name
        login_id = (self.login_id or "").strip()
        if login_id:
            return login_id
        return f"{self.student_id[:8]}…"

    @property
    def label(self) -> str:
        """체크박스·선택 목록에 표시할 문자열.

        말머리를 **맨 앞**에 두어 정렬·검색 모두에서 일반 학생과 갈린다.
        """
        parts = [f"{self.tag} {self.display_name}"]
        if self.level_text:
            parts.append(self.level_text)
        if self.grade:
            grade = f"{self.grade}학년"
            if self.class_name:
                grade += f" {self.class_name}반"
            parts.append(grade)
        return " · ".join(parts)

    def matches(self, query: str) -> bool:
        """검색어가 말머리/이름/ID 중 하나에 들어 있으면 True.

        `AIuser03` 이나 `03` 으로도 찾을 수 있어야 한다.
        """
        needle = query.strip().lower()
        if not needle:
            return True
        haystack = " ".join(
            str(v).lower()
            for v in (self.tag, self.student_id, self.name, self.login_id, self.level_text)
            if v
        )
        return needle in haystack


@dataclass(frozen=True)
class AIPreviewDates:
    """AI 테스트 학생의 최신 데이터 날짜.

    `GET /api-kids/dev/ai-preview/latest-date` 응답(`date`/`reportDate`/`chatDate`).
    원본은 `YYYY-MM-DD` 인데, 대화 조회 API 는 `YY.MM.DD` 를 받으므로 변환해 쓴다.
    """

    chat_date: date | None = None
    report_date: date | None = None

    @property
    def chat_date_param(self) -> str:
        """대화 조회 API 에 넘길 날짜 (YY.MM.DD). 없으면 빈 문자열."""
        return self.chat_date.strftime("%y.%m.%d") if self.chat_date else ""

    @property
    def is_empty(self) -> bool:
        return self.chat_date is None and self.report_date is None


@dataclass(frozen=True)
class ScaleSession:
    """학생이 실시한 척도검사 1건 (= 하루톡 대화 1세션).

    외부 대화 API 는 날짜를 `YY.MM.DD` 문자열로 받으므로 변환을 여기서 책임진다.
    """

    student_id: str
    session_id: str
    session_date: date
    stage: str | None = None  # AI 대화 엔진의 원본 stage 값 (stress / depression …)
    scale_stage: str | None = None  # 위 stage 를 척도명으로 옮긴 값
    # 검사를 끝까지 마쳤는지. 분석 레포트가 생성됐으면 완료로 본다.
    # 중간에 이탈한 세션은 평가할 대화가 부족하거나 아예 없다.
    is_completed: bool = False

    @property
    def chat_date(self) -> str:
        """외부 API 가 받는 날짜 형식 (YY.MM.DD)."""
        return self.session_date.strftime("%y.%m.%d")

    @property
    def label(self) -> str:
        return f"{self.session_date:%Y-%m-%d} · {self.session_id[:12]}…"


@dataclass(frozen=True)
class QATurn:
    """외부 API 대화에서 파싱한 Q&A 한 쌍 (SPEC.md §3)."""

    turn_index: int
    ai_question: str
    user_answer: str
    date: str | None = None


@dataclass
class Assignment:
    id: int
    doctor_id: str
    student_id: str
    session_id: str
    chat_date: str
    total_turns: int
    completed_turns: int
    status: str
    scale_stage: str | None = None  # 세션에서 판별한 척도 (전문의 화면 기본값)
    created_at: datetime | None = None
    updated_at: datetime | None = None
    completed_at: datetime | None = None
    doctor_name: str | None = None
    source: str = SOURCE_SERVICE  # SERVICE | AI_PREVIEW

    @property
    def is_completed(self) -> bool:
        return self.status == STATUS_COMPLETED

    @property
    def is_ai_preview(self) -> bool:
        """AI 가 만든 테스트용 데이터인지.

        전문의 화면은 이 값을 보고 날짜를 `latest-date` API 로 채운다.
        """
        return self.source == SOURCE_AI_PREVIEW

    @property
    def progress_pct(self) -> float:
        """진행률(%). 전체 턴 수를 모르면 0."""
        if self.total_turns <= 0:
            return 0.0
        return round(self.completed_turns / self.total_turns * 100, 1)


@dataclass
class Evaluation:
    """doctor_evaluations 한 행 = 한 턴의 Q&A + 전문의 평가."""

    assignment_id: int
    turn_index: int
    evaluation_code: str | None = None
    scale_stage: str | None = None
    ai_question: str | None = None
    user_answer: str | None = None
    doctor_score: str | None = None
    doctor_opinion: str | None = None
    id: int | None = None
    updated_at: datetime | None = None

    @property
    def is_filled(self) -> bool:
        """점수와 판단 이유가 모두 채워졌으면 '완료된 턴'으로 센다."""
        return bool((self.doctor_score or "").strip()) and bool(
            (self.doctor_opinion or "").strip()
        )

    @property
    def is_evaluable(self) -> bool:
        """평가 대상 턴인지. 학생 답변이 있어야 평가할 것이 있다.

        대화 마지막에 AI 가 붙이는 분석 레포트(정신건강 점수·대화 요약·조언)는
        질문이 아니라 결과물이라 학생 답변이 따라오지 않는다. 이걸 평가 대상으로
        세면 전문의가 채울 수 없는 턴 때문에 [최종 완료] 가 영영 막힌다.
        """
        return bool((self.user_answer or "").strip())


def build_evaluation_code(assignment_id: int, turn_index: int) -> str:
    """평가 ID 생성 (SPEC 예시 'KID-001' 형식을 턴 단위로 확장)."""
    return f"KID-{assignment_id:03d}-{turn_index:02d}"
