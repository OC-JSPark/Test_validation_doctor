"""AI 테스트 데이터 조회 API 클라이언트.

실제 네트워크 호출은 하지 않는다 — `responses` 로 HTTP 계층을 가로챈다.
"""

from __future__ import annotations

from datetime import date

import pytest
import responses

from app.config import (
    DEFAULT_AI_LATEST_DATE_PATH,
    DEFAULT_AI_REPORT_PATH,
    DEFAULT_AI_STUDENTS_PATH,
    Settings,
)
from app.external_api import LOGIN_PATH, ChatAPIClient, ExternalAPIError

BASE_URL = "https://dev.aimie-m.test"
STUDENTS_URL = f"{BASE_URL}{DEFAULT_AI_STUDENTS_PATH}"
LATEST_DATE_URL = f"{BASE_URL}{DEFAULT_AI_LATEST_DATE_PATH}"
REPORT_URL = f"{BASE_URL}{DEFAULT_AI_REPORT_PATH}"
LOGIN_URL = f"{BASE_URL}{LOGIN_PATH}"


@pytest.fixture
def settings() -> Settings:
    return Settings(
        database_url="postgresql://unused",
        api_base_url=BASE_URL,
        api_token=None,
        api_login_id=None,
        api_password=None,
        api_timeout=1.0,
        score_options=("1점",),
        scale_stages=("1단계",),
    )


@pytest.fixture
def students_payload() -> dict:
    return {
        "success": True,
        "code": "OK",
        "data": [
            {
                "id": 1,
                "studentId": "0fffe77c82d450b0115e12915be4d9fe",
                "loginId": None,
                "name": "0fffe77c82d450b0115e12915be4d9fe",
                "grade": 0,
                "level": 1,
                "levelText": "1단계",
                "class": 0,
            },
            {
                "id": 2,
                "studentId": "1fdceef6c065d0690eb2177a000cdfff",
                "loginId": None,
                "name": "1fdceef6c065d0690eb2177a000cdfff",
                "level": 2,
                "levelText": "2단계",
            },
        ],
    }


# --- AI 학생 목록 -----------------------------------------------------------


@responses.activate
def test_AI_학생_목록을_가져온다(settings, students_payload):
    responses.add(responses.GET, STUDENTS_URL, json=students_payload, status=200)
    client = ChatAPIClient(settings, token="tok")

    students = client.fetch_ai_students()

    assert [s.tag for s in students] == ["[AIuser01]", "[AIuser02]"]
    assert students[0].student_id == "0fffe77c82d450b0115e12915be4d9fe"


@responses.activate
def test_mode_기본값은_all(settings, students_payload):
    responses.add(responses.GET, STUDENTS_URL, json=students_payload, status=200)
    client = ChatAPIClient(settings, token="tok")

    client.fetch_ai_students()

    assert "mode=all" in responses.calls[0].request.url


@responses.activate
def test_검색어와_mode_를_넘길_수_있다(settings, students_payload):
    responses.add(responses.GET, STUDENTS_URL, json=students_payload, status=200)
    client = ChatAPIClient(settings, token="tok")

    client.fetch_ai_students(mode="risk", search="0fffe")

    url = responses.calls[0].request.url
    assert "mode=risk" in url
    assert "search=0fffe" in url


@responses.activate
def test_빈_검색어는_파라미터로_붙지_않는다(settings, students_payload):
    responses.add(responses.GET, STUDENTS_URL, json=students_payload, status=200)
    client = ChatAPIClient(settings, token="tok")

    client.fetch_ai_students(search="   ")

    assert "search=" not in responses.calls[0].request.url


@responses.activate
def test_인증헤더가_붙는다(settings, students_payload):
    responses.add(responses.GET, STUDENTS_URL, json=students_payload, status=200)
    client = ChatAPIClient(settings, token="tok-abc")

    client.fetch_ai_students()

    assert responses.calls[0].request.headers["Authorization"] == "Bearer tok-abc"


