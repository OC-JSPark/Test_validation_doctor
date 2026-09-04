"""로그인 화면 (POST /api/auth/login)."""

from __future__ import annotations

import psycopg
import streamlit as st

from app.services import auth as auth_service
from app.ui.common import connection


def render() -> None:
    st.title("🩺 전문의 평가 시스템")
    st.caption("로컬 평가 DB 계정으로 로그인하세요.")

    with st.form("login_form"):
        user_id = st.text_input("아이디")
        password = st.text_input("비밀번호", type="password")
        submitted = st.form_submit_button("로그인", use_container_width=True)

    if not submitted:
        return

    try:
        with connection() as conn:
            user = auth_service.login(conn, user_id.strip(), password)
    except psycopg.OperationalError:
        st.error(
            "평가 DB(validation_db)에 연결할 수 없습니다. "
            "`docker compose up -d test-db` 로 컨테이너를 먼저 기동하세요."
        )
        return

    if user is None:
        st.error("아이디 또는 비밀번호가 올바르지 않습니다.")
        return

    st.session_state.user = user
    st.rerun()
