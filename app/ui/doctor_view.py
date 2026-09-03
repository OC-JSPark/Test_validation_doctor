"""전문의 화면 — 턴별 Prev/Next 1:1 평가 (SPEC.md §5 Doctor)."""

from __future__ import annotations

import streamlit as st

from app.config import get_settings
from app.external_api import ExternalAPIError
from app.models import STATUS_COMPLETED, Assignment, User
from app.services import doctor as doctor_service
from app.services.doctor import AssignmentLocked, EvaluationSet
from app.ui.common import chat_client, connection, progress_bar

_SELECTED_KEY = "doctor_selected_assignment"
_TURN_KEY = "doctor_turn_index"


def render(user: User) -> None:
    st.title("전문의")
    with connection() as conn:
        assignments = doctor_service.list_my_assignments(conn, user.user_id)

    if not assignments:
        st.info("할당된 작업이 없습니다. 관리자에게 문의하세요.")
        return

    _render_todo_list(assignments)
    selected_id = st.session_state.get(_SELECTED_KEY)
    if selected_id is None:
        st.caption("위 목록에서 작업을 선택하면 평가 화면이 열립니다.")
        return

    evaluation_set = _load(selected_id)
    if evaluation_set is None:
        return
    _render_evaluation(evaluation_set)


# --- 작업 목록 -------------------------------------------------------------


def _render_todo_list(assignments: list[Assignment]) -> None:
    st.subheader("나의 작업 목록")
    st.dataframe(
        [
            {
                "ID": a.id,
                "학생 ID": a.student_id,
                "세션 ID": a.session_id or "(전체)",
                "날짜": a.chat_date or "(전체)",
                "진행": f"{a.completed_turns}/{a.total_turns}",
                "진행률(%)": a.progress_pct,
                "상태": a.status,
            }
            for a in assignments
        ],
        use_container_width=True,
        hide_index=True,
    )

    options = [a.id for a in assignments]
    labels = {
        a.id: f"#{a.id} · {a.student_id[:12]}… · {a.status} ({a.completed_turns}/{a.total_turns})"
        for a in assignments
    }
    previous = st.session_state.get(_SELECTED_KEY)
    selected = st.selectbox(
        "평가할 작업 선택",
        options,
        index=options.index(previous) if previous in options else 0,
        format_func=lambda aid: labels[aid],
    )
    if selected != previous:
        st.session_state[_SELECTED_KEY] = selected
        st.session_state[_TURN_KEY] = 0


# --- 데이터 로딩 -----------------------------------------------------------


def _load(assignment_id: int) -> EvaluationSet | None:
    """외부 API 로 대화를 새로 받아온다. 실패하면 저장된 턴만으로 진행한다."""
    refresh = st.session_state.pop("doctor_force_refresh", False) or not st.session_state.get(
        f"doctor_loaded_{assignment_id}", False
    )
    try:
        with connection() as conn:
            result = doctor_service.load_evaluation_set(
                conn, assignment_id, chat_client(), refresh=refresh
            )
        st.session_state[f"doctor_loaded_{assignment_id}"] = True
        return result
    except ExternalAPIError as exc:
        st.warning(f"외부 API 에서 대화를 가져오지 못했습니다: {exc}")
        st.caption("이미 저장된 턴만 표시합니다. 상단 [대화 다시 불러오기] 로 재시도할 수 있습니다.")
        with connection() as conn:
            return doctor_service.load_evaluation_set(
                conn, assignment_id, chat_client(), refresh=False
            )
    except ValueError as exc:
        st.error(str(exc))
        return None


# --- 평가 화면 -------------------------------------------------------------


NO_SCORE = "(미선택)"


def _autosave(assignment_id: int, turn_index: int) -> None:
    """위젯 변경/턴 이동 시 자동 저장 (SPEC.md §2 상세 4)."""
    score = st.session_state.get(f"score_{assignment_id}_{turn_index}")
    opinion = st.session_state.get(f"opinion_{assignment_id}_{turn_index}")
    stage = st.session_state.get(f"stage_{assignment_id}_{turn_index}")
    if score == NO_SCORE:
        score = None  # 미선택은 '입력 없음' 으로 저장해 완료 턴에서 제외한다
    try:
        with connection() as conn:
            doctor_service.save_turn(
                conn,
                assignment_id,
                turn_index,
                doctor_score=score,
                doctor_opinion=opinion,
                scale_stage=stage,
            )
    except AssignmentLocked:
        st.toast("최종 완료된 평가는 수정할 수 없습니다.")


def _move(assignment_id: int, turn_index: int, delta: int, last: int) -> None:
    _autosave(assignment_id, turn_index)
    st.session_state[_TURN_KEY] = min(max(turn_index + delta, 0), last)


