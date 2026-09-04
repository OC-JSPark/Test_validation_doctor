"""관리자 화면 (SPEC.md §5 ADMIN)."""

from __future__ import annotations

from datetime import datetime

import streamlit as st

from app import student_directory
from app.models import User
from app.repositories import users as users_repo
from app.services import admin as admin_service
from app.student_directory import StudentDirectoryError
from app.ui.common import connection, progress_bar

_SELECTED_STUDENTS_KEY = "admin_selected_students"


def render(user: User) -> None:
    st.title("관리자")
    tab_dashboard, tab_assign, tab_export = st.tabs(
        ["📊 진도율 대시보드", "🗂 평가 작업 할당", "⬇️ CSV 추출"]
    )
    with tab_dashboard:
        _render_dashboard()
    with tab_assign:
        _render_assign()
    with tab_export:
        _render_export()


# --- 진도율 대시보드 --------------------------------------------------------


def _render_dashboard() -> None:
    st.subheader("전문의별 진도율")
    with connection() as conn:
        progress = admin_service.doctor_progress(conn)
        assignments = admin_service.list_assignments(conn)

    if not progress:
        st.info("등록된 전문의가 없습니다. `uv run python -m scripts.init_db --seed` 로 계정을 만드세요.")
        return

    st.dataframe(
        [
            {
                "전문의": f"{row.doctor_name} ({row.doctor_id})",
                "할당 건수": row.assigned,
                "완료 건수": row.completed,
                "전체 턴": row.total_turns,
                "완료 턴": row.completed_turns,
                "진행률(%)": row.progress_pct,
            }
            for row in progress
        ],
        use_container_width=True,
        hide_index=True,
    )

    total_turns = sum(r.total_turns for r in progress)
    done_turns = sum(r.completed_turns for r in progress)
    overall = round(done_turns / total_turns * 100, 1) if total_turns else 0.0
    st.markdown("**전체 진행률**")
    progress_bar(overall)

    st.subheader("할당 현황")
    if not assignments:
        st.info("아직 할당된 작업이 없습니다.")
        return
    st.dataframe(
        [
            {
                "ID": a.id,
                "전문의": a.doctor_name,
                "학생 ID": a.student_id,
                "세션 ID": a.session_id or "(전체)",
                "날짜": a.chat_date or "(전체)",
                "진행": f"{a.completed_turns}/{a.total_turns}",
                "진행률(%)": a.progress_pct,
                "상태": a.status,
                "완료일시": a.completed_at.strftime("%Y-%m-%d %H:%M") if a.completed_at else "",
            }
            for a in assignments
        ],
        use_container_width=True,
        hide_index=True,
    )


# --- 작업 할당 -------------------------------------------------------------


def _pool_from_state(key: str) -> list[str]:
    return st.session_state.get(key, [])


def _selected_students() -> set[str]:
    """선택 상태는 체크박스 위젯이 아니라 여기에 보관한다.

    검색으로 걸러진 학생의 체크박스는 렌더링되지 않는데, 그때 위젯 상태가
    사라져도 선택이 풀리지 않도록 별도 집합으로 관리한다.
    """
    return st.session_state.setdefault(_SELECTED_STUDENTS_KEY, set())


def _toggle_student(student_id: str) -> None:
    selected = _selected_students()
    if st.session_state.get(f"stu_chk_{student_id}"):
        selected.add(student_id)
    else:
        selected.discard(student_id)


def _set_all(student_ids: list[str], checked: bool) -> None:
    selected = _selected_students()
    for student_id in student_ids:
        st.session_state[f"stu_chk_{student_id}"] = checked
        selected.add(student_id) if checked else selected.discard(student_id)


