"""AIMIE Kids 전문의 평가 시스템 — Streamlit 진입점.

    uv run streamlit run Test_validation_doctor.py

아키텍처 (SPEC.md §1):
- 학생 대화 내역은 외부 API 로만 읽는다 (기존 서비스 DB 직접 접근 금지).
- 평가·할당·계정 데이터는 신규 로컬 DB(validation_db) 에만 저장한다.
"""

from __future__ import annotations

import streamlit as st

from app.ui import admin_view, doctor_view, login_view
from app.ui.common import current_user, logout

st.set_page_config(page_title="AIMIE Kids 전문의 평가 시스템", layout="wide")


def main() -> None:
    user = current_user()
    if user is None:
        login_view.render()
        return

    with st.sidebar:
        st.markdown(f"**{user.name}**")
        st.caption(f"{user.user_id} · {user.role}")
        if st.button("로그아웃", use_container_width=True):
            logout()
            st.rerun()

    if user.is_admin:
        admin_view.render(user)
    else:
        doctor_view.render(user)


main()