def _render_evaluation(evaluation_set: EvaluationSet) -> None:
    assignment = evaluation_set.assignment
    evaluations = evaluation_set.evaluations
    settings = get_settings()

    st.divider()
    header_left, header_right = st.columns([3, 1])
    with header_right:
        if st.button("🔄 대화 다시 불러오기", use_container_width=True):
            st.session_state["doctor_force_refresh"] = True
            st.rerun()

    if not evaluations:
        st.warning(
            "이 세션에서 가져온 Q&A 턴이 없습니다. "
            "학생 ID / 세션 ID / 날짜가 올바른지 확인하세요."
        )
        return

    total = len(evaluations)
    turn_index = min(st.session_state.get(_TURN_KEY, 0), total - 1)
    st.session_state[_TURN_KEY] = turn_index
    current = evaluations[turn_index]
    locked = assignment.status == STATUS_COMPLETED

    # Header: 평가 ID, 진단 단계, 진행 턴 수
    with header_left:
        st.subheader(f"평가 {current.evaluation_code or '-'}")
        st.caption(
            f"학생 {assignment.student_id} · 세션 {assignment.session_id or '(전체)'} "
            f"· {assignment.chat_date or '(전체)'}"
        )
    st.markdown(f"**진행: {turn_index + 1} / {total} 턴**")
    progress_bar(evaluation_set.filled_turns / total * 100)

    if locked:
        st.success("최종 완료된 평가입니다 (수정 잠금).")

    stage_options = list(settings.scale_stages)
    stage_key = f"stage_{assignment.id}_{turn_index}"
    # 위젯 값은 세션 상태로 관리한다. 최초 렌더 때만 DB 값으로 채우고,
    # 이후에는 사용자가 입력한 값이 살아 있어야 한다.
    if stage_key not in st.session_state:
        st.session_state[stage_key] = (
            current.scale_stage if current.scale_stage in stage_options else stage_options[0]
        )
    st.selectbox(
        "진단 단계 (척도)",
        stage_options,
        key=stage_key,
        disabled=locked,
        on_change=_autosave,
        args=(assignment.id, turn_index),
    )

    # Body: Q&A(읽기 전용) + 평가 입력
    body_left, body_right = st.columns(2)
    with body_left:
        st.markdown("#### 🤖 AI 질문")
        st.info(current.ai_question or "(질문 없음)")
        st.markdown("#### 👤 학생 답변")
        st.success(current.user_answer or "(답변 없음)")

    with body_right:
        score_options = [NO_SCORE] + list(settings.score_options)
        score_key = f"score_{assignment.id}_{turn_index}"
        if score_key not in st.session_state:
            st.session_state[score_key] = (
                current.doctor_score if current.doctor_score in score_options else NO_SCORE
            )
        st.selectbox(
            "전문의 점수 / 조치",
            score_options,
            key=score_key,
            disabled=locked,
            on_change=_autosave,
            args=(assignment.id, turn_index),
        )

        opinion_key = f"opinion_{assignment.id}_{turn_index}"
        if opinion_key not in st.session_state:
            st.session_state[opinion_key] = current.doctor_opinion or ""
        st.text_area(
            "전문의 판단 이유",
            key=opinion_key,
            height=220,
            disabled=locked,
            on_change=_autosave,
            args=(assignment.id, turn_index),
        )

    # Footer: Prev / Next / 최종 완료
    st.divider()
    col_prev, col_next, col_save, col_done = st.columns([1, 1, 1, 2])
    col_prev.button(
        "⬅ 이전",
        use_container_width=True,
        disabled=turn_index == 0,
        on_click=_move,
        args=(assignment.id, turn_index, -1, total - 1),
    )
    col_next.button(
        "다음 ➡",
        use_container_width=True,
        disabled=turn_index >= total - 1,
        on_click=_move,
        args=(assignment.id, turn_index, 1, total - 1),
    )
    col_save.button(
        "💾 임시저장",
        use_container_width=True,
        disabled=locked,
        on_click=_autosave,
        args=(assignment.id, turn_index),
    )

    with col_done:
        if locked:
            if st.button("🔓 수정하기 (잠금 해제)", use_container_width=True):
                with connection() as conn:
                    doctor_service.reopen_assignment(conn, assignment.id)
                st.rerun()
        else:
            can_complete = evaluation_set.can_complete
            if st.button(
                "✅ 최종 완료",
                type="primary",
                use_container_width=True,
                disabled=not can_complete,
                help=None if can_complete else "모든 턴의 점수와 판단 이유를 입력해야 활성화됩니다.",
            ):
                try:
                    with connection() as conn:
                        doctor_service.complete_assignment(conn, assignment.id)
                except ValueError as exc:
                    st.error(str(exc))
                else:
                    st.rerun()

    remaining = total - evaluation_set.filled_turns
    if remaining and not locked:
        st.caption(f"미입력 턴 {remaining}개가 남아 있어 [최종 완료] 가 비활성화되어 있습니다.")
