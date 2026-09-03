"""관리자 화면 (SPEC.md §5 ADMIN)."""

from __future__ import annotations

from datetime import datetime

import streamlit as st

from app.models import User
from app.repositories import users as users_repo
from app.services import admin as admin_service
from app.ui.common import connection, progress_bar

_STUDENT_POOL_KEY = "admin_student_pool"
_SESSION_POOL_KEY = "admin_session_pool"


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


def _render_id_picker(label: str, key: str, raw_key: str, placeholder: str) -> list[str]:
    """ID 를 입력해 후보로 만들고, 칩을 눌러 on/off 로 고른다 (SPEC.md §2 상세 1)."""
    raw = st.text_area(
        f"{label} 후보 입력 (한 줄에 하나 또는 콤마 구분)",
        key=raw_key,
        placeholder=placeholder,
        height=90,
    )
    candidates: list[str] = []
    for line in raw.replace(",", "\n").splitlines():
        value = line.strip()
        if value and value not in candidates:
            candidates.append(value)
    st.session_state[key] = candidates

    if not candidates:
        st.caption(f"{label}를 입력하면 아래에 선택 칩이 나타납니다.")
        return []
    return st.pills(
        f"{label} 선택 (클릭해서 on/off)",
        candidates,
        selection_mode="multi",
        key=f"{key}_selected",
    )


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

    col_student, col_session = st.columns(2)
    with col_student:
        selected_students = _render_id_picker(
            "학생 ID", _STUDENT_POOL_KEY, "admin_student_raw", "9612f93d33d0dfbe0df89ce3bea7a98b"
        )
    with col_session:
        selected_sessions = _render_id_picker(
            "세션 ID", _SESSION_POOL_KEY, "admin_session_raw", "sess-0001 (비우면 전체 세션)"
        )

    chat_date = st.text_input("조회 날짜 (YY.MM.DD)", value=datetime.now().strftime("%y.%m.%d"))

    targets = admin_service.build_targets(
        list(selected_students), list(selected_sessions), chat_date
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
