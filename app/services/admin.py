"""관리자 서비스 (SPEC.md §5 ADMIN, §6 Admin API)."""

from __future__ import annotations

import csv
import io
from dataclasses import dataclass
from datetime import datetime

import psycopg

from app.models import Assignment, ScaleSession, STATUS_COMPLETED
from app.repositories import assignments as assignments_repo
from app.repositories import evaluations as evaluations_repo
from app.repositories import users as users_repo

CSV_HEADERS = [
    "평가 ID",
    "진단단계",
    "AI질문",
    "User Answer",
    "전문의점수/조치",
    "전문의판단이유",
    "담당전문의",
    "완료일시",
]


@dataclass(frozen=True)
class DoctorProgress:
    """전문의 1명의 진도 요약."""

    doctor_id: str
    doctor_name: str
    assigned: int
    completed: int
    total_turns: int
    completed_turns: int

    @property
    def progress_pct(self) -> float:
        """진행률(%) = 완료 건수 / 할당 건수 (SPEC.md §5).

        턴 기준으로 계산하면 안 된다. 할당의 전체 턴 수는 전문의가 그 세션을
        처음 열어 외부 API 를 호출해야 알 수 있어서, 아직 열지 않은 할당은
        `total_turns = 0` 이다. 턴 기준으로 나누면 열어본 할당만 분모에 들어가
        39건 중 1건을 끝낸 전문의가 100% 로 보인다.
        """
        if self.assigned <= 0:
            return 0.0
        return round(self.completed / self.assigned * 100, 1)

    @property
    def turn_progress_pct(self) -> float:
        """열어본 할당에 한정한 턴 기준 진행률(%). 참고용 보조 지표."""
        if self.total_turns <= 0:
            return 0.0
        return round(self.completed_turns / self.total_turns * 100, 1)


def build_targets(sessions: list[ScaleSession]) -> list[tuple[str, str, str]]:
    """척도검사 목록을 (student_id, session_id, chat_date) 할당 대상으로 바꾼다.

    학생을 고르면 그 학생이 실시한 척도검사가 **전부** 할당 대상이 되므로,
    관리자가 날짜를 따로 입력하지 않는다. 날짜는 각 검사 실시일에서 나온다.

    중복 조합은 순서를 유지한 채 한 번만 남긴다.
    """
    targets: list[tuple[str, str, str]] = []
    seen: set[tuple[str, str, str]] = set()
    for session in sessions:
        key = (session.student_id, session.session_id, session.chat_date)
        if key not in seen:
            seen.add(key)
            targets.append(key)
    return targets


def create_assignments(
    conn: psycopg.Connection, doctor_id: str, targets: list[tuple[str, str, str]]
) -> tuple[list[Assignment], int]:
    """POST /api/admin/assignments — 일괄 등록.

    (생성된 할당 목록, 중복으로 건너뛴 건수) 를 돌려준다.
    """
    if users_repo.get_user(conn, doctor_id) is None:
        raise ValueError(f"존재하지 않는 전문의입니다: {doctor_id}")

    created: list[Assignment] = []
    skipped = 0
    for student_id, session_id, chat_date in targets:
        assignment = assignments_repo.create_assignment(
            conn, doctor_id, student_id, session_id, chat_date
        )
        if assignment is None:
            skipped += 1
        else:
            created.append(assignment)
    return created, skipped


def list_assignments(conn: psycopg.Connection) -> list[Assignment]:
    """GET /api/admin/assignments — 전체 할당 현황."""
    return assignments_repo.list_assignments(conn)


def doctor_progress(conn: psycopg.Connection) -> list[DoctorProgress]:
    """전문의별 할당/완료 건수와 진행률 (진도율 대시보드)."""
    all_assignments = assignments_repo.list_assignments(conn)
    by_doctor: dict[str, list[Assignment]] = {}
    for assignment in all_assignments:
        by_doctor.setdefault(assignment.doctor_id, []).append(assignment)

    rows: list[DoctorProgress] = []
    for doctor in users_repo.list_doctors(conn):
        items = by_doctor.get(doctor.user_id, [])
        rows.append(
            DoctorProgress(
                doctor_id=doctor.user_id,
                doctor_name=doctor.name,
                assigned=len(items),
                completed=sum(1 for a in items if a.status == STATUS_COMPLETED),
                total_turns=sum(a.total_turns for a in items),
                completed_turns=sum(a.completed_turns for a in items),
            )
        )
    return rows


@dataclass(frozen=True)
class DeletionPreview:
    """삭제 전 영향도. 전문의가 이미 입력한 평가가 함께 지워지는지 알려준다."""

    assignment: Assignment
    filled_turns: int

    @property
    def has_work(self) -> bool:
        """지우면 전문의 입력이 사라지는 건인지."""
        return self.filled_turns > 0 or self.assignment.status == STATUS_COMPLETED


def preview_deletion(
    conn: psycopg.Connection, assignment_ids: list[int]
) -> list[DeletionPreview]:
    """삭제 대상들의 영향도를 미리 계산한다 (UI 확인 문구용)."""
    previews: list[DeletionPreview] = []
    for assignment_id in assignment_ids:
        assignment = assignments_repo.get_assignment(conn, assignment_id)
        if assignment is None:
            continue
        filled = sum(
            1 for e in evaluations_repo.list_evaluations(conn, assignment_id) if e.is_filled
        )
        previews.append(DeletionPreview(assignment=assignment, filled_turns=filled))
    return previews


def delete_assignments(
    conn: psycopg.Connection, assignment_ids: list[int], *, force: bool = False
) -> tuple[list[int], list[DeletionPreview]]:
    """할당 삭제. 평가 결과(doctor_evaluations)도 함께 지워진다.

    전문의가 이미 입력한 건은 기본적으로 건너뛴다. 실수로 평가 결과를
    날리지 않도록, 지우려면 `force=True` 를 명시해야 한다.

    (삭제한 ID 목록, 보호되어 건너뛴 항목) 을 돌려준다.
    """
    deleted: list[int] = []
    protected: list[DeletionPreview] = []

    for preview in preview_deletion(conn, assignment_ids):
        if preview.has_work and not force:
            protected.append(preview)
            continue
        assignments_repo.delete_assignment(conn, preview.assignment.id)
        deleted.append(preview.assignment.id)

    return deleted, protected


def _format_datetime(value: datetime | None) -> str:
    return value.strftime("%Y-%m-%d %H:%M:%S") if value else ""


def export_csv(conn: psycopg.Connection) -> bytes:
    """GET /api/admin/export/csv — 완료된 평가 데이터를 CSV 바이트로.

    Excel 에서 한글이 깨지지 않도록 UTF-8 BOM 을 붙인다.
    """
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(CSV_HEADERS)
    for row in evaluations_repo.list_completed_rows(conn):
        writer.writerow(
            [
                row["evaluation_code"] or "",
                row["scale_stage"] or "",
                row["ai_question"] or "",
                row["user_answer"] or "",
                row["doctor_score"] or "",
                row["doctor_opinion"] or "",
                row["doctor_name"] or "",
                _format_datetime(row["completed_at"]),
            ]
        )
    return buffer.getvalue().encode("utf-8-sig")
