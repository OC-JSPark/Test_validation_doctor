"""전문의 서비스 (SPEC.md §5 Doctor, §6 Doctor API)."""

from __future__ import annotations

from dataclasses import dataclass

import psycopg

from app.external_api import ChatAPIClient
from app.models import UNSET, Assignment, Evaluation, _Unset, status_label
from app.repositories import assignments as assignments_repo
from app.repositories import evaluations as evaluations_repo


class AssignmentLocked(RuntimeError):
    """최종 완료된 할당은 수정할 수 없다."""


# 선택된 행 맨 앞에 붙는 마커. 배경색이 안 먹는 환경(복사·붙여넣기, 흑백 출력)
# 에서도 어느 행이 열려 있는지 알 수 있도록 글자로도 표시한다.
SELECTED_MARKER = "▶"


def resolve_selection(assignments: list[Assignment], previous: int | None) -> int | None:
    """작업 목록에서 실제로 열려 있는 할당 ID.

    표를 그리는 시점에는 selectbox 가 아직 그려지지 않아 세션 상태만으로는
    선택을 알 수 없다. selectbox 와 **같은 규칙**으로 미리 확정해야
    강조된 행과 실제로 열린 작업이 어긋나지 않는다.

    이전 선택이 목록에 없으면(할당이 삭제된 경우 등) 첫 번째로 되돌린다.
    """
    ids = [a.id for a in assignments]
    if not ids:
        return None
    return previous if previous in ids else ids[0]


def build_todo_rows(
    assignments: list[Assignment], selected_id: int | None
) -> list[dict]:
    """작업 목록 표의 행. 선택된 건 맨 앞에 마커를 단다.

    표시용 변환(빈 값 → `(전체)`)만 하고 Streamlit 에 의존하지 않는다.
    """
    return [
        {
            "": SELECTED_MARKER if a.id == selected_id else "",
            "ID": a.id,
            "학생 ID": a.student_id,
            "세션 ID": a.session_id or "(전체)",
            "날짜": a.chat_date or "(전체)",
            "진행": f"{a.completed_turns}/{a.total_turns}",
            "진행률(%)": a.progress_pct,
            "상태": status_label(a.status),
        }
        for a in assignments
    ]


@dataclass
class EvaluationSet:
    """한 세션의 전체 턴 + 평가 데이터."""

    assignment: Assignment
    evaluations: list[Evaluation]

    @property
    def evaluable(self) -> list[Evaluation]:
        """평가 대상 턴만. 학생 답변이 없는 턴(마지막 분석 레포트 등)은 뺀다."""
        return [e for e in self.evaluations if e.is_evaluable]

    @property
    def total_turns(self) -> int:
        """화면에 보이는 전체 턴 수 (레포트 턴 포함)."""
        return len(self.evaluations)

    @property
    def evaluable_turns(self) -> int:
        """평가해야 하는 턴 수."""
        return len(self.evaluable)

    @property
    def filled_turns(self) -> int:
        return sum(1 for e in self.evaluable if e.is_filled)

    @property
    def can_complete(self) -> bool:
        """평가 대상 턴이 모두 채워져야 [최종 완료] 가 활성화된다.

        학생 답변이 없는 턴은 전문의가 채울 것이 없으므로 요건에서 뺀다.
        """
        return self.evaluable_turns > 0 and self.filled_turns == self.evaluable_turns


def list_my_assignments(conn: psycopg.Connection, doctor_id: str) -> list[Assignment]:
    """GET /api/doctor/assignments — 내 작업 목록."""
    return assignments_repo.list_assignments(conn, doctor_id=doctor_id)


