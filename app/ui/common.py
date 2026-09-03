"""화면 공통 유틸 (커넥션 캐시, 세션 상태)."""

from __future__ import annotations

from contextlib import contextmanager
from typing import Iterator

import psycopg
import streamlit as st
from psycopg_pool import ConnectionPool

from app import db
from app.config import get_settings
from app.external_api import ChatAPIClient
from app.models import User


@st.cache_resource(show_spinner=False)
def _pool() -> ConnectionPool:
    """커넥션 풀은 세션·재실행과 무관하게 프로세스당 1개만 만든다."""
    return db.get_pool()


@contextmanager
def connection() -> Iterator[psycopg.Connection]:
    """트랜잭션 1개. 정상 종료 시 커밋, 예외 시 롤백."""
    with _pool().connection() as conn:
        yield conn


@st.cache_resource(show_spinner=False)
def chat_client() -> ChatAPIClient:
    """외부 API 클라이언트 (토큰을 재사용하기 위해 캐시한다)."""
    return ChatAPIClient()


def current_user() -> User | None:
    return st.session_state.get("user")


def require_login() -> User:
    user = current_user()
    if user is None:
        st.stop()
    return user


def logout() -> None:
    for key in list(st.session_state.keys()):
        del st.session_state[key]


def api_status_caption() -> None:
    settings = get_settings()
    st.caption(f"외부 API: {settings.api_base_url} (읽기 전용)")


def progress_bar(pct: float) -> None:
    st.progress(min(max(pct / 100, 0.0), 1.0), text=f"{pct:.1f}%")
