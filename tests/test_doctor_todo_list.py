"""전문의 작업 목록 — 선택한 작업 강조 표시.

목록이 길어지면 지금 평가 중인 건이 표의 어디인지 눈으로 찾기 어렵다.
선택된 행에 마커(▶)와 배경색을 넣어 한눈에 들어오게 한다.
"""

from __future__ import annotations

from app.models import (
    STATUS_COMPLETED,
    STATUS_IN_PROGRESS,
    STATUS_PENDING,
    Assignment,
)
from app.services.doctor import (
    SELECTED_MARKER,
    build_todo_rows,
    resolve_selection,
)
from app.ui.doctor_view import _highlight_selected


def _assignment(assignment_id: int, status: str = STATUS_PENDING) -> Assignment:
    return Assignment(
        id=assignment_id,
        doctor_id="doctor01",
        student_id=f"student-{assignment_id}",
        session_id=f"sess-{assignment_id}",
        chat_date="26.08.31",
        total_turns=10,
        completed_turns=3,
        status=status,
    )


# --- 선택 확정 --------------------------------------------------------------


def test_이전_선택이_목록에_있으면_그대로_쓴다():
    assignments = [_assignment(1), _assignment(2), _assignment(3)]

    assert resolve_selection(assignments, 2) == 2


def test_선택한_적이_없으면_첫_번째_작업():
    """selectbox 의 기본값과 같아야 표의 강조와 실제 열린 작업이 어긋나지 않는다."""
    assignments = [_assignment(7), _assignment(8)]

    assert resolve_selection(assignments, None) == 7


def test_이전_선택이_사라졌으면_첫_번째로_되돌린다():
    """관리자가 할당을 삭제하면 세션에 남은 ID 가 목록에 없을 수 있다."""
    assignments = [_assignment(10), _assignment(11)]

    assert resolve_selection(assignments, 999) == 10


def test_작업이_없으면_선택도_없다():
    assert resolve_selection([], None) is None
    assert resolve_selection([], 5) is None


# --- 표의 행 ---------------------------------------------------------------


def test_선택한_행에만_마커가_붙는다():
    assignments = [_assignment(1), _assignment(2), _assignment(3)]

    rows = build_todo_rows(assignments, selected_id=2)

    assert [r[""] for r in rows] == ["", SELECTED_MARKER, ""]


def test_아무것도_선택되지_않으면_마커가_없다():
    rows = build_todo_rows([_assignment(1), _assignment(2)], selected_id=None)

    assert all(r[""] == "" for r in rows)


def test_마커가_맨_앞_열이다():
    """마커가 뒤쪽에 있으면 가로 스크롤에 묻혀 강조 효과가 없다."""
    rows = build_todo_rows([_assignment(1)], selected_id=1)

    assert list(rows[0])[0] == ""


def test_기존_열이_그대로_유지된다():
    rows = build_todo_rows([_assignment(42, STATUS_IN_PROGRESS)], selected_id=42)

    row = rows[0]
    assert row["ID"] == 42
    assert row["학생 ID"] == "student-42"
    assert row["세션 ID"] == "sess-42"
    assert row["날짜"] == "26.08.31"
    assert row["진행"] == "3/10"
    assert row["진행률(%)"] == 30.0
    assert row["상태"] == "진행중"  # 저장값은 IN_PROGRESS, 화면만 한글


def test_비어있는_세션과_날짜는_전체로_표시된다():
    assignment = _assignment(1)
    assignment.session_id = ""
    assignment.chat_date = ""

    row = build_todo_rows([assignment], selected_id=1)[0]

    assert row["세션 ID"] == "(전체)"
    assert row["날짜"] == "(전체)"


def test_행_순서가_할당_순서와_같다():
    """강조 행 번호와 화면 순서가 어긋나면 엉뚱한 행에 색이 칠해진다."""
    assignments = [_assignment(5), _assignment(3), _assignment(9)]

    rows = build_todo_rows(assignments, selected_id=3)

    assert [r["ID"] for r in rows] == [5, 3, 9]
    assert rows[1][""] == SELECTED_MARKER


def test_완료된_작업도_선택하면_강조된다():
    rows = build_todo_rows([_assignment(1, STATUS_COMPLETED)], selected_id=1)

    assert rows[0][""] == SELECTED_MARKER
    assert rows[0]["상태"] == "완료"


def test_상태가_한글로_보인다():
    """전문의 화면에는 PENDING 같은 영문 저장값이 보이면 안 된다."""
    assignments = [
        _assignment(1, STATUS_PENDING),
        _assignment(2, STATUS_IN_PROGRESS),
        _assignment(3, STATUS_COMPLETED),
    ]

    rows = build_todo_rows(assignments, selected_id=None)

    assert [r["상태"] for r in rows] == ["시작전", "진행중", "완료"]


# --- 표 서식 ---------------------------------------------------------------


def test_선택한_행_전체에_배경색이_칠해진다():
    """한 칸만 칠하면 눈에 띄지 않는다. 행 전체가 칠해져야 한다."""
    rows = build_todo_rows([_assignment(1), _assignment(2)], selected_id=2)

    html = _highlight_selected(rows, selected_row=1).to_html()
    # pandas 는 같은 규칙을 쓰는 칸들을 한 선택자로 묶는다.
    rule = html.split("background-color: rgba(255, 184, 0, 0.28)")[0]

    assert all(f"row1_col{i}" in rule for i in range(len(rows[0])))
    assert "row0_col" not in rule  # 선택되지 않은 행은 칠하지 않는다


def test_선택이_없으면_배경색도_없다():
    rows = build_todo_rows([_assignment(1)], selected_id=None)

    assert "background-color" not in _highlight_selected(rows, None).to_html()


def test_진행률은_소수_한_자리로_보인다():
    """Styler 기본 서식은 `0.000000` 처럼 나온다. 표에서 읽기 어렵다."""
    rows = build_todo_rows([_assignment(1)], selected_id=1)

    html = _highlight_selected(rows, selected_row=0).to_html()

    assert ">30.0<" in html
    assert "30.000000" not in html


def test_선택이_없어도_진행률_서식은_유지된다():
    rows = build_todo_rows([_assignment(1)], selected_id=None)

    html = _highlight_selected(rows, None).to_html()

    assert ">30.0<" in html
    assert "30.000000" not in html
