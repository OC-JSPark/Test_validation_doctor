"""AI 테스트 데이터 할당 — 생성(관리자) 과 날짜 자동 세팅(전문의).

실제 사용자 데이터와 다른 점은 날짜를 정하는 방식 하나다.
  SERVICE    — 척도검사 DB 에서 세션·날짜를 미리 안다.
  AI_PREVIEW — 세션 목록 API 가 없다. 전문의가 열 때 latest-date 로 받는다.
"""

from __future__ import annotations

from datetime import date

import pytest

from app.models import (
    SOURCE_AI_PREVIEW,
    SOURCE_SERVICE,
    AIPreviewDates,
    AIStudent,
    QATurn,
)
from app.repositories import assignments as assignments_repo
from app.services import admin as admin_service
from app.services import doctor as doctor_service

pytestmark = pytest.mark.db


class _FakeAIClient:
    """대화 + AI 날짜를 함께 돌려주는 가짜 클라이언트 (네트워크 없음)."""

    def __init__(self, turns, dates: AIPreviewDates | None = None) -> None:
        self._turns = list(turns)
        self._dates = dates or AIPreviewDates(
            chat_date=date(2026, 9, 22), report_date=date(2026, 9, 18)
        )
        self.chat_calls: list[tuple[str, str | None, str | None]] = []
        self.date_calls: list[str] = []

    def fetch_turns(self, student_id, *, date=None, session_id=None):
        self.chat_calls.append((student_id, date, session_id))
        return list(self._turns)

    def fetch_ai_latest_date(self, student_id):
        self.date_calls.append(student_id)
        return self._dates


@pytest.fixture
def turns() -> list[QATurn]:
    return [QATurn(i, f"질문{i}", f"답변{i}") for i in range(3)]


@pytest.fixture
def ai_students() -> list[AIStudent]:
    return [
        AIStudent(student_id="ai-stu-1", seq=1, source_id=1),
        AIStudent(student_id="ai-stu-2", seq=2, source_id=2),
    ]


# --- 관리자: 할당 생성 -------------------------------------------------------


def test_AI_학생은_1명당_할당_1건(ai_students):
    """척도검사 목록 API 가 없어 세션별로 쪼갤 수 없다."""
    targets = admin_service.build_ai_targets(ai_students)

    assert len(targets) == 2
    assert [t.student_id for t in targets] == ["ai-stu-1", "ai-stu-2"]


def test_AI_할당은_세션과_날짜가_비어있다(ai_students):
    """전문의가 열 때 latest-date 로 채운다."""
    target = admin_service.build_ai_targets(ai_students)[0]

    assert target.session_id == ""
    assert target.chat_date == ""
    assert target.scale_stage is None


def test_AI_할당에_출처가_표시된다(ai_students):
    assert all(t.source == SOURCE_AI_PREVIEW for t in admin_service.build_ai_targets(ai_students))


def test_같은_학생이_두_번_들어와도_한_건():
    students = [
        AIStudent(student_id="dup", seq=1),
        AIStudent(student_id="dup", seq=2),
    ]

    assert len(admin_service.build_ai_targets(students)) == 1


def test_학생ID_가_비면_건너뛴다():
    students = [AIStudent(student_id="", seq=1), AIStudent(student_id="ok", seq=2)]

    assert [t.student_id for t in admin_service.build_ai_targets(students)] == ["ok"]


def test_AI_할당이_DB_에_저장된다(conn, doctor, ai_students):
    targets = admin_service.build_ai_targets(ai_students)

    created, skipped = admin_service.create_assignments(conn, doctor.user_id, targets)

    assert len(created) == 2
    assert skipped == 0
    assert all(a.source == SOURCE_AI_PREVIEW for a in created)
    assert all(a.is_ai_preview for a in created)


def test_중복_할당은_건너뛴다(conn, doctor, ai_students):
    targets = admin_service.build_ai_targets(ai_students)
    admin_service.create_assignments(conn, doctor.user_id, targets)

    created, skipped = admin_service.create_assignments(conn, doctor.user_id, targets)

    assert created == []
    assert skipped == 2


def test_기존_할당은_SERVICE_로_남는다(conn, doctor):
    """마이그레이션 004 의 DEFAULT. 기존 행이 AI 로 오인되면 안 된다."""
    assignment = assignments_repo.create_assignment(
        conn, doctor.user_id, "stu-1", "sess-1", "26.08.31"
    )

    assert assignment.source == SOURCE_SERVICE
    assert not assignment.is_ai_preview


# --- 전문의: 날짜 자동 세팅 --------------------------------------------------


def test_AI_할당을_열면_최신_날짜를_받아온다(conn, doctor, turns):
    assignment = assignments_repo.create_assignment(
        conn, doctor.user_id, "ai-stu-1", "", "", None, SOURCE_AI_PREVIEW
    )
    client = _FakeAIClient(turns)

    result = doctor_service.load_evaluation_set(conn, assignment.id, client)

    assert client.date_calls == ["ai-stu-1"]
    assert result.assignment.chat_date == "26.09.22"


def test_받은_날짜로_대화를_조회한다(conn, doctor, turns):
    """날짜 없이 조회하면 어느 날짜 대화가 올지 보장되지 않는다."""
    assignment = assignments_repo.create_assignment(
        conn, doctor.user_id, "ai-stu-1", "", "", None, SOURCE_AI_PREVIEW
    )
    client = _FakeAIClient(turns)

    doctor_service.load_evaluation_set(conn, assignment.id, client)

    assert client.chat_calls == [("ai-stu-1", "26.09.22", None)]


