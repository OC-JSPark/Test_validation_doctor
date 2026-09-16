"""전문의 서비스 (SPEC.md §5 Doctor, §6 Doctor API)."""

from __future__ import annotations

import pytest

from app.models import STATUS_COMPLETED, STATUS_IN_PROGRESS, QATurn
from app.repositories import assignments as assignments_repo
from app.repositories import evaluations as evaluations_repo
from app.services import doctor as doctor_service
from app.services.doctor import AssignmentLocked

pytestmark = pytest.mark.db


class _FakeTurns:
    """지정한 턴을 그대로 돌려주는 가짜 API 클라이언트."""

    def __init__(self, turns):
        self._turns = list(turns)

    def fetch_turns(self, student_id, *, date=None, session_id=None):
        return list(self._turns)



@pytest.fixture
def assignment(conn, doctor):
    return assignments_repo.create_assignment(
        conn, doctor.user_id, "stu-1", "sess-1", "26.08.31"
    )


def _fill_all(conn, assignment_id: int, count: int) -> None:
    for i in range(count):
        doctor_service.save_turn(
            conn,
            assignment_id,
            i,
            doctor_score="Very (4점)",
            doctor_opinion=f"턴 {i} 판단 사유",
        )


# --- 작업 목록 -------------------------------------------------------------


def test_내_작업만_보인다(conn, doctor, admin, assignment):
    items = doctor_service.list_my_assignments(conn, doctor.user_id)
    assert [a.id for a in items] == [assignment.id]
    assert doctor_service.list_my_assignments(conn, admin.user_id) == []


# --- 평가 화면 진입 ---------------------------------------------------------


def test_외부_API_대화를_불러와_턴을_만든다(conn, assignment, fake_client):
    result = doctor_service.load_evaluation_set(conn, assignment.id, fake_client)

    assert result.total_turns == 4
    assert result.assignment.total_turns == 4
    assert result.evaluations[0].ai_question == "요즘 학원 생활은 어때?"
    assert result.evaluations[0].user_answer == "학원 숙제가 너무 많아요."


def test_할당의_학생_세션_날짜로_API_를_호출한다(conn, assignment, fake_client):
    doctor_service.load_evaluation_set(conn, assignment.id, fake_client)
    assert fake_client.calls == [("stu-1", "26.08.31", "sess-1")]


def test_재진입시_저장된_평가가_그대로_보인다(conn, assignment, fake_client):
    doctor_service.load_evaluation_set(conn, assignment.id, fake_client)
    doctor_service.save_turn(
        conn, assignment.id, 1, doctor_score="Slightly (2점)", doctor_opinion="사유"
    )

    result = doctor_service.load_evaluation_set(conn, assignment.id, fake_client)
    assert result.evaluations[1].doctor_score == "Slightly (2점)"
    assert result.evaluations[1].doctor_opinion == "사유"


def test_refresh_False_면_외부_API_를_호출하지_않는다(conn, assignment, fake_client):
    doctor_service.load_evaluation_set(conn, assignment.id, fake_client)
    fake_client.calls.clear()

    result = doctor_service.load_evaluation_set(
        conn, assignment.id, fake_client, refresh=False
    )
    assert fake_client.calls == []
    assert result.total_turns == 4


def test_없는_할당은_오류(conn, fake_client):
    with pytest.raises(ValueError, match="할당을 찾을 수 없습니다"):
        doctor_service.load_evaluation_set(conn, 999_999, fake_client)


# --- 턴 저장 및 진행률 ------------------------------------------------------


def test_턴_저장시_진행률이_갱신된다(conn, assignment, fake_client):
    doctor_service.load_evaluation_set(conn, assignment.id, fake_client)

    updated = doctor_service.save_turn(
        conn, assignment.id, 0, doctor_score="4점", doctor_opinion="사유"
    )
    assert updated.completed_turns == 1
    assert updated.total_turns == 4
    assert updated.progress_pct == 25.0
    assert updated.status == STATUS_IN_PROGRESS


def test_일부_필드만_저장해도_나머지가_지워지지_않는다(conn, assignment, fake_client):
    """UI 자동저장이 위젯 상태를 일부만 넘겨도 기존 입력이 살아 있어야 한다."""
    doctor_service.load_evaluation_set(conn, assignment.id, fake_client)
    doctor_service.save_turn(
        conn, assignment.id, 0, doctor_score="Very (4점)", doctor_opinion="원래 소견"
    )

    # 소견만 넘긴다 (점수는 UNSET)
    updated = doctor_service.save_turn(conn, assignment.id, 0, doctor_opinion="수정 소견")

    row = evaluations_repo.get_evaluation(conn, assignment.id, 0)
    assert row.doctor_score == "Very (4점)"
    assert row.doctor_opinion == "수정 소견"
    assert updated.completed_turns == 1  # 여전히 완료된 턴으로 집계된다


def test_can_complete_는_모든_턴이_채워져야_True(conn, assignment, fake_client):
    doctor_service.load_evaluation_set(conn, assignment.id, fake_client)
    assert doctor_service.load_evaluation_set(
        conn, assignment.id, fake_client
    ).can_complete is False

    _fill_all(conn, assignment.id, 4)
    result = doctor_service.load_evaluation_set(conn, assignment.id, fake_client)
    assert result.filled_turns == 4
    assert result.can_complete is True


# --- 최종 완료 및 잠금 ------------------------------------------------------