# --- 최신 날짜 --------------------------------------------------------------


@responses.activate
def test_최신_날짜를_가져온다(settings):
    responses.add(
        responses.GET,
        LATEST_DATE_URL,
        json={
            "success": True,
            "data": {
                "date": "2026-09-18",
                "reportDate": "2026-09-18",
                "chatDate": "2026-09-22",
            },
        },
        status=200,
    )
    client = ChatAPIClient(settings, token="tok")

    dates = client.fetch_ai_latest_date("stu-1")

    assert dates.chat_date == date(2026, 9, 22)
    assert dates.report_date == date(2026, 9, 18)
    assert "studentId=stu-1" in responses.calls[0].request.url


@responses.activate
def test_날짜가_없어도_예외가_아니다(settings):
    """데이터가 아직 없는 학생이 있을 수 있다. 화면은 '없음'으로 안내한다."""
    responses.add(responses.GET, LATEST_DATE_URL, json={"data": {}}, status=200)
    client = ChatAPIClient(settings, token="tok")

    assert client.fetch_ai_latest_date("stu-1").is_empty


# --- AI 리포트 --------------------------------------------------------------


@responses.activate
def test_리포트를_가져온다(settings):
    responses.add(
        responses.GET,
        REPORT_URL,
        json={"success": True, "data": "1. 정신건강 점수 분석\\n- 스트레스 0.0점"},
        status=200,
    )
    client = ChatAPIClient(settings, token="tok")

    report = client.fetch_ai_report("stu-1", date="26.09.22")

    assert report.startswith("1. 정신건강 점수 분석")
    assert "\n" in report  # 이스케이프된 개행이 풀렸다
    assert "date=26.09.22" in responses.calls[0].request.url


# --- 토큰 만료 --------------------------------------------------------------


@responses.activate
def test_토큰이_만료되면_한_번_재로그인한다(settings, students_payload):
    """AI 조회도 대화 조회와 똑같이 401 을 한 번만 복구해야 한다."""
    settings = Settings(**{**settings.__dict__, "api_login_id": "t", "api_password": "p"})
    responses.add(responses.GET, STUDENTS_URL, json={"message": "expired"}, status=401)
    responses.add(responses.POST, LOGIN_URL, json={"accessToken": "new-tok"}, status=200)
    responses.add(responses.GET, STUDENTS_URL, json=students_payload, status=200)
    client = ChatAPIClient(settings, token="old-tok")

    students = client.fetch_ai_students()

    assert len(students) == 2
    assert client.token == "new-tok"
    assert len(responses.calls) == 3  # 401 → 로그인 → 재조회. 그 이상 돌지 않는다


@responses.activate
def test_재로그인_후에도_401_이면_포기한다(settings):
    """자격증명 자체가 틀린 경우 무한 재로그인에 빠지면 안 된다."""
    settings = Settings(**{**settings.__dict__, "api_login_id": "t", "api_password": "p"})
    responses.add(responses.GET, LATEST_DATE_URL, json={}, status=401)
    responses.add(responses.POST, LOGIN_URL, json={"accessToken": "tok2"}, status=200)
    responses.add(responses.GET, LATEST_DATE_URL, json={}, status=401)
    client = ChatAPIClient(settings, token="old")

    with pytest.raises(ExternalAPIError):
        client.fetch_ai_latest_date("stu-1")

    assert len(responses.calls) == 3


@responses.activate
def test_경로가_없으면_어디를_고칠지_알려준다(settings):
    """dev 전용 엔드포인트라 환경에 따라 없을 수 있다."""
    responses.add(responses.GET, STUDENTS_URL, json={}, status=404)
    client = ChatAPIClient(settings, token="tok")

    with pytest.raises(ExternalAPIError) as exc:
        client.fetch_ai_students()

    assert DEFAULT_AI_STUDENTS_PATH in str(exc.value)