def _render_student_picker() -> list[str]:
    """학생 DB 에서 전체 명부를 읽어 스크롤 가능한 체크박스 목록으로 보여준다."""
    try:
        students = student_directory.list_students()
    except StudentDirectoryError as exc:
        st.error(str(exc))
        return []

    if not students:
        st.warning("학생 명부가 비어 있습니다.")
        return []

    query = st.text_input(
        "학생 검색", key="admin_student_query", placeholder="이름 · 학교 · 학생 ID"
    )
    visible = student_directory.filter_students(students, query)
    selected = _selected_students()

    col_all, col_none, col_count = st.columns([1, 1, 2])
    col_all.button(
        "전체 선택",
        use_container_width=True,
        on_click=_set_all,
        args=([s.student_id for s in visible], True),
        disabled=not visible,
    )
    col_none.button(
        "전체 해제",
        use_container_width=True,
        on_click=_set_all,
        args=([s.student_id for s in students], False),
        disabled=not selected,
    )
    col_count.markdown(
        f"전체 **{len(students)}명** · 검색결과 **{len(visible)}명** · 선택 **{len(selected)}명**"
    )

    if not visible:
        st.caption("검색 조건에 맞는 학생이 없습니다.")

    # 명부가 길어져도 화면이 밀리지 않도록 고정 높이 스크롤 영역에 담는다.
    with st.container(height=320, border=True):
        for student in visible:
            key = f"stu_chk_{student.student_id}"
            if key not in st.session_state:
                st.session_state[key] = student.student_id in selected
            st.checkbox(
                student.label,
                key=key,
                help=f"studentId: {student.student_id}",
                on_change=_toggle_student,
                args=(student.student_id,),
            )

    # 명부에 있는 학생만, 화면 순서대로 돌려준다.
    return [s.student_id for s in students if s.student_id in selected]


def _render_assign() -> None:
    st.subheader("평가 작업 할당")

    with connection() as conn:
        doctors = users_repo.list_doctors(conn)

    if not doctors:
        st.warning("전문의 계정이 없습니다. 먼저 계정을 생성하세요.")
        return

    doctor_id = st.selectbox(
        "담당 전문의",
        [d.user_id for d in doctors],
        format_func=lambda uid: next(
            f"{d.name} ({d.user_id})" for d in doctors if d.user_id == uid
        ),
    )

    st.markdown("#### 학생 선택")
    selected_students = _render_student_picker()

    col_session, col_date = st.columns(2)
    with col_session:
        session_id = st.text_input(
            "세션 ID (선택)",
            key="admin_session_id",
            placeholder="비우면 해당 날짜의 전체 세션",
        )
    with col_date:
        chat_date = st.text_input(
            "조회 날짜 (YY.MM.DD)", value=datetime.now().strftime("%y.%m.%d")
        )

    targets = admin_service.build_targets(
        list(selected_students), [session_id] if session_id else [], chat_date
    )
    st.caption(f"생성될 작업: **{len(targets)}건**")

    if st.button("작업 생성", type="primary", disabled=not targets):
        with connection() as conn:
            created, skipped = admin_service.create_assignments(conn, doctor_id, targets)
        if created:
            st.success(f"{len(created)}건을 할당했습니다.")
        if skipped:
            st.info(f"{skipped}건은 이미 같은 할당이 있어 건너뛰었습니다.")
        st.rerun()


# --- CSV 추출 --------------------------------------------------------------


def _render_export() -> None:
    st.subheader("완료 평가 CSV 추출")
    st.caption("상태가 COMPLETED 인 할당의 모든 턴이 포함됩니다.")

    with connection() as conn:
        payload = admin_service.export_csv(conn)

    line_count = max(payload.decode("utf-8-sig").count("\n") - 1, 0)
    if line_count == 0:
        st.info("아직 완료된 평가가 없습니다.")
    else:
        st.success(f"{line_count}개 턴이 추출 가능합니다.")

    st.download_button(
        "CSV 다운로드",
        data=payload,
        file_name=f"doctor_evaluations_{datetime.now():%Y%m%d_%H%M%S}.csv",
        mime="text/csv",
        disabled=line_count == 0,
    )