def test_학생_답변이_없는_턴은_평가_대상이_아니다(conn, assignment):
    """대화 끝에 AI 가 붙이는 분석 레포트(정신건강 점수·요약·조언)는
    질문이 아니라 결과물이라 학생 답변이 없다. 이걸 평가 대상으로 세면
    전문의가 채울 수 없는 턴 때문에 최종 완료가 영영 막힌다."""
    turns = [QATurn(0, "질문1", "답변1"), QATurn(1, "질문2", "답변2")]
    turns.append(QATurn(2, "1. 정신건강 점수 분석 …", ""))  # 레포트 턴
    evaluations_repo.sync_turns(conn, assignment.id, turns)

    es = doctor_service.load_evaluation_set(
        conn, assignment.id, _FakeTurns(turns), refresh=True
    )

    assert es.total_turns == 3          # 화면에는 3턴 다 보인다
    assert es.evaluable_turns == 2      # 평가해야 하는 건 2턴
    assert es.assignment.total_turns == 2


def test_레포트_턴을_비워도_최종완료된다(conn, assignment):
    """실제 dev 배포에서 막힌 건 — 21턴 중 마지막이 레포트라 완료가 안 됐다."""
    turns = [
        QATurn(0, "질문1", "답변1"),
        QATurn(1, "질문2", "답변2"),
        QATurn(2, "1. 정신건강 점수 분석 …", ""),
    ]
    client = _FakeTurns(turns)
    doctor_service.load_evaluation_set(conn, assignment.id, client)

    # 평가 대상 2턴만 채운다. 레포트 턴(2)은 손대지 않는다.
    for i in (0, 1):
        doctor_service.save_turn(
            conn, assignment.id, i, doctor_score="4점", doctor_opinion="사유"
        )

    es = doctor_service.load_evaluation_set(conn, assignment.id, client, refresh=False)
    assert es.can_complete is True
    assert es.assignment.progress_pct == 100.0

    done = doctor_service.complete_assignment(conn, assignment.id)
    assert done.status == STATUS_COMPLETED


def test_레포트_턴을_채워도_진행률이_넘치지_않는다(conn, assignment):
    """전문의가 레포트 턴에도 점수를 넣을 수 있다. 세면 100% 를 넘는다."""
    turns = [QATurn(0, "질문1", "답변1"), QATurn(2, "레포트", "")]
    client = _FakeTurns(turns)
    doctor_service.load_evaluation_set(conn, assignment.id, client)

    for e in doctor_service.load_evaluation_set(
        conn, assignment.id, client, refresh=False
    ).evaluations:
        doctor_service.save_turn(
            conn, assignment.id, e.turn_index, doctor_score="4점", doctor_opinion="사유"
        )

    updated = assignments_repo.get_assignment(conn, assignment.id)
    assert updated.completed_turns == 1        # 레포트 턴은 세지 않는다
    assert updated.progress_pct == 100.0       # 100% 를 넘지 않는다


def test_평가할_수_있는_턴이_하나도_없으면_완료_불가(conn, assignment):
    evaluations_repo.sync_turns(conn, assignment.id, [QATurn(0, "레포트만", "")])

    with pytest.raises(ValueError, match="평가할 수 있는 턴이 없습니다"):
        doctor_service.complete_assignment(conn, assignment.id)


def test_미입력_턴이_있으면_최종완료가_거부된다(conn, assignment, fake_client):
    doctor_service.load_evaluation_set(conn, assignment.id, fake_client)
    _fill_all(conn, assignment.id, 3)  # 4턴 중 3턴만

    with pytest.raises(ValueError, match="입력되지 않은 턴"):
        doctor_service.complete_assignment(conn, assignment.id)


def test_턴이_하나도_없으면_최종완료_불가(conn, assignment):
    with pytest.raises(ValueError, match="평가할 턴이 없습니다"):
        doctor_service.complete_assignment(conn, assignment.id)


def test_모든_턴_입력후_최종완료(conn, assignment, fake_client):
    doctor_service.load_evaluation_set(conn, assignment.id, fake_client)
    _fill_all(conn, assignment.id, 4)

    completed = doctor_service.complete_assignment(conn, assignment.id)
    assert completed.status == STATUS_COMPLETED
    assert completed.completed_at is not None
    assert completed.progress_pct == 100.0


def test_최종완료된_평가는_수정_잠금(conn, assignment, fake_client):
    doctor_service.load_evaluation_set(conn, assignment.id, fake_client)
    _fill_all(conn, assignment.id, 4)
    doctor_service.complete_assignment(conn, assignment.id)

    with pytest.raises(AssignmentLocked):
        doctor_service.save_turn(
            conn, assignment.id, 0, doctor_score="1점", doctor_opinion="바꿔보기"
        )
    # 값이 실제로 바뀌지 않았는지 확인
    row = evaluations_repo.get_evaluation(conn, assignment.id, 0)
    assert row.doctor_score == "Very (4점)"


def test_잠금_해제_후_다시_수정할_수_있다(conn, assignment, fake_client):
    doctor_service.load_evaluation_set(conn, assignment.id, fake_client)
    _fill_all(conn, assignment.id, 4)
    doctor_service.complete_assignment(conn, assignment.id)

    doctor_service.reopen_assignment(conn, assignment.id)
    updated = doctor_service.save_turn(
        conn, assignment.id, 0, doctor_score="Slightly (2점)", doctor_opinion="수정함"
    )

    assert updated.status == STATUS_IN_PROGRESS
    row = evaluations_repo.get_evaluation(conn, assignment.id, 0)
    assert row.doctor_score == "Slightly (2점)"


def test_완료된_건은_대화를_다시_불러와도_상태가_유지된다(conn, assignment, fake_client):
    doctor_service.load_evaluation_set(conn, assignment.id, fake_client)
    _fill_all(conn, assignment.id, 4)
    doctor_service.complete_assignment(conn, assignment.id)

    result = doctor_service.load_evaluation_set(conn, assignment.id, fake_client)
    assert result.assignment.status == STATUS_COMPLETED