def test_받은_날짜가_할당에_저장된다(conn, doctor, turns):
    """저장하지 않으면 CSV 추출의 '검사일자' 가 빈칸으로 남는다."""
    assignment = assignments_repo.create_assignment(
        conn, doctor.user_id, "ai-stu-1", "", "", None, SOURCE_AI_PREVIEW
    )

    result = doctor_service.load_evaluation_set(conn, assignment.id, _FakeAIClient(turns))

    assert result.assignment.chat_date == "26.09.22"
    assert assignments_repo.get_assignment(conn, assignment.id).chat_date == "26.09.22"


def test_이미_날짜가_있으면_덮어쓰지_않는다(conn, doctor, turns):
    """평가가 끝난 할당의 날짜가 바뀌면 저장된 평가와 CSV 가 어긋난다."""
    assignment = assignments_repo.create_assignment(
        conn, doctor.user_id, "ai-stu-1", "", "26.01.01", None, SOURCE_AI_PREVIEW
    )

    result = doctor_service.load_evaluation_set(conn, assignment.id, _FakeAIClient(turns))

    assert result.assignment.chat_date == "26.01.01"


def test_날짜가_겹치면_갱신하지_않는다(conn, doctor, turns):
    """유니크 제약(전문의, 학생, 세션, 날짜) 위반으로 트랜잭션이 깨지면 안 된다."""
    assignments_repo.create_assignment(
        conn, doctor.user_id, "ai-stu-1", "", "26.09.22", None, SOURCE_AI_PREVIEW
    )
    blank = assignments_repo.create_assignment(
        conn, doctor.user_id, "ai-stu-1", "", "", None, SOURCE_AI_PREVIEW
    )

    result = doctor_service.load_evaluation_set(conn, blank.id, _FakeAIClient(turns))

    # 갱신은 건너뛰되, 조회는 받은 날짜로 정상 수행된다
    assert result.assignment.chat_date == ""
    assert assignments_repo.get_assignment(conn, blank.id) is not None


def test_날짜를_못_받아도_대화는_조회한다(conn, doctor, turns):
    """데이터가 아직 없는 학생이어도 화면이 떠야 한다."""
    assignment = assignments_repo.create_assignment(
        conn, doctor.user_id, "ai-stu-1", "", "", None, SOURCE_AI_PREVIEW
    )
    client = _FakeAIClient(turns, dates=AIPreviewDates())

    result = doctor_service.load_evaluation_set(conn, assignment.id, client)

    assert result.assignment.chat_date == ""
    assert client.chat_calls == [("ai-stu-1", None, None)]
    assert len(result.evaluations) == 3


def test_일반_할당은_날짜_API_를_부르지_않는다(conn, doctor, turns):
    """실제 사용자 데이터는 할당에 날짜가 이미 있다. 부르면 낭비이고 dev 전용 경로다."""
    assignment = assignments_repo.create_assignment(
        conn, doctor.user_id, "stu-1", "sess-1", "26.08.31"
    )
    client = _FakeAIClient(turns)

    doctor_service.load_evaluation_set(conn, assignment.id, client)

    assert client.date_calls == []
    assert client.chat_calls == [("stu-1", "26.08.31", "sess-1")]


def test_새로고침_하지_않으면_날짜_API_도_부르지_않는다(conn, doctor, turns):
    assignment = assignments_repo.create_assignment(
        conn, doctor.user_id, "ai-stu-1", "", "", None, SOURCE_AI_PREVIEW
    )
    client = _FakeAIClient(turns)

    doctor_service.load_evaluation_set(conn, assignment.id, client, refresh=False)

    assert client.date_calls == []


# --- 평가 저장 (기존 흐름이 그대로 동작하는지) -------------------------------


def test_AI_할당도_기존처럼_평가를_저장한다(conn, doctor, turns):
    """출처가 달라도 평가 입력·완료 흐름은 똑같아야 한다."""
    assignment = assignments_repo.create_assignment(
        conn, doctor.user_id, "ai-stu-1", "", "", None, SOURCE_AI_PREVIEW
    )
    doctor_service.load_evaluation_set(conn, assignment.id, _FakeAIClient(turns))

    for i in range(3):
        doctor_service.save_turn(
            conn, assignment.id, i, doctor_score="Very (4점)", doctor_opinion=f"사유 {i}"
        )
    completed = doctor_service.complete_assignment(conn, assignment.id)

    assert completed.is_completed
    assert completed.completed_turns == 3


def test_AI_평가도_CSV_에_나온다(conn, doctor, turns):
    """출처와 무관하게 완료된 평가는 모두 추출 대상이다."""
    assignment = assignments_repo.create_assignment(
        conn, doctor.user_id, "ai-stu-1", "", "", None, SOURCE_AI_PREVIEW
    )
    doctor_service.load_evaluation_set(conn, assignment.id, _FakeAIClient(turns))
    for i in range(3):
        doctor_service.save_turn(
            conn, assignment.id, i, doctor_score="Very (4점)", doctor_opinion=f"사유 {i}"
        )
    doctor_service.complete_assignment(conn, assignment.id)

    csv_text = admin_service.export_csv(conn, doctor_ids=[doctor.user_id]).decode("utf-8-sig")
    header, *rows = [r for r in csv_text.splitlines() if r.strip()]
    columns = header.split(",")
    first = rows[0].split(",")

    assert first[columns.index("학생ID")] == "ai-stu-1"
    # latest-date 로 받은 날짜가 저장돼 '검사일자' 가 빈칸이 아니다
    assert first[columns.index("검사일자")] == "26.09.22"
