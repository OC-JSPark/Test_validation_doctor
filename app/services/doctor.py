"""전문의 서비스 (SPEC.md §5 Doctor, §6 Doctor API)."""

from __future__ import annotations

from dataclasses import dataclass

import psycopg

from app.external_api import ChatAPIClient
from app.models import Assignment, Evaluation
from app.repositories import assignments as assignments_repo
from app.repositories import evaluations as evaluations_repo


class AssignmentLocked(RuntimeError):
    """최종 완료된 할당은 수정할 수 없다."""


@dataclass
class EvaluationSet:
    """한 세션의 전체 턴 + 평가 데이터."""

    assignment: Assignment
    evaluations: list[Evaluation]

    @property
    def total_turns(self) -> int:
        return len(self.evaluations)

    @property
    def filled_turns(self) -> int:
        return sum(1 for e in self.evaluations if e.is_filled)

    @property
    def can_complete(self) -> bool:
        """모든 턴의 점수/판단 이유가 채워져야 [최종 완료] 가 활성화된다."""
        return self.total_turns > 0 and self.filled_turns == self.total_turns


def list_my_assignments(conn: psycopg.Connection, doctor_id: str) -> list[Assignment]:
    """GET /api/doctor/assignments — 내 작업 목록."""
    return assignments_repo.list_assignments(conn, doctor_id=doctor_id)


def load_evaluation_set(
    conn: psycopg.Connection,
    assignment_id: int,
    client: ChatAPIClient,
    *,
    refresh: bool = True,
) -> EvaluationSet:
    """GET /api/doctor/evaluations/{assignmentId}

    `refresh=True` 면 외부 API 에서 대화를 다시 가져와 턴을 동기화한다.
    이미 저장된 전문의 입력은 유지된다.
    """
    assignment = assignments_repo.get_assignment(conn, assignment_id)
    if assignment is None:
        raise ValueError(f"할당을 찾을 수 없습니다: {assignment_id}")

    if refresh:
        turns = client.fetch_turns(
            assignment.student_id,
            date=assignment.chat_date or None,
            session_id=assignment.session_id or None,
        )
        if turns:
            evaluations_repo.sync_turns(conn, assignment_id, turns)
            assignments_repo.update_total_turns(conn, assignment_id, len(turns))

    assignment = assignments_repo.refresh_progress(conn, assignment_id) or assignment
    return EvaluationSet(
        assignment=assignment,
        evaluations=evaluations_repo.list_evaluations(conn, assignment_id),
    )


def save_turn(
    conn: psycopg.Connection,
    assignment_id: int,
    turn_index: int,
    *,
    doctor_score: str | None,
    doctor_opinion: str | None,
    scale_stage: str | None = None,
) -> Assignment:
    """POST /api/doctor/evaluations/{assignmentId}/turn/{turnIndex}

    턴 이동 시 자동 저장(Upsert)에 쓰인다. 저장 후 진행률을 다시 계산한다.
    """
    assignment = assignments_repo.get_assignment(conn, assignment_id)
    if assignment is None:
        raise ValueError(f"할당을 찾을 수 없습니다: {assignment_id}")
    if assignment.is_completed:
        raise AssignmentLocked("최종 완료된 평가는 수정할 수 없습니다.")

    evaluations_repo.save_evaluation(
        conn,
        assignment_id,
        turn_index,
        doctor_score=doctor_score,
        doctor_opinion=doctor_opinion,
        scale_stage=scale_stage,
    )
    return assignments_repo.refresh_progress(conn, assignment_id) or assignment


def complete_assignment(conn: psycopg.Connection, assignment_id: int) -> Assignment:
    """PATCH /api/doctor/assignments/{assignmentId}/complete

    모든 턴이 채워지지 않았으면 거부한다 (UI 버튼 비활성화의 서버측 방어선).
    """
    assignment = assignments_repo.refresh_progress(conn, assignment_id)
    if assignment is None:
        raise ValueError(f"할당을 찾을 수 없습니다: {assignment_id}")

    evaluations = evaluations_repo.list_evaluations(conn, assignment_id)
    if not evaluations:
        raise ValueError("평가할 턴이 없습니다. 대화 내용을 먼저 불러오세요.")

    unfilled = [e.turn_index for e in evaluations if not e.is_filled]
    if unfilled:
        preview = ", ".join(str(i + 1) for i in unfilled[:5])
        raise ValueError(f"아직 입력되지 않은 턴이 있습니다 (턴 {preview} ...).")

    return assignments_repo.mark_completed(conn, assignment_id) or assignment


def reopen_assignment(conn: psycopg.Connection, assignment_id: int) -> Assignment:
    """최종 완료 건의 수정 잠금을 해제한다 (SPEC 외 추가 기능)."""
    assignment = assignments_repo.reopen(conn, assignment_id)
    if assignment is None:
        raise ValueError(f"할당을 찾을 수 없습니다: {assignment_id}")
    return assignment
