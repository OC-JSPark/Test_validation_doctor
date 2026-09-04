"""Streamlit 화면 스모크 테스트 (AppTest).

실제 validation_db 를 쓰지만, 테스트가 만든 계정/할당은 마지막에 지운다.
AppTest 는 별도 커넥션(앱의 풀)을 쓰므로 롤백 픽스처로 격리할 수 없다.
"""

from __future__ import annotations

import uuid
from pathlib import Path

import psycopg
import pytest
from psycopg.rows import dict_row
from streamlit.testing.v1 import AppTest

from app import session_directory, student_directory
from app.config import get_settings
from app.models import ROLE_ADMIN, ROLE_DOCTOR, QATurn
from app.repositories import assignments as assignments_repo
from app.repositories import evaluations as evaluations_repo
from app.repositories import users as users_repo

pytestmark = pytest.mark.db

# AppTest 는 상대 경로를 '호출한 파일' 기준으로 풀기 때문에 절대 경로로 넘긴다.
APP_FILE = str(Path(__file__).resolve().parent.parent / "Test_validation_doctor.py")


@pytest.fixture
def committed_conn():
    """커밋되는 커넥션. 테스트가 만든 계정은 끝나고 직접 지운다."""
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


def _login(user_id: str, password: str) -> AppTest:
    at = AppTest.from_file(APP_FILE, default_timeout=30).run()
    at.text_input[0].set_value(user_id)
    at.text_input[1].set_value(password)
    at.button[0].click().run()
    return at


# --- 로그인 ---------------------------------------------------------------


def test_로그인_화면이_먼저_보인다():
    at = AppTest.from_file(APP_FILE, default_timeout=30).run()

    assert not at.exception
    assert at.title[0].value == "🩺 전문의 평가 시스템"
    assert at.text_input[0].label == "아이디"


def test_잘못된_비밀번호는_거부된다(ui_doctor):
    at = _login(ui_doctor, "틀린비밀번호")

    assert not at.exception
    assert "올바르지 않습니다" in at.error[0].value
    assert "user" not in at.session_state


def test_관리자_로그인시_관리자_화면(ui_admin):
    at = _login(ui_admin, "pw1234")

    assert not at.exception
    assert at.session_state["user"].role == ROLE_ADMIN
    assert at.title[0].value == "관리자"


def test_전문의_로그인시_전문의_화면(ui_doctor):
    at = _login(ui_doctor, "pw1234")

    assert not at.exception
    assert at.title[0].value == "전문의"
    # 할당이 없으면 안내 문구
    assert any("할당된 작업이 없습니다" in info.value for info in at.info)


# --- 전문의 평가 화면 -------------------------------------------------------


def test_할당이_있으면_평가_화면이_열린다(committed_conn, ui_doctor):
    conn, _ = committed_conn
    assignment = assignments_repo.create_assignment(conn, ui_doctor, "stu-1", "sess-1", "26.08.31")
    evaluations_repo.sync_turns(
        conn, assignment.id, [QATurn(i, f"질문{i}", f"답변{i}") for i in range(3)]
    )
    assignments_repo.update_total_turns(conn, assignment.id, 3)

    at = _login(ui_doctor, "pw1234")
    # 외부 API 호출은 실패하지만(토큰 없음) 저장된 턴으로 화면이 떠야 한다.
    assert not at.exception
    assert any("진행: 1 / 3 턴" in md.value for md in at.markdown)
    assert any("AI 질문" in md.value for md in at.markdown)


def test_최종완료_버튼은_미입력_턴이_있으면_비활성(committed_conn, ui_doctor):
    conn, _ = committed_conn
    assignment = assignments_repo.create_assignment(conn, ui_doctor, "stu-2", "sess-1", "26.08.31")
    evaluations_repo.sync_turns(conn, assignment.id, [QATurn(0, "질문", "답변")])
    assignments_repo.update_total_turns(conn, assignment.id, 1)

    at = _login(ui_doctor, "pw1234")

    complete_button = next(b for b in at.button if "최종 완료" in b.label)
    assert complete_button.disabled is True


