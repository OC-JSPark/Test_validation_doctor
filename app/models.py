"""도메인 모델 (dataclass).

DB 행과 외부 API 응답을 그대로 dict 로 들고 다니지 않기 위한 얇은 레이어.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

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

    @property
    def label(self) -> str:
        """체크박스에 표시할 이름. 닉네임이 없으면 ID 앞부분으로 대체한다."""
        name = (self.nickname or "").strip() or f"(이름없음) {self.student_id[:8]}…"
        parts = [name]
        if self.school_name:
            school = self.school_name
            if self.grade:
                school += f" {self.grade}학년"
                if self.class_name:
                    school += f" {self.class_name}반"
            parts.append(school)
        return " · ".join(parts)

    def matches(self, query: str) -> bool:
        """검색어가 이름/학교/ID 중 하나에 포함되면 True (대소문자 무시)."""
        needle = query.strip().lower()
        if not needle:
            return True
        haystack = " ".join(
            str(v).lower()
            for v in (self.student_id, self.nickname, self.school_name, self.class_name)
            if v
        )
        return needle in haystack


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
    created_at: datetime | None = None
    updated_at: datetime | None = None
    completed_at: datetime | None = None
    doctor_name: str | None = None

    @property
    def is_completed(self) -> bool:
        return self.status == STATUS_COMPLETED

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


def build_evaluation_code(assignment_id: int, turn_index: int) -> str:
    """평가 ID 생성 (SPEC 예시 'KID-001' 형식을 턴 단위로 확장)."""
    return f"KID-{assignment_id:03d}-{turn_index:02d}"
