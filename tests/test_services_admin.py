"""관리자 서비스 (SPEC.md §5 ADMIN, §6 Admin API)."""

from __future__ import annotations

import csv
import io

import pytest

from app.models import QATurn
from app.repositories import assignments as assignments_repo
from app.repositories import evaluations as evaluations_repo
from app.services import admin as admin_service

pytestmark = pytest.mark.db


# --- build_targets (순수 로직) --------------------------------------------


def test_학생과_세션의_모든_조합이_만들어진다():
    targets = admin_service.build_targets(["s1", "s2"], ["a", "b"], "26.08.31")
    assert targets == [
        ("s1", "a", "26.08.31"),
        ("s1", "b", "26.08.31"),
        ("s2", "a", "26.08.31"),
        ("s2", "b", "26.08.31"),
    ]


def test_세션을_안_고르면_학생당_한_건():
    assert admin_service.build_targets(["s1"], [], "26.08.31") == [("s1", "", "26.08.31")]


def test_공백과_중복은_정리된다():
    targets = admin_service.build_targets([" s1 ", "s1", ""], [" a "], " 26.08.31 ")
    assert targets == [("s1", "a", "26.08.31")]


# --- 할당 등록 -------------------------------------------------------------


def test_일괄_할당_등록(conn, doctor):
    targets = admin_service.build_targets(["s1", "s2"], ["sess"], "26.08.31")
    created, skipped = admin_service.create_assignments(conn, doctor.user_id, targets)

    assert len(created) == 2
    assert skipped == 0


def test_이미_있는_할당은_건너뛴다(conn, doctor):
    targets = admin_service.build_targets(["s1"], ["sess"], "d")
    admin_service.create_assignments(conn, doctor.user_id, targets)

    created, skipped = admin_service.create_assignments(conn, doctor.user_id, targets)
    assert created == []
    assert skipped == 1


def test_없는_전문의에게는_할당할_수_없다(conn):
    with pytest.raises(ValueError, match="존재하지 않는 전문의"):
        admin_service.create_assignments(conn, "없는사람", [("s", "x", "d")])


# --- 진도율 대시보드 --------------------------------------------------------


def test_전문의별_진행률이_턴_기준으로_집계된다(conn, doctor):
    a1 = assignments_repo.create_assignment(conn, doctor.user_id, "s1", "x", "d")
    a2 = assignments_repo.create_assignment(conn, doctor.user_id, "s2", "x", "d")
    for assignment in (a1, a2):
        evaluations_repo.sync_turns(conn, assignment.id, [QATurn(i, "q", "a") for i in range(2)])
        assignments_repo.update_total_turns(conn, assignment.id, 2)
    # a1 만 2턴 모두 입력 → 전체 4턴 중 2턴 완료
    for i in range(2):
        evaluations_repo.save_evaluation(
            conn, a1.id, i, doctor_score="4점", doctor_opinion="사유"
        )
    assignments_repo.refresh_progress(conn, a1.id)
    assignments_repo.mark_completed(conn, a1.id)
    assignments_repo.refresh_progress(conn, a2.id)

    row = next(r for r in admin_service.doctor_progress(conn) if r.doctor_id == doctor.user_id)
    assert row.assigned == 2
    assert row.completed == 1
    assert row.total_turns == 4
    assert row.completed_turns == 2
    assert row.progress_pct == 50.0


def test_할당이_없는_전문의도_대시보드에_0으로_나온다(conn, doctor):
    row = next(r for r in admin_service.doctor_progress(conn) if r.doctor_id == doctor.user_id)
    assert row.assigned == 0
    assert row.progress_pct == 0.0


# --- CSV 추출 --------------------------------------------------------------


def _read_csv(payload: bytes) -> list[list[str]]:
    return list(csv.reader(io.StringIO(payload.decode("utf-8-sig"))))


def test_CSV_헤더는_명세대로다(conn):
    rows = _read_csv(admin_service.export_csv(conn))
    assert rows[0] == [
        "평가 ID",
        "진단단계",
        "AI질문",
        "User Answer",
        "전문의점수/조치",
        "전문의판단이유",
        "담당전문의",
        "완료일시",
    ]


def test_CSV_는_엑셀_한글깨짐_방지_BOM_을_포함한다(conn):
    assert admin_service.export_csv(conn).startswith(b"\xef\xbb\xbf")


def test_완료된_평가만_CSV_에_들어간다(conn, doctor):
    assignment = assignments_repo.create_assignment(conn, doctor.user_id, "stu-9", "x", "d")
    evaluations_repo.sync_turns(conn, assignment.id, [QATurn(0, "AI 질문", "학생 답변")])
    evaluations_repo.save_evaluation(
        conn,
        assignment.id,
        0,
        doctor_score="Very (4점)",
        doctor_opinion="수면 문제 호소",
        scale_stage="1단계 KIDSCREEN-10",
    )

    # 아직 미완료 → CSV 에 없다
    codes = [r[0] for r in _read_csv(admin_service.export_csv(conn))[1:]]
    assert not any(str(assignment.id).zfill(3) in c for c in codes)

    assignments_repo.mark_completed(conn, assignment.id)
    rows = _read_csv(admin_service.export_csv(conn))
    row = next(r for r in rows[1:] if r[2] == "AI 질문")

    assert row[1] == "1단계 KIDSCREEN-10"
    assert row[3] == "학생 답변"
    assert row[4] == "Very (4점)"
    assert row[5] == "수면 문제 호소"
    assert row[6] == "테스트 전문의"
    assert row[7]  # 완료일시가 채워져 있다
