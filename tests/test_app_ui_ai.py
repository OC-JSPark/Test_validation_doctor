"""AI 테스트 데이터 화면 흐름 (AppTest).

외부 API 는 실제로 부르지 않는다 — 클라이언트를 fake 로 갈아끼운다.
AppTest 는 앱 자신의 커넥션 풀을 쓰므로 롤백으로 격리할 수 없다.
테스트가 만든 계정·할당은 픽스처 teardown 에서 직접 지운다.
"""

from __future__ import annotations

import uuid
from datetime import date
from pathlib import Path

import psycopg
import pytest
import streamlit as st
from psycopg.rows import dict_row
from streamlit.testing.v1 import AppTest

from app.config import get_settings
from app.models import (
    AIActivity,
    SOURCE_AI_PREVIEW,
    ROLE_ADMIN,
    ROLE_DOCTOR,
    AIPreviewDates,
    AIStudent,
    QATurn,
)
from app.repositories import assignments as assignments_repo
from app.repositories import evaluations as evaluations_repo
from app.repositories import users as users_repo
from app.ui import admin_view, doctor_view

pytestmark = pytest.mark.db

APP_FILE = str(Path(__file__).resolve().parent.parent / "Test_validation_doctor.py")


class _FakeAIClient:
    """AI 조회 API 를 대신하는 fake (네트워크 호출 없음)."""

    def __init__(self, students: list[AIStudent], turns: list[QATurn]) -> None:
        self.students = students
        self.turns = turns
        self.dates = AIPreviewDates(
            chat_date=date(2026, 9, 22), report_date=date(2026, 9, 18)
        )
        self.report = "1. 정신건강 점수 분석\n- 스트레스 점수는 0.0점"
        self.date_calls: list[str] = []
        self.activity_calls: list[str] = []
        # ai-stu-1 은 두 날짜, ai-stu-2 는 한 날짜에 검사를 받았다.
        self.activities = {
            "ai-stu-1": [
                AIActivity(
                    student_id="ai-stu-1",
                    chat_date="26.09.18",
                    session_id="sess-a",
                    level=1,
                    level_text="스트레스",
                    scale_stage="1단계 PHQ-stress",
                ),
                AIActivity(
                    student_id="ai-stu-1",
                    chat_date="26.09.11",
                    session_id="sess-b",
                    level=3,
                    level_text="우울증",
                    scale_stage="3단계 PHQ-A",
                ),
            ],
            "ai-stu-2": [
                AIActivity(
                    student_id="ai-stu-2", chat_date="26.08.20", session_id="sess-c"
                )
            ],
        }

    def fetch_ai_students(self, *, mode="all", search=None):
        return list(self.students)

    def fetch_ai_activities(self, student_id):
        self.activity_calls.append(student_id)
        return list(self.activities.get(student_id, []))

    def fetch_ai_latest_date(self, student_id):
        self.date_calls.append(student_id)
        return self.dates

    def fetch_ai_report(self, student_id, *, date=None, session_id=None):
        return self.report

    def fetch_turns(self, student_id, *, date=None, session_id=None):
        return list(self.turns)


@pytest.fixture
def committed_conn():
    settings = get_settings()
    conn = psycopg.connect(settings.database_url, row_factory=dict_row, autocommit=True)
    created_users: list[str] = []
    try:
        yield conn, created_users
    finally:
        for user_id in created_users:
            conn.execute(
                "DELETE FROM evaluation_assignments WHERE doctor_id = %s", (user_id,)
            )
            conn.execute("DELETE FROM users WHERE user_id = %s", (user_id,))
        conn.close()


@pytest.fixture
def ui_admin(committed_conn):
    conn, created = committed_conn
    user_id = f"ui_admin_{uuid.uuid4().hex[:8]}"
    users_repo.upsert_user(conn, user_id, "UI 관리자", ROLE_ADMIN, "pw1234")
    created.append(user_id)
    return user_id


@pytest.fixture
def ui_doctor(committed_conn):
    conn, created = committed_conn
    user_id = f"ui_doctor_{uuid.uuid4().hex[:8]}"
    users_repo.upsert_user(conn, user_id, "UI 전문의", ROLE_DOCTOR, "pw1234")
    created.append(user_id)
    return user_id


