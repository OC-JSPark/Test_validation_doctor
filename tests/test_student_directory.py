"""학생 명부 조회 (외부 학생 DB, 읽기 전용)."""

from __future__ import annotations

import psycopg
import pytest
from psycopg.rows import dict_row

from app.config import get_settings
from app.models import Student
from app.student_directory import (
    StudentDirectoryError,
    filter_students,
    list_students,
)


# --- 순수 로직 (DB 불필요) --------------------------------------------------


def test_라벨은_실명과_닉네임을_함께_보여준다():
    """관리자는 실명으로 식별하지만, 앱 화면과 대조하려면 닉네임도 필요하다."""
    student = Student("abc123", "쪼리", "강남초등학교", 4, "2", name="박은지")
    assert student.label == "박은지 (쪼리) · 강남초등학교 4학년 2반"


def test_실명과_닉네임이_같으면_한_번만_표시한다():
    student = Student("abc123", "박은지", "강남초등학교", 4, "2", name="박은지")
    assert student.label == "박은지 · 강남초등학교 4학년 2반"


def test_실명이_없으면_닉네임으로_떨어진다():
    student = Student("abc123", "쪼리", "강남초등학교", 4, "2")
    assert student.label == "쪼리 · 강남초등학교 4학년 2반"


def test_실명도_닉네임도_없으면_ID_앞부분으로_표시한다():
    student = Student("8ef6012c8929758eec9da85ac250bb9e", None, "강남초등학교", 2, None)
    assert student.label.startswith("(이름없음) 8ef6012c…")


def test_공백뿐인_이름은_없는_것으로_본다():
    student = Student("abc123", "  ", name="  ")
    assert student.label.startswith("(이름없음)")


def test_학교만_있어도_라벨이_만들어진다():
    assert Student("id1", "우리", "테스트초등학교").label == "우리 · 테스트초등학교"


def test_학교정보가_없으면_이름만_나온다():
    assert Student("id1", "우리").label == "우리"


def test_검색은_실명_닉네임_학교_ID_를_모두_본다():
    students = [
        Student("aaa111", "쪼리", "강남초등학교", 4, "2", name="박은지"),
        Student("bbb222", "테스터", "테스트초등학교", 2, "4", name="김데브"),
    ]
    assert [s.nickname for s in filter_students(students, "쪼리")] == ["쪼리"]
    assert [s.nickname for s in filter_students(students, "박은지")] == ["쪼리"]  # 실명
    assert [s.nickname for s in filter_students(students, "김데브")] == ["테스터"]
    assert [s.nickname for s in filter_students(students, "테스트초")] == ["테스터"]
    assert [s.nickname for s in filter_students(students, "aaa")] == ["쪼리"]


def test_검색어가_비면_전체를_돌려준다():
    students = [Student("a", "가"), Student("b", "나")]
    assert len(filter_students(students, "")) == 2
    assert len(filter_students(students, "   ")) == 2


def test_검색은_대소문자를_구분하지_않는다():
    students = [Student("ABC123", "쪼리")]
    assert len(filter_students(students, "abc")) == 1


def test_일치하는_학생이_없으면_빈_목록():
    assert filter_students([Student("a", "가")], "없는이름") == []


# --- 실제 학생 DB (aimie_kids_app) -----------------------------------------

pytestmark_db = pytest.mark.db


@pytest.fixture
def student_conn():
    """학생 명부 DB 커넥션 (읽기 전용)."""
    url = get_settings().student_db_url
    try:
        conn = psycopg.connect(url, row_factory=dict_row)
    except psycopg.OperationalError as exc:
        pytest.fail(
            "학생 명부 DB(aimie_kids_app) 에 연결할 수 없습니다. "
            "docker compose up -d test-db 로 컨테이너를 기동하고 덤프를 복원하세요.\n"
            f"원인: {exc}",
            pytrace=False,
        )
    conn.read_only = True
    try:
        yield conn
    finally:
        conn.rollback()
        conn.close()


@pytest.mark.db
def test_실제_DB_에서_학생_목록을_읽어온다(student_conn):
    students = list_students(student_conn)

    assert students, "학생 명부가 비어 있습니다."
    assert all(s.student_id for s in students)
    # studentId 는 외부 API 가 쓰는 32자 UUID 형식이어야 한다.
    assert all(len(s.student_id) == 32 for s in students)


@pytest.mark.db
def test_실제_DB_에서_실명이_실려_온다(student_conn):
    """t_user.name 이 NOT NULL 이라 전원 실명을 갖는다 (관리자 화면 표시용)."""
    students = list_students(student_conn)

    assert all(s.name for s in students), "실명이 비어 있는 학생이 있습니다."
    # 라벨에 실명이 들어가야 관리자가 학생을 식별할 수 있다
    assert all(s.name in s.label for s in students)


@pytest.mark.db
def test_읽기전용_커넥션은_쓰기를_거부한다(student_conn):
    """실수로 쓰기 쿼리가 섞여도 DB 단계에서 막혀야 한다."""
    with pytest.raises(psycopg.errors.ReadOnlySqlTransaction):
        student_conn.execute("UPDATE t_student SET nickname = 'x' WHERE false")


@pytest.mark.db
def test_조회_실패는_StudentDirectoryError_로_감싼다():
    """DB 오류가 psycopg 예외 그대로 화면까지 올라가지 않도록 감싼다."""
    broken = psycopg.connect(get_settings().student_db_url, row_factory=dict_row)
    broken.close()

    with pytest.raises(StudentDirectoryError, match="STUDENT_SOURCE_DATABASE_URL"):
        list_students(broken)
