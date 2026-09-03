"""관리자 서비스 (SPEC.md §5 ADMIN, §6 Admin API)."""

from __future__ import annotations

import csv
import io
from dataclasses import dataclass
from datetime import datetime

import psycopg

from app.models import Assignment, STATUS_COMPLETED
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
        """턴 기준 진행률(%). 할당된 턴 수를 아직 모르면 건 수 기준으로 대체한다."""
        if self.total_turns > 0:
            return round(self.completed_turns / self.total_turns * 100, 1)
        if self.assigned > 0:
            return round(self.completed / self.assigned * 100, 1)
        return 0.0


def build_targets(
    student_ids: list[str], session_ids: list[str], chat_date: str
) -> list[tuple[str, str, str]]:
    """관리자가 선택한 학생/세션 조합을 (student_id, session_id, date) 목록으로 만든다.

    세션을 하나도 고르지 않으면 세션 지정 없이(빈 문자열) 학생당 1건을 만든다.
    중복 조합은 순서를 유지한 채 한 번만 남긴다.
    """
    students = [s.strip() for s in student_ids if s and s.strip()]
    sessions = [s.strip() for s in session_ids if s and s.strip()] or [""]
    date = (chat_date or "").strip()

    targets: list[tuple[str, str, str]] = []
    seen: set[tuple[str, str, str]] = set()
    for student in students:
        for session in sessions:
            key = (student, session, date)
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