@pytest.fixture
def fake_ai(monkeypatch) -> _FakeAIClient:
    """앱이 쓰는 외부 API 클라이언트를 fake 로 바꾼다.

    `admin_view`/`doctor_view` 가 import 시점에 이름을 가져가므로
    두 모듈 모두에서 갈아끼운다.
    """
    client = _FakeAIClient(
        students=[
            AIStudent(student_id="ai-stu-1", seq=1, source_id=1, level_text="1단계"),
            AIStudent(student_id="ai-stu-2", seq=2, source_id=2, level_text="2단계"),
        ],
        turns=[QATurn(i, f"질문{i}", f"답변{i}") for i in range(3)],
    )
    monkeypatch.setattr(admin_view, "chat_client", lambda: client)
    monkeypatch.setattr(doctor_view, "chat_client", lambda: client)
    # 목록은 @st.cache_data 로 캐시된다. 테스트끼리 새면 안 된다.
    st.cache_data.clear()
    yield client
    st.cache_data.clear()


def _login(user_id: str, password: str) -> AppTest:
    at = AppTest.from_file(APP_FILE, default_timeout=30).run()
    at.text_input[0].set_value(user_id)
    at.text_input[1].set_value(password)
    at.button[0].click().run()
    return at


def _radio(at: AppTest, label: str):
    return next(r for r in at.radio if r.label == label)


# --- 관리자 화면 ------------------------------------------------------------


def test_데이터_출처를_고를_수_있다(ui_admin, fake_ai):
    at = _login(ui_admin, "pw1234")

    assert not at.exception
    source = _radio(at, "데이터 출처")
    assert source.value == "SERVICE"  # 기본은 기존 흐름 (실제 사용자 데이터)


def test_AI_를_고르면_AI_학생_목록이_뜬다(ui_admin, fake_ai):
    at = _login(ui_admin, "pw1234")

    _radio(at, "데이터 출처").set_value(SOURCE_AI_PREVIEW).run()

    assert not at.exception
    picker = next(m for m in at.multiselect if m.label == "AI 테스트 학생 선택")
    assert len(picker.options) == 2


def test_선택_목록에_말머리가_노출된다(ui_admin, fake_ai):
    """이 API 의 name 은 해시값이라, 말머리가 없으면 일반 학생과 구분되지 않는다.

    AppTest 의 `options` 는 format_func 를 거친 **화면 표시 문자열**이다.
    """
    at = _login(ui_admin, "pw1234")
    _radio(at, "데이터 출처").set_value(SOURCE_AI_PREVIEW).run()

    options = next(m for m in at.multiselect if m.label == "AI 테스트 학생 선택").options

    assert options[0].startswith("[AIuser01]")
    assert options[1].startswith("[AIuser02]")


def test_학생을_고르면_날짜별_검사가_모두_할당된다(
    committed_conn, ui_admin, ui_doctor, fake_ai
):
    """실제 사용자 데이터와 같은 규칙 — 학생 1명당 1건이 아니라 검사 1회당 1건."""
    conn, _ = committed_conn
    at = _login(ui_admin, "pw1234")
    _radio(at, "데이터 출처").set_value(SOURCE_AI_PREVIEW).run()

    # 담당 전문의를 테스트 계정으로 바꾼 뒤 학생 2명 선택
    at.selectbox[0].set_value(ui_doctor).run()
    next(m for m in at.multiselect if m.label == "AI 테스트 학생 선택").set_value(
        ["ai-stu-1", "ai-stu-2"]
    ).run()
    next(b for b in at.button if b.label == "작업 생성").click().run()

    assert not at.exception
    saved = assignments_repo.list_assignments(conn, doctor_id=ui_doctor)
    # ai-stu-1 이 2건, ai-stu-2 가 1건
    assert len(saved) == 3
    assert all(a.is_ai_preview for a in saved)
    assert {(a.student_id, a.chat_date, a.session_id) for a in saved} == {
        ("ai-stu-1", "26.09.18", "sess-a"),
        ("ai-stu-1", "26.09.11", "sess-b"),
        ("ai-stu-2", "26.08.20", "sess-c"),
    }


def test_판별된_척도가_할당에_실린다(committed_conn, ui_admin, ui_doctor, fake_ai):
    conn, _ = committed_conn
    at = _login(ui_admin, "pw1234")
    _radio(at, "데이터 출처").set_value(SOURCE_AI_PREVIEW).run()
    at.selectbox[0].set_value(ui_doctor).run()
    next(m for m in at.multiselect if m.label == "AI 테스트 학생 선택").set_value(
        ["ai-stu-1"]
    ).run()
    next(b for b in at.button if b.label == "작업 생성").click().run()

    saved = assignments_repo.list_assignments(conn, doctor_id=ui_doctor)
    by_date = {a.chat_date: a.scale_stage for a in saved}

    assert by_date["26.09.18"] == "1단계 PHQ-stress"
    assert by_date["26.09.11"] == "3단계 PHQ-A"


