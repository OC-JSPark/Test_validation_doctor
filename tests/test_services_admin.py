"""관리자 서비스 (SPEC.md §5 ADMIN, §6 Admin API)."""

from __future__ import annotations

import csv
import io
from datetime import date

import pytest

from app.models import QATurn, ScaleSession
from app.repositories import assignments as assignments_repo
from app.repositories import evaluations as evaluations_repo
from app.services import admin as admin_service

pytestmark = pytest.mark.db


# --- build_targets (순수 로직) --------------------------------------------


def test_척도검사마다_할당_대상이_하나씩_만들어진다():
    """학생을 고르면 그 학생이 실시한 검사가 전부 대상이 된다."""
    sessions = [
        ScaleSession("s1", "sess-a", date(2026, 8, 6)),
        ScaleSession("s1", "sess-b", date(2026, 8, 21)),
        ScaleSession("s2", "sess-c", date(2026, 8, 25)),
    ]

    assert admin_service.build_targets(sessions) == [
        ("s1", "sess-a", "26.08.06"),
        ("s1", "sess-b", "26.08.21"),
        ("s2", "sess-c", "26.08.25"),
    ]


def test_검사일이_외부API_날짜형식으로_변환된다():
    session = ScaleSession("s1", "sess-a", date(2026, 9, 4))
    assert session.chat_date == "26.09.04"
    assert admin_service.build_targets([session])[0][2] == "26.09.04"


def test_중복_검사는_한_번만_남는다():
    same = ScaleSession("s1", "sess-a", date(2026, 8, 6))
    assert len(admin_service.build_targets([same, same])) == 1


def test_검사가_없으면_대상도_없다():
    assert admin_service.build_targets([]) == []


# --- 할당 등록 -------------------------------------------------------------


def test_일괄_할당_등록(conn, doctor):
    targets = admin_service.build_targets(
        [
            ScaleSession("s1", "sess-a", date(2026, 8, 31)),
            ScaleSession("s2", "sess-b", date(2026, 8, 31)),
        ]
    )
    created, skipped = admin_service.create_assignments(conn, doctor.user_id, targets)

    assert len(created) == 2
    assert skipped == 0


def test_이미_있는_할당은_건너뛴다(conn, doctor):
    targets = admin_service.build_targets([ScaleSession("s1", "sess", date(2026, 8, 31))])
    admin_service.create_assignments(conn, doctor.user_id, targets)

    created, skipped = admin_service.create_assignments(conn, doctor.user_id, targets)
    assert created == []
    assert skipped == 1


def test_없는_전문의에게는_할당할_수_없다(conn):
    with pytest.raises(ValueError, match="존재하지 않는 전문의"):
        admin_service.create_assignments(conn, "없는사람", [("s", "x", "d")])


# --- 진도율 대시보드 --------------------------------------------------------


def test_아직_열지_않은_할당이_있어도_진행률이_부풀지_않는다(conn, doctor):
    """실제 화면에서 발견된 회귀.

    턴 기준으로 계산하면, 아직 열지 않아 total_turns=0 인 할당이 분모에서
    빠져 39건 중 1건을 끝낸 전문의가 100% 로 보였다.
    """
    done = assignments_repo.create_assignment(conn, doctor.user_id, "s1", "x", "d")
    evaluations_repo.sync_turns(conn, done.id, [QATurn(0, "q", "a")])
    assignments_repo.update_total_turns(conn, done.id, 1)
    evaluations_repo.save_evaluation(
        conn, done.id, 0, doctor_score="4점", doctor_opinion="사유"
    )
    assignments_repo.refresh_progress(conn, done.id)
    assignments_repo.mark_completed(conn, done.id)
    # 아직 열지 않은 할당 3건 (total_turns = 0)
    for i in range(3):
        assignments_repo.create_assignment(conn, doctor.user_id, f"unopened{i}", "x", "d")

    row = next(r for r in admin_service.doctor_progress(conn) if r.doctor_id == doctor.user_id)

    assert row.assigned == 4
    assert row.completed == 1
    assert row.progress_pct == 25.0  # 건 기준. 턴 기준이면 100.0 이 나왔다
    assert row.turn_progress_pct == 100.0  # 열어본 할당에 한정한 보조 지표


def test_전문의별_진행률이_건_기준으로_집계된다(conn, doctor):
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


# --- 할당 삭제 -------------------------------------------------------------