def resolve_ai_dates(
    conn: psycopg.Connection, assignment: Assignment, client: ChatAPIClient
) -> Assignment:
    """날짜 없이 만들어진 AI 테스트 할당의 조회 날짜를 채운다 (안전망).

    보통은 관리자가 할당할 때 활동 이력에서 날짜와 세션 ID 를 받아 오므로
    여기까지 오지 않는다. 활동 이력이 비어 날짜 없이 만들어진 할당이나,
    날짜 개념이 없던 시기에 만들어진 옛 할당을 위한 길이다.

    **이 과정은 전문의에게 드러나지 않는다.** 평가자가 "AI 가 만든 데이터"라고
    알면 판단이 달라질 수 있어, 화면은 일반 할당과 똑같이 보여야 한다.
    받은 날짜를 할당에 적어 두는 것도 그래서다 — 다음부터는 일반 할당과
    구별할 근거가 화면에 남지 않는다. CSV 의 '검사일자' 도 빈칸으로 남지 않는다.

    날짜가 이미 있으면 **API 를 부르지 않는다.** 덮어쓰면 저장된 평가와
    날짜가 어긋나고, 매번 부르면 dev 전용 경로를 공연히 두드리게 된다.
    실제 사용자 데이터 할당도 그대로 돌려준다.
    """
    if not assignment.is_ai_preview or assignment.chat_date:
        return assignment

    dates = client.fetch_ai_latest_date(assignment.student_id)
    if dates.chat_date_param and not assignment.chat_date:
        assignment = (
            assignments_repo.set_chat_date(conn, assignment.id, dates.chat_date_param)
            or assignment
        )
    return assignment


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

    AI 테스트 할당이면 대화를 가져오기 **전에** 최신 날짜를 먼저 확정한다
    (날짜 없이 조회하면 어느 날짜 대화가 올지 보장되지 않는다).
    전문의 화면은 그 차이를 드러내지 않는다 — `resolve_ai_dates` 참고.
    """
    assignment = assignments_repo.get_assignment(conn, assignment_id)
    if assignment is None:
        raise ValueError(f"할당을 찾을 수 없습니다: {assignment_id}")

    if refresh:
        assignment = resolve_ai_dates(conn, assignment, client)
        turns = client.fetch_turns(
            assignment.student_id,
            date=assignment.chat_date or None,
            session_id=assignment.session_id or None,
        )
        if turns:
            evaluations_repo.sync_turns(conn, assignment_id, turns)
            # 학생 답변이 있는 턴만 센다. 레포트 턴까지 세면 진행률이
            # 100% 에 닿지 못해 [최종 완료] 가 영영 열리지 않는다.
            evaluable = sum(1 for t in turns if (t.user_answer or "").strip())
            assignments_repo.update_total_turns(conn, assignment_id, evaluable)

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
    doctor_score: str | None | _Unset = UNSET,
    doctor_opinion: str | None | _Unset = UNSET,
    scale_stage: str | None | _Unset = UNSET,
) -> Assignment:
    """POST /api/doctor/evaluations/{assignmentId}/turn/{turnIndex}

    턴 이동 시 자동 저장(Upsert)에 쓰인다. 저장 후 진행률을 다시 계산한다.
    넘기지 않은 필드(UNSET)는 기존 값을 유지한다 — 자세한 내용은
    `evaluations_repo.save_evaluation` 참고.
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

    평가 대상 턴이 모두 채워지지 않았으면 거부한다
    (UI 버튼 비활성화의 서버측 방어선).
    """
    assignment = assignments_repo.refresh_progress(conn, assignment_id)
    if assignment is None:
        raise ValueError(f"할당을 찾을 수 없습니다: {assignment_id}")

    evaluations = evaluations_repo.list_evaluations(conn, assignment_id)
    if not evaluations:
        raise ValueError("평가할 턴이 없습니다. 대화 내용을 먼저 불러오세요.")

    # 학생 답변이 없는 턴(마지막 분석 레포트 등)은 채울 것이 없어 요건에서 뺀다.
    evaluable = [e for e in evaluations if e.is_evaluable]
    if not evaluable:
        raise ValueError(
            "평가할 수 있는 턴이 없습니다. 학생 답변이 있는 턴이 하나도 없습니다."
        )

    unfilled = [e.turn_index for e in evaluable if not e.is_filled]
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