def test_선택한_학생의_검사_목록이_보인다(ui_admin, fake_ai):
    """관리자가 무엇이 할당될지 생성 전에 확인할 수 있어야 한다."""
    at = _login(ui_admin, "pw1234")
    _radio(at, "데이터 출처").set_value(SOURCE_AI_PREVIEW).run()
    next(m for m in at.multiselect if m.label == "AI 테스트 학생 선택").set_value(
        ["ai-stu-1"]
    ).run()

    assert not at.exception
    assert fake_ai.activity_calls == ["ai-stu-1"]
    # 관리자 화면에는 표가 여럿이다 (진도율·할당 현황·요약·상세).
    # 상세 표는 '표시 번호' 와 '세션 ID' 를 함께 가진 것뿐이다.
    detail = next(
        df
        for df in at.dataframe
        if {"표시 번호", "세션 ID"} <= set(getattr(df.value, "columns", []))
    )
    assert set(detail.value["날짜"]) == {"26.09.18", "26.09.11"}
    assert set(detail.value["세션 ID"]) == {"sess-a", "sess-b"}
    assert set(detail.value["척도"]) == {"1단계 PHQ-stress", "3단계 PHQ-A"}


def test_이력이_없는_학생은_할당되지_않는다(ui_admin, fake_ai):
    fake_ai.activities["ai-stu-2"] = []
    at = _login(ui_admin, "pw1234")
    _radio(at, "데이터 출처").set_value(SOURCE_AI_PREVIEW).run()
    next(m for m in at.multiselect if m.label == "AI 테스트 학생 선택").set_value(
        ["ai-stu-2"]
    ).run()

    assert not at.exception
    assert any("활동 이력이 없어" in w.value for w in at.warning)
    assert next(b for b in at.button if b.label == "작업 생성").disabled


def test_선택_전에는_작업_생성이_비활성(ui_admin, fake_ai):
    at = _login(ui_admin, "pw1234")
    _radio(at, "데이터 출처").set_value(SOURCE_AI_PREVIEW).run()

    assert next(b for b in at.button if b.label == "작업 생성").disabled


def test_관리자는_출처를_볼_수_있다(committed_conn, ui_admin, ui_doctor, fake_ai):
    """전문의는 몰라야 하지만, 관리자는 어느 쪽을 할당했는지 알아야 한다."""
    conn, _ = committed_conn
    assignments_repo.create_assignment(
        conn, ui_doctor, "ai-stu-1", "", "", None, SOURCE_AI_PREVIEW
    )
    assignments_repo.create_assignment(conn, ui_doctor, "stu-1", "sess-1", "26.08.31")

    at = _login(ui_admin, "pw1234")

    # 할당 현황 표 (진도율 표 다음)
    table = next(
        df for df in at.dataframe if "출처" in getattr(df.value, "columns", [])
    )
    sources = dict(zip(table.value["학생 ID"], table.value["출처"]))
    assert sources["ai-stu-1"] == "🤖 AI"
    assert sources["stu-1"] == "👤 실사용"


def test_API_가_죽어도_화면은_뜬다(ui_admin, monkeypatch):
    """dev 전용 엔드포인트라 환경에 따라 없을 수 있다. 앱이 죽으면 안 된다."""
    from app.external_api import ExternalAPIError

    class _Broken:
        def fetch_ai_students(self, *, mode="all", search=None):
            raise ExternalAPIError("외부 API 오류 (404)")

    monkeypatch.setattr(admin_view, "chat_client", lambda: _Broken())
    st.cache_data.clear()

    at = _login(ui_admin, "pw1234")
    _radio(at, "데이터 출처").set_value(SOURCE_AI_PREVIEW).run()

    assert not at.exception
    assert any("가져오지 못했습니다" in e.value for e in at.error)
    st.cache_data.clear()


# --- 전문의 화면 ------------------------------------------------------------


def test_작업_목록에_실제_날짜와_세션ID_가_보인다(committed_conn, ui_doctor, fake_ai):
    """'(전체)' 가 아니라 그 검사의 날짜와 세션 ID 가 떠야 한다."""
    conn, _ = committed_conn
    assignments_repo.create_assignment(
        conn, ui_doctor, "ai-stu-1", "sess-a", "26.09.18", None, SOURCE_AI_PREVIEW
    )

    at = _login(ui_doctor, "pw1234")

    assert not at.exception
    row = at.dataframe[0].value
    assert list(row["날짜"]) == ["26.09.18"]
    assert list(row["세션 ID"]) == ["sess-a"]
    # 날짜가 이미 있으니 latest-date 를 부를 일이 없다
    assert fake_ai.date_calls == []