def test_입력이_없는_할당은_바로_삭제된다(conn, doctor):
    assignment = assignments_repo.create_assignment(conn, doctor.user_id, "s1", "x", "d")
    evaluations_repo.sync_turns(conn, assignment.id, [QATurn(0, "q", "a")])

    deleted, protected = admin_service.delete_assignments(conn, [assignment.id])

    assert deleted == [assignment.id]
    assert protected == []
    assert assignments_repo.get_assignment(conn, assignment.id) is None
    # 원본 Q&A 행도 함께 지워진다
    assert evaluations_repo.list_evaluations(conn, assignment.id) == []


def test_전문의_입력이_있으면_기본적으로_보호된다(conn, doctor):
    """실수로 평가 결과를 날리지 않도록 force 없이는 건너뛴다."""
    assignment = assignments_repo.create_assignment(conn, doctor.user_id, "s1", "x", "d")
    evaluations_repo.sync_turns(conn, assignment.id, [QATurn(0, "q", "a")])
    evaluations_repo.save_evaluation(
        conn, assignment.id, 0, doctor_score="4점", doctor_opinion="사유"
    )

    deleted, protected = admin_service.delete_assignments(conn, [assignment.id])

    assert deleted == []
    assert [p.assignment.id for p in protected] == [assignment.id]
    assert protected[0].filled_turns == 1
    assert assignments_repo.get_assignment(conn, assignment.id) is not None


def test_force_를_주면_평가가_있어도_삭제된다(conn, doctor):
    assignment = assignments_repo.create_assignment(conn, doctor.user_id, "s1", "x", "d")
    evaluations_repo.sync_turns(conn, assignment.id, [QATurn(0, "q", "a")])
    evaluations_repo.save_evaluation(
        conn, assignment.id, 0, doctor_score="4점", doctor_opinion="사유"
    )

    deleted, protected = admin_service.delete_assignments(conn, [assignment.id], force=True)

    assert deleted == [assignment.id]
    assert protected == []
    assert assignments_repo.get_assignment(conn, assignment.id) is None


def test_완료된_할당도_보호_대상이다(conn, doctor):
    assignment = assignments_repo.create_assignment(conn, doctor.user_id, "s1", "x", "d")
    assignments_repo.mark_completed(conn, assignment.id)

    _, protected = admin_service.delete_assignments(conn, [assignment.id])

    assert [p.assignment.id for p in protected] == [assignment.id]


def test_보호된_건과_삭제된_건이_함께_처리된다(conn, doctor):
    clean = assignments_repo.create_assignment(conn, doctor.user_id, "s1", "x", "d")
    dirty = assignments_repo.create_assignment(conn, doctor.user_id, "s2", "x", "d")
    evaluations_repo.save_evaluation(
        conn, dirty.id, 0, doctor_score="4점", doctor_opinion="사유"
    )

    deleted, protected = admin_service.delete_assignments(conn, [clean.id, dirty.id])

    assert deleted == [clean.id]
    assert [p.assignment.id for p in protected] == [dirty.id]


def test_없는_ID_는_조용히_무시된다(conn, doctor):
    assignment = assignments_repo.create_assignment(conn, doctor.user_id, "s1", "x", "d")

    deleted, protected = admin_service.delete_assignments(conn, [assignment.id, 999_999])

    assert deleted == [assignment.id]
    assert protected == []


def test_삭제_미리보기가_입력된_턴_수를_알려준다(conn, doctor):
    assignment = assignments_repo.create_assignment(conn, doctor.user_id, "s1", "x", "d")
    evaluations_repo.sync_turns(conn, assignment.id, [QATurn(i, "q", "a") for i in range(3)])
    for i in range(2):
        evaluations_repo.save_evaluation(
            conn, assignment.id, i, doctor_score="4점", doctor_opinion="사유"
        )

    preview = admin_service.preview_deletion(conn, [assignment.id])[0]

    assert preview.filled_turns == 2
    assert preview.has_work is True


def test_삭제된_할당은_CSV_에서도_사라진다(conn, doctor):
    assignment = assignments_repo.create_assignment(conn, doctor.user_id, "stu-del", "x", "d")
    evaluations_repo.sync_turns(conn, assignment.id, [QATurn(0, "삭제될 질문", "답변")])
    evaluations_repo.save_evaluation(
        conn, assignment.id, 0, doctor_score="4점", doctor_opinion="사유"
    )
    assignments_repo.mark_completed(conn, assignment.id)
    assert "삭제될 질문" in admin_service.export_csv(conn).decode("utf-8-sig")

    admin_service.delete_assignments(conn, [assignment.id], force=True)

    assert "삭제될 질문" not in admin_service.export_csv(conn).decode("utf-8-sig")


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