def test_모든_턴_입력후_최종완료_버튼_활성(committed_conn, ui_doctor):
    conn, _ = committed_conn
    assignment = assignments_repo.create_assignment(conn, ui_doctor, "stu-3", "sess-1", "26.08.31")
    evaluations_repo.sync_turns(conn, assignment.id, [QATurn(0, "질문", "답변")])
    assignments_repo.update_total_turns(conn, assignment.id, 1)
    evaluations_repo.save_evaluation(
        conn, assignment.id, 0, doctor_score="Very (4점)", doctor_opinion="사유"
    )
    assignments_repo.refresh_progress(conn, assignment.id)

    at = _login(ui_doctor, "pw1234")

    complete_button = next(b for b in at.button if "최종 완료" in b.label)
    assert complete_button.disabled is False


def test_소견을_나중에_입력해도_점수가_유지된다(committed_conn, ui_doctor):
    """실제 브라우저에서 발견된 회귀 시나리오: 점수 먼저 → 소견 나중."""
    conn, _ = committed_conn
    assignment = assignments_repo.create_assignment(conn, ui_doctor, "stu-5", "sess-1", "26.08.31")
    evaluations_repo.sync_turns(conn, assignment.id, [QATurn(0, "질문", "답변")])
    assignments_repo.update_total_turns(conn, assignment.id, 1)

    at = _login(ui_doctor, "pw1234")
    score_box = next(s for s in at.selectbox if "점수" in s.label)
    score_box.set_value("Very (4점)").run()
    at.text_area[0].set_value("나중에 입력한 소견").run()

    assert not at.exception
    saved = evaluations_repo.get_evaluation(conn, assignment.id, 0)
    assert saved.doctor_score == "Very (4점)"
    assert saved.doctor_opinion == "나중에 입력한 소견"
    # 두 항목이 다 채워졌으므로 [최종 완료] 가 활성화되어야 한다
    assert next(b for b in at.button if "최종 완료" in b.label).disabled is False


def test_판단이유_입력시_자동저장된다(committed_conn, ui_doctor):
    conn, _ = committed_conn
    assignment = assignments_repo.create_assignment(conn, ui_doctor, "stu-4", "sess-1", "26.08.31")
    evaluations_repo.sync_turns(conn, assignment.id, [QATurn(0, "질문", "답변")])
    assignments_repo.update_total_turns(conn, assignment.id, 1)

    at = _login(ui_doctor, "pw1234")
    at.text_area[0].set_value("자동저장 확인용 소견").run()

    assert not at.exception
    saved = evaluations_repo.get_evaluation(conn, assignment.id, 0)
    assert saved.doctor_opinion == "자동저장 확인용 소견"


# --- 관리자 화면 -----------------------------------------------------------


def test_관리자_화면에_학생_체크박스_목록이_뜬다(ui_admin):
    """학생 명부를 DB 에서 읽어 체크박스로 보여준다."""
    at = _login(ui_admin, "pw1234")

    assert not at.exception
    assert at.checkbox, "학생 체크박스가 하나도 렌더링되지 않았습니다."
    assert any("전체 선택" in b.label for b in at.button)
    assert any("학생 검색" in t.label for t in at.text_input)


def test_학생을_체크하면_그_학생의_척도검사_수만큼_대상이_잡힌다(ui_admin):
    """학생 1명 선택 → 그 학생이 실시한 검사 전부가 할당 대상이 된다."""
    at = _login(ui_admin, "pw1234")

    # 검사 이력이 있는 학생을 골라 체크한다.
    students = student_directory.list_students()
    sessions = session_directory.list_sessions([s.student_id for s in students])
    grouped = session_directory.group_by_student(sessions)
    if not grouped:
        pytest.fail("명부 학생 중 척도검사 이력이 있는 학생이 없습니다.", pytrace=False)
    target_id, target_sessions = next(iter(grouped.items()))

    next(c for c in at.checkbox if target_id in (c.help or "")).check().run()

    assert not at.exception
    texts = [c.value for c in at.markdown] + [c.value for c in at.caption]
    assert any(f"생성될 작업: **{len(target_sessions)}건**" in t for t in texts)
    assert next(b for b in at.button if b.label == "작업 생성").disabled is False


