"""관리자 화면 (SPEC.md §5 ADMIN)."""

from __future__ import annotations

from datetime import datetime

import streamlit as st

from app import session_directory, student_directory
from app.models import Assignment, ScaleSession, User
from app.repositories import users as users_repo
from app.services import admin as admin_service
from app.session_directory import SessionDirectoryError
from app.student_directory import StudentDirectoryError
from app.ui.common import connection, progress_bar

_SELECTED_STUDENTS_KEY = "admin_selected_students"
_FLASH_KEY = "admin_flash"


def _render_flash() -> None:
    """직전 실행에서 남긴 결과 메시지를 보여준다.

    `st.rerun()` 은 스크립트를 처음부터 다시 실행하므로, rerun 직전에 그린
    메시지는 화면에 남지 않는다. 그래서 결과를 세션에 담아 다음 실행에서 그린다.
    """
    flash = st.session_state.pop(_FLASH_KEY, None)
    if not flash:
        return
    if flash.get("created"):
        st.success(f"{flash['created']}건을 할당했습니다.")
    if flash.get("skipped"):
        st.info(f"{flash['skipped']}건은 이미 같은 할당이 있어 건너뛰었습니다.")
    if flash.get("deleted"):
        ids = ", ".join(str(i) for i in flash["deleted"])
        st.success(f"{len(flash['deleted'])}건을 삭제했습니다. (ID: {ids})")
    if flash.get("protected"):
        st.info(
            f"{len(flash['protected'])}건은 평가 결과가 있어 건너뛰었습니다. "
            "지우려면 확인 체크박스를 켜세요."
        )


def render(user: User) -> None:
    st.title("관리자")
    _render_flash()
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
                "진행률(%)": row.progress_pct,
                "완료 턴": row.completed_turns,
                "열어본 턴": row.total_turns,
            }
            for row in progress
        ],
        use_container_width=True,
        hide_index=True,
    )
    st.caption(
        "진행률 = 완료 건수 / 할당 건수. "
        "'열어본 턴' 은 전문의가 실제로 연 세션의 턴 수라, 아직 열지 않은 할당은 0 이다."
    )

    assigned = sum(r.assigned for r in progress)
    completed = sum(r.completed for r in progress)
    overall = round(completed / assigned * 100, 1) if assigned else 0.0
    st.markdown(f"**전체 진행률** — {completed} / {assigned}건")
    progress_bar(overall)

    st.subheader("할당 현황")
    if not assignments:
        st.info("아직 할당된 작업이 없습니다.")
        return
    _render_assignment_table(assignments)
    _render_delete(assignments)


def _render_assignment_table(assignments: list[Assignment]) -> None:
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


# --- 할당 삭제 -------------------------------------------------------------


def _render_delete(assignments: list[Assignment]) -> None:
    """할당 삭제. 평가 결과가 함께 지워지므로 확인 단계를 둔다."""
    with st.expander("🗑 할당 삭제"):
        labels = {
            a.id: (
                f"#{a.id} · {a.doctor_name} · {a.student_id[:12]}… · "
                f"{a.chat_date or '(전체)'} · {a.status} ({a.completed_turns}/{a.total_turns})"
            )
            for a in assignments
        }
        # 삭제 후에는 위젯을 새 키로 다시 만들어 선택을 비운다.
        # (이미 만들어진 위젯의 session_state 는 같은 실행 안에서 바꿀 수 없다.)
        nonce = st.session_state.get("admin_delete_nonce", 0)
        target_ids = st.multiselect(
            "삭제할 할당 선택",
            [a.id for a in assignments],
            format_func=lambda aid: labels[aid],
            key=f"admin_delete_targets_{nonce}",
        )
        if not target_ids:
            st.caption("삭제할 할당을 고르면 영향도가 표시됩니다.")
            return

        with connection() as conn:
            previews = admin_service.preview_deletion(conn, list(target_ids))

        at_risk = [p for p in previews if p.has_work]
        if at_risk:
            st.warning(
                f"선택한 {len(previews)}건 중 **{len(at_risk)}건**에 전문의가 입력한 평가가 "
                "있습니다. 삭제하면 해당 평가 결과도 함께 사라지며 되돌릴 수 없습니다."
            )
            for preview in at_risk:
                st.markdown(
                    f"- `#{preview.assignment.id}` {preview.assignment.doctor_name} — "
                    f"입력된 턴 **{preview.filled_turns}개**, 상태 `{preview.assignment.status}`"
                )
        else:
            st.info(f"선택한 {len(previews)}건 모두 입력된 평가가 없습니다.")

        force = st.checkbox(
            "평가 결과가 있는 할당도 함께 삭제합니다 (되돌릴 수 없음)",
            key="admin_delete_force",
            disabled=not at_risk,
        )

        if st.button("선택한 할당 삭제", type="primary", key="admin_delete_btn"):
            with connection() as conn:
                deleted, protected = admin_service.delete_assignments(
                    conn, list(target_ids), force=force
                )
            # rerun 이 화면을 다시 그리므로, 결과는 세션에 담아 다음 실행에서 보여준다.
            st.session_state[_FLASH_KEY] = {
                "deleted": deleted,
                "protected": [p.assignment.id for p in protected],
            }
            st.session_state["admin_delete_nonce"] = nonce + 1
            st.rerun()


# --- 작업 할당 -------------------------------------------------------------


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

    # 학생을 고르면 그 학생이 실시한 척도검사가 전부 할당 대상이 된다.
    # 관리자가 날짜를 따로 입력하지 않는다.
    targets: list[tuple[str, str, str]] = []
    if selected_students:
        try:
            sessions = session_directory.list_sessions(selected_students)
        except SessionDirectoryError as exc:
            st.error(str(exc))
            return
        targets = admin_service.build_targets(sessions)
        _render_session_summary(selected_students, sessions)

    st.caption(f"생성될 작업: **{len(targets)}건**")

    if st.button("작업 생성", type="primary", disabled=not targets):
        with connection() as conn:
            created, skipped = admin_service.create_assignments(conn, doctor_id, targets)
        # rerun 이 화면을 다시 그리므로, 결과는 세션에 담아 다음 실행에서 보여준다.
        st.session_state[_FLASH_KEY] = {"created": len(created), "skipped": skipped}
        st.rerun()


def _render_session_summary(student_ids: list[str], sessions: list[ScaleSession]) -> None:
    """학생별 척도검사 건수와, 검사 이력이 없는 학생을 알려준다."""
    grouped = session_directory.group_by_student(sessions)
    empty = [sid for sid in student_ids if sid not in grouped]

    st.dataframe(
        [
            {
                "학생 ID": student_id,
                "척도검사 수": len(grouped.get(student_id, [])),
                "최초 검사일": min(s.session_date for s in grouped[student_id]).isoformat()
                if student_id in grouped
                else "-",
                "최종 검사일": max(s.session_date for s in grouped[student_id]).isoformat()
                if student_id in grouped
                else "-",
            }
            for student_id in student_ids
        ],
        use_container_width=True,
        hide_index=True,
    )
    if empty:
        st.warning(
            f"{len(empty)}명은 척도검사 이력이 없어 할당되지 않습니다: "
            + ", ".join(sid[:12] + "…" for sid in empty)
        )


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
