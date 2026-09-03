"""리포지토리 계층 (실제 validation_db 사용, 매 테스트 롤백)."""

from __future__ import annotations

import pytest

from app.models import (
    STATUS_COMPLETED,
    STATUS_IN_PROGRESS,
    STATUS_PENDING,
    QATurn,
    build_evaluation_code,
)
from app.repositories import assignments as assignments_repo
from app.repositories import evaluations as evaluations_repo
from app.repositories import users as users_repo

pytestmark = pytest.mark.db


def _turns(count: int) -> list[QATurn]:
    return [QATurn(i, f"질문{i}", f"답변{i}") for i in range(count)]


# --- users ---------------------------------------------------------------


def test_계정_생성_후_조회된다(conn, doctor):
    found = users_repo.get_user(conn, doctor.user_id)
    assert found is not None
    assert found.name == "테스트 전문의"
    assert found.role == "DOCTOR"


def test_비밀번호는_해시로_저장된다(conn, doctor):
    stored = users_repo.get_password_hash(conn, doctor.user_id)
    assert stored is not None
    assert "pw1234" not in stored


def test_없는_계정은_None(conn):
    assert users_repo.get_user(conn, "존재하지-않음") is None
    assert users_repo.get_password_hash(conn, "존재하지-않음") is None


def test_전문의_목록에_ADMIN_은_안_들어간다(conn, doctor, admin):
    doctor_ids = {u.user_id for u in users_repo.list_doctors(conn)}
    assert doctor.user_id in doctor_ids
    assert admin.user_id not in doctor_ids


# --- assignments ---------------------------------------------------------


def test_할당_생성_기본값(conn, doctor):
    assignment = assignments_repo.create_assignment(
        conn, doctor.user_id, "stu-1", "sess-1", "26.08.31"
    )
    assert assignment is not None
    assert assignment.status == STATUS_PENDING
    assert assignment.total_turns == 0
    assert assignment.completed_turns == 0
    assert assignment.progress_pct == 0.0


def test_같은_조합_중복_할당은_None(conn, doctor):
    assert assignments_repo.create_assignment(conn, doctor.user_id, "s", "x", "d")
    assert assignments_repo.create_assignment(conn, doctor.user_id, "s", "x", "d") is None


def test_날짜가_다르면_별도_할당이_된다(conn, doctor):
    assert assignments_repo.create_assignment(conn, doctor.user_id, "s", "x", "26.08.31")
    assert assignments_repo.create_assignment(conn, doctor.user_id, "s", "x", "26.09.01")


def test_전문의별_목록_조회(conn, doctor):
    assignments_repo.create_assignment(conn, doctor.user_id, "stu-1", "sess-1", "d")
    items = assignments_repo.list_assignments(conn, doctor_id=doctor.user_id)
    assert len(items) == 1
    assert items[0].doctor_name == "테스트 전문의"


def test_진행률은_완료턴_비율로_계산된다(conn, doctor):
    assignment = assignments_repo.create_assignment(conn, doctor.user_id, "s", "x", "d")
    evaluations_repo.sync_turns(conn, assignment.id, _turns(4))
    assignments_repo.update_total_turns(conn, assignment.id, 4)
    evaluations_repo.save_evaluation(
        conn, assignment.id, 0, doctor_score="4점", doctor_opinion="사유"
    )

    refreshed = assignments_repo.refresh_progress(conn, assignment.id)
    assert refreshed.completed_turns == 1
    assert refreshed.total_turns == 4
    assert refreshed.progress_pct == 25.0
    assert refreshed.status == STATUS_IN_PROGRESS


def test_점수만_있고_사유가_없으면_완료턴이_아니다(conn, doctor):
    assignment = assignments_repo.create_assignment(conn, doctor.user_id, "s", "x", "d")
    evaluations_repo.sync_turns(conn, assignment.id, _turns(2))
    evaluations_repo.save_evaluation(
        conn, assignment.id, 0, doctor_score="4점", doctor_opinion="   "
    )

    refreshed = assignments_repo.refresh_progress(conn, assignment.id)
    assert refreshed.completed_turns == 0
    assert refreshed.status == STATUS_PENDING


def test_최종완료_후_진행률_재계산이_상태를_되돌리지_않는다(conn, doctor):
    assignment = assignments_repo.create_assignment(conn, doctor.user_id, "s", "x", "d")
    assignments_repo.mark_completed(conn, assignment.id)

    refreshed = assignments_repo.refresh_progress(conn, assignment.id)
    assert refreshed.status == STATUS_COMPLETED
    assert refreshed.completed_at is not None


