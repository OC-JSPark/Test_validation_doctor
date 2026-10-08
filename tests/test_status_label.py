"""상태 표시 이름 (PENDING → 시작전).

DB 에 저장되는 값은 영문 그대로다. 화면에 보이는 글자만 바꾼다.
저장값까지 바꾸면 CHECK 제약·기존 데이터·CSV 가 전부 깨진다.
"""

from __future__ import annotations

from app.models import (
    STATUS_COMPLETED,
    STATUS_IN_PROGRESS,
    STATUS_PENDING,
    status_label,
)


def test_세_가지_상태를_한글로_바꾼다():
    assert status_label(STATUS_PENDING) == "시작전"
    assert status_label(STATUS_IN_PROGRESS) == "진행중"
    assert status_label(STATUS_COMPLETED) == "완료"


def test_저장값은_영문_그대로다():
    """상수 자체가 바뀌면 DB CHECK 제약과 기존 행이 깨진다."""
    assert STATUS_PENDING == "PENDING"
    assert STATUS_IN_PROGRESS == "IN_PROGRESS"
    assert STATUS_COMPLETED == "COMPLETED"


def test_대소문자와_공백을_흡수한다():
    assert status_label(" pending ") == "시작전"
    assert status_label("In_Progress") == "진행중"


def test_모르는_상태는_원본을_그대로_보여준다():
    """상태가 늘어났을 때 화면에서 조용히 사라지면 안 된다."""
    assert status_label("ARCHIVED") == "ARCHIVED"


def test_비어_있으면_빈_문자열():
    assert status_label(None) == ""
    assert status_label("") == ""