def test_검사이력이_없는_학생은_경고하고_할당하지_않는다(ui_admin):
    at = _login(ui_admin, "pw1234")

    students = student_directory.list_students()
    sessions = session_directory.list_sessions([s.student_id for s in students])
    with_sessions = set(session_directory.group_by_student(sessions))
    empty = [s for s in students if s.student_id not in with_sessions]
    if not empty:
        pytest.skip("명부 학생 전원이 검사 이력을 가지고 있어 이 경우를 만들 수 없습니다.")

    next(c for c in at.checkbox if empty[0].student_id in (c.help or "")).check().run()

    assert not at.exception
    assert any("척도검사 이력이 없어" in w.value for w in at.warning)
    texts = [c.value for c in at.markdown] + [c.value for c in at.caption]
    assert any("생성될 작업: **0건**" in t for t in texts)


def test_검색으로_걸러져도_선택이_유지된다(ui_admin):
    """체크된 학생이 검색 결과에서 빠져도 선택이 풀리면 안 된다."""
    at = _login(ui_admin, "pw1234")
    at.checkbox[0].check().run()

    search = next(t for t in at.text_input if "학생 검색" in t.label)
    search.set_value("검색결과가없는문자열zzz").run()

    assert not at.exception
    assert at.session_state["admin_selected_students"]  # 선택은 그대로


def test_할당을_고르기_전에는_삭제버튼이_없다(committed_conn, ui_admin, ui_doctor):
    """실수 클릭을 막기 위해 대상을 고른 뒤에만 삭제 버튼이 나타난다."""
    conn, _ = committed_conn
    assignment = assignments_repo.create_assignment(
        conn, ui_doctor, "stu-del-ui", "sess", "26.08.31"
    )

    at = _login(ui_admin, "pw1234")
    assert not at.exception
    assert any("삭제할 할당 선택" in m.label for m in at.multiselect)
    assert not any("선택한 할당 삭제" in b.label for b in at.button)

    target = next(m for m in at.multiselect if "삭제할 할당 선택" in m.label)
    target.set_value([assignment.id]).run()

    assert any("선택한 할당 삭제" in b.label for b in at.button)


def test_평가가_없는_할당은_삭제된다(committed_conn, ui_admin, ui_doctor):
    conn, _ = committed_conn
    assignment = assignments_repo.create_assignment(conn, ui_doctor, "stu-del-2", "sess", "26.08.31")

    at = _login(ui_admin, "pw1234")
    target = next(m for m in at.multiselect if "삭제할 할당 선택" in m.label)
    target.set_value([assignment.id]).run()
    next(b for b in at.button if "선택한 할당 삭제" in b.label).click().run()

    assert not at.exception
    assert assignments_repo.get_assignment(conn, assignment.id) is None


def test_평가가_있는_할당은_확인없이_삭제되지_않는다(committed_conn, ui_admin, ui_doctor):
    conn, _ = committed_conn
    assignment = assignments_repo.create_assignment(conn, ui_doctor, "stu-del-3", "sess", "26.08.31")
    evaluations_repo.sync_turns(conn, assignment.id, [QATurn(0, "질문", "답변")])
    evaluations_repo.save_evaluation(
        conn, assignment.id, 0, doctor_score="Very (4점)", doctor_opinion="사유"
    )

    at = _login(ui_admin, "pw1234")
    target = next(m for m in at.multiselect if "삭제할 할당 선택" in m.label)
    target.set_value([assignment.id]).run()
    next(b for b in at.button if "선택한 할당 삭제" in b.label).click().run()

    assert not at.exception
    assert assignments_repo.get_assignment(conn, assignment.id) is not None


def test_관리자_화면에_CSV_다운로드_버튼이_있다(ui_admin):
    at = _login(ui_admin, "pw1234")

    assert not at.exception
    assert len(at.tabs) == 3
    assert any("CSV 다운로드" in b.label for b in at.download_button)