def test_reopen_은_잠금을_풀고_완료일시를_지운다(conn, doctor):
    assignment = assignments_repo.create_assignment(conn, doctor.user_id, "s", "x", "d")
    assignments_repo.mark_completed(conn, assignment.id)

    reopened = assignments_repo.reopen(conn, assignment.id)
    assert reopened.status != STATUS_COMPLETED
    assert reopened.completed_at is None


def test_할당_삭제시_평가도_함께_지워진다(conn, doctor):
    assignment = assignments_repo.create_assignment(conn, doctor.user_id, "s", "x", "d")
    evaluations_repo.sync_turns(conn, assignment.id, _turns(2))

    assignments_repo.delete_assignment(conn, assignment.id)
    assert evaluations_repo.list_evaluations(conn, assignment.id) == []


# --- evaluations ---------------------------------------------------------


def test_턴_동기화로_원본_QA_가_저장된다(conn, doctor):
    assignment = assignments_repo.create_assignment(conn, doctor.user_id, "s", "x", "d")
    evaluations_repo.sync_turns(conn, assignment.id, _turns(3))

    rows = evaluations_repo.list_evaluations(conn, assignment.id)
    assert [r.turn_index for r in rows] == [0, 1, 2]
    assert rows[1].ai_question == "질문1"
    assert rows[1].user_answer == "답변1"
    assert rows[1].evaluation_code == build_evaluation_code(assignment.id, 1)


def test_재동기화해도_전문의_입력은_보존된다(conn, doctor):
    """대화를 다시 불러와도 이미 입력한 점수·사유가 날아가면 안 된다."""
    assignment = assignments_repo.create_assignment(conn, doctor.user_id, "s", "x", "d")
    evaluations_repo.sync_turns(conn, assignment.id, _turns(2))
    evaluations_repo.save_evaluation(
        conn, assignment.id, 0, doctor_score="4점", doctor_opinion="판단 사유"
    )

    updated = [QATurn(0, "수정된 질문0", "수정된 답변0"), QATurn(1, "질문1", "답변1")]
    evaluations_repo.sync_turns(conn, assignment.id, updated)

    row = evaluations_repo.get_evaluation(conn, assignment.id, 0)
    assert row.ai_question == "수정된 질문0"  # 원본은 갱신되고
    assert row.doctor_score == "4점"  # 전문의 입력은 유지
    assert row.doctor_opinion == "판단 사유"


def test_평가_저장은_upsert_라_중복행이_생기지_않는다(conn, doctor):
    assignment = assignments_repo.create_assignment(conn, doctor.user_id, "s", "x", "d")
    evaluations_repo.save_evaluation(
        conn, assignment.id, 0, doctor_score="1점", doctor_opinion="첫번째"
    )
    evaluations_repo.save_evaluation(
        conn, assignment.id, 0, doctor_score="5점", doctor_opinion="수정본"
    )

    rows = evaluations_repo.list_evaluations(conn, assignment.id)
    assert len(rows) == 1
    assert rows[0].doctor_score == "5점"
    assert rows[0].doctor_opinion == "수정본"


def test_평가_저장은_원본_QA_를_덮어쓰지_않는다(conn, doctor):
    assignment = assignments_repo.create_assignment(conn, doctor.user_id, "s", "x", "d")
    evaluations_repo.sync_turns(conn, assignment.id, _turns(1))
    evaluations_repo.save_evaluation(
        conn, assignment.id, 0, doctor_score="4점", doctor_opinion="사유"
    )

    row = evaluations_repo.get_evaluation(conn, assignment.id, 0)
    assert row.ai_question == "질문0"
    assert row.user_answer == "답변0"


def test_CSV_원본은_COMPLETED_건만_포함한다(conn, doctor):
    done = assignments_repo.create_assignment(conn, doctor.user_id, "s1", "x", "d")
    todo = assignments_repo.create_assignment(conn, doctor.user_id, "s2", "x", "d")
    for assignment in (done, todo):
        evaluations_repo.sync_turns(conn, assignment.id, _turns(2))
        for i in range(2):
            evaluations_repo.save_evaluation(
                conn, assignment.id, i, doctor_score="4점", doctor_opinion="사유"
            )
    assignments_repo.mark_completed(conn, done.id)

    codes = {r["evaluation_code"] for r in evaluations_repo.list_completed_rows(conn)}
    assert build_evaluation_code(done.id, 0) in codes
    assert build_evaluation_code(todo.id, 0) not in codes