def test_평가_화면에도_날짜가_그대로_뜬다(committed_conn, ui_doctor, fake_ai):
    conn, _ = committed_conn
    assignments_repo.create_assignment(
        conn, ui_doctor, "ai-stu-1", "sess-a", "26.09.18", None, SOURCE_AI_PREVIEW
    )

    at = _login(ui_doctor, "pw1234")

    assert any("26.09.18" in c.value and "sess-a" in c.value for c in at.caption)


def test_날짜가_없는_옛_할당은_자동으로_채워진다(committed_conn, ui_doctor, fake_ai):
    """활동 이력이 없어 날짜 없이 만들어진 할당의 안전망."""
    conn, _ = committed_conn
    created = assignments_repo.create_assignment(
        conn, ui_doctor, "ai-stu-1", "", "", None, SOURCE_AI_PREVIEW
    )

    at = _login(ui_doctor, "pw1234")

    assert not at.exception
    assert fake_ai.date_calls == ["ai-stu-1"]
    assert assignments_repo.get_assignment(conn, created.id).chat_date == "26.09.22"


def test_전문의_화면에_AI_라는_흔적이_없다(committed_conn, ui_doctor, fake_ai):
    """평가자가 'AI 가 만든 데이터'라고 알면 판단이 달라질 수 있다.

    할당받은 것을 그대로 평가하는 기존 화면과 **구별되지 않아야** 한다.
    """
    conn, _ = committed_conn
    assignments_repo.create_assignment(
        conn, ui_doctor, "ai-stu-1", "", "", None, SOURCE_AI_PREVIEW
    )

    at = _login(ui_doctor, "pw1234")

    rendered = " ".join(
        [e.value for e in at.markdown]
        + [e.value for e in at.info]
        + [e.value for e in at.warning]
        + [e.value for e in at.caption]
        + [e.label for e in at.button]
    )
    for 흔적 in ("AI 테스트", "AI_PREVIEW", "가상", "리포트 날짜", "자동"):
        assert 흔적 not in rendered, f"전문의 화면에 '{흔적}' 이 노출됐습니다"
    assert not at.metric  # 대화/리포트 날짜 지표를 띄우지 않는다


def test_작업_목록에_출처_열이_없다(committed_conn, ui_doctor, fake_ai):
    """열이 있으면 값이 무엇이든 AI 여부를 역추적할 수 있다."""
    conn, _ = committed_conn
    assignments_repo.create_assignment(
        conn, ui_doctor, "ai-stu-1", "", "", None, SOURCE_AI_PREVIEW
    )

    at = _login(ui_doctor, "pw1234")

    assert "출처" not in at.dataframe[0].value.columns


def test_AI_할당과_일반_할당의_목록_열이_같다(committed_conn, ui_doctor, fake_ai):
    conn, _ = committed_conn
    assignments_repo.create_assignment(
        conn, ui_doctor, "ai-stu-1", "", "", None, SOURCE_AI_PREVIEW
    )
    at_ai = _login(ui_doctor, "pw1234")
    ai_columns = list(at_ai.dataframe[0].value.columns)

    conn.execute(
        "UPDATE evaluation_assignments SET source = 'SERVICE' WHERE doctor_id = %s",
        (ui_doctor,),
    )
    at_service = _login(ui_doctor, "pw1234")

    assert list(at_service.dataframe[0].value.columns) == ai_columns


def test_일반_할당에는_날짜_API_를_부르지_않는다(committed_conn, ui_doctor, fake_ai):
    conn, _ = committed_conn
    assignment = assignments_repo.create_assignment(
        conn, ui_doctor, "stu-1", "sess-1", "26.08.31"
    )
    evaluations_repo.sync_turns(conn, assignment.id, [QATurn(0, "질문", "답변")])

    _login(ui_doctor, "pw1234")

    assert fake_ai.date_calls == []  # dev 전용 경로를 괜히 부르지 않는다


def test_AI_할당도_기존처럼_평가_화면이_열린다(committed_conn, ui_doctor, fake_ai):
    conn, _ = committed_conn
    assignments_repo.create_assignment(
        conn, ui_doctor, "ai-stu-1", "", "", None, SOURCE_AI_PREVIEW
    )

    at = _login(ui_doctor, "pw1234")

    assert not at.exception
    assert any("진행: 1 / 3 턴" in md.value for md in at.markdown)
    assert any("AI 질문" in md.value for md in at.markdown)
