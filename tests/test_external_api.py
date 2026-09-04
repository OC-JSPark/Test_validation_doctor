"""외부 API 클라이언트.

실제 네트워크 호출은 하지 않는다 — `responses` 로 HTTP 계층을 가로챈다.
"""

from __future__ import annotations

import json

import pytest
import responses

from app.config import Settings
from app.external_api import CHAT_PATH, LOGIN_PATH, ChatAPIClient, ExternalAPIError

BASE_URL = "https://dev.aimie-m.test"
CHAT_URL = f"{BASE_URL}{CHAT_PATH}"
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


@responses.activate
def test_대화를_가져와_턴으로_파싱한다(settings, chat_payload):
    responses.add(responses.GET, CHAT_URL, json=chat_payload, status=200)
    client = ChatAPIClient(settings, token="tok")

    turns = client.fetch_turns("student-1", date="26.08.31", session_id="sess-1")

    assert len(turns) == 4
    assert turns[0].ai_question == "요즘 학원 생활은 어때?"


@responses.activate
def test_쿼리파라미터와_인증헤더가_붙는다(settings, chat_payload):
    responses.add(responses.GET, CHAT_URL, json=chat_payload, status=200)
    client = ChatAPIClient(settings, token="tok-abc")

    client.fetch_chat("student-1", date="26.08.31", session_id="sess-1")

    request = responses.calls[0].request
    assert request.headers["Authorization"] == "Bearer tok-abc"
    assert "studentId=student-1" in request.url
    assert "sessionId=sess-1" in request.url
    assert "date=26.08.31" in request.url


@responses.activate
def test_선택_파라미터는_비어있으면_생략된다(settings, chat_payload):
    responses.add(responses.GET, CHAT_URL, json=chat_payload, status=200)

    ChatAPIClient(settings, token="tok").fetch_chat("student-1")

    url = responses.calls[0].request.url
    assert "sessionId" not in url
    assert "date" not in url


@responses.activate
def test_로그인하면_토큰을_보관한다(settings):
    responses.add(
        responses.POST,
        LOGIN_URL,
        json={"result": "SUCCESS", "accessToken": "new-token", "role": "TEACHER"},
        status=200,
    )
    client = ChatAPIClient(settings)

    assert client.login("id", "pw") == "new-token"
    assert client.token == "new-token"


@responses.activate
def test_토큰이_없으면_설정된_계정으로_자동_로그인한다(chat_payload):
    settings = Settings(
        database_url="postgresql://unused",
        api_base_url=BASE_URL,
        api_token=None,
        api_login_id="admin-id",
        api_password="admin-pw",
        api_timeout=1.0,
        score_options=("1점",),
        scale_stages=("1단계",),
    )
    responses.add(responses.POST, LOGIN_URL, json={"accessToken": "auto"}, status=200)
    responses.add(responses.GET, CHAT_URL, json=chat_payload, status=200)

    client = ChatAPIClient(settings)
    client.fetch_chat("student-1")

    assert client.token == "auto"
    assert responses.calls[1].request.headers["Authorization"] == "Bearer auto"


@responses.activate
def test_accessToken_이_없는_로그인_응답은_오류(settings):
    responses.add(responses.POST, LOGIN_URL, json={"result": "FAIL"}, status=200)

    with pytest.raises(ExternalAPIError, match="accessToken"):
        ChatAPIClient(settings).login("id", "pw")


@responses.activate
def test_HTTP_오류는_상태코드와_함께_전달된다(settings):
    responses.add(responses.GET, CHAT_URL, json={"message": "unauthorized"}, status=401)

    with pytest.raises(ExternalAPIError) as excinfo:
        ChatAPIClient(settings, token="bad").fetch_chat("student-1")

    assert excinfo.value.status_code == 401


@responses.activate
def test_JSON_이_아닌_응답은_오류(settings):
    responses.add(responses.GET, CHAT_URL, body="<html>500</html>", status=200)

    with pytest.raises(ExternalAPIError, match="JSON"):
        ChatAPIClient(settings, token="tok").fetch_chat("student-1")


@responses.activate
def test_연결_실패는_ExternalAPIError_로_감싼다(settings):
    # 등록된 응답이 없으면 responses 가 ConnectionError 를 낸다.
    with pytest.raises(ExternalAPIError, match="연결 실패"):
        ChatAPIClient(settings, token="tok").fetch_chat("student-1")


@responses.activate
def test_토큰_만료시_재로그인_후_재시도한다(chat_payload):
    """실제 화면에서 발견된 회귀: 캐시된 토큰이 만료되면 계속 401 만 났다."""
    creds = Settings(
        database_url="postgresql://unused",
        api_base_url=BASE_URL,
        api_token=None,
        api_login_id="admin-id",
        api_password="admin-pw",
        api_timeout=1.0,
        score_options=("1점",),
        scale_stages=("1단계",),
    )
    responses.add(responses.GET, CHAT_URL, json={"message": "Token expired"}, status=401)
    responses.add(responses.POST, LOGIN_URL, json={"accessToken": "fresh"}, status=200)
    responses.add(responses.GET, CHAT_URL, json=chat_payload, status=200)

    client = ChatAPIClient(creds, token="expired-token")
    turns = client.fetch_turns("student-1")

    assert len(turns) == 4
    assert client.token == "fresh"
    assert responses.calls[2].request.headers["Authorization"] == "Bearer fresh"


@responses.activate
def test_로그인_정보가_없으면_401_을_그대로_올린다(settings):
    """재시도할 수단이 없으면 조용히 삼키지 말고 오류를 보여준다."""
    responses.add(responses.GET, CHAT_URL, json={}, status=401)

    with pytest.raises(ExternalAPIError) as excinfo:
        ChatAPIClient(settings, token="expired").fetch_chat("student-1")

    assert excinfo.value.status_code == 401


@responses.activate
def test_재로그인해도_401_이면_두번만_시도하고_포기한다(chat_payload):
    creds = Settings(
        database_url="postgresql://unused",
        api_base_url=BASE_URL,
        api_token=None,
        api_login_id="admin-id",
        api_password="admin-pw",
        api_timeout=1.0,
        score_options=("1점",),
        scale_stages=("1단계",),
    )
    responses.add(responses.GET, CHAT_URL, json={}, status=401)
    responses.add(responses.POST, LOGIN_URL, json={"accessToken": "fresh"}, status=200)
    responses.add(responses.GET, CHAT_URL, json={}, status=401)

    with pytest.raises(ExternalAPIError):
        ChatAPIClient(creds, token="expired").fetch_chat("student-1")

    # 조회 2회 + 로그인 1회 = 3회. 무한 재시도하지 않는다.
    assert len(responses.calls) == 3


@responses.activate
def test_404_오류에_경로_확인_힌트가_붙는다(settings):
    """게이트웨이 프리픽스가 틀렸을 때 어디를 고쳐야 하는지 알려준다."""
    responses.add(responses.GET, CHAT_URL, json={}, status=404)

    with pytest.raises(ExternalAPIError, match="EXTERNAL_API_CHAT_PATH"):
        ChatAPIClient(settings, token="tok").fetch_chat("student-1")


@responses.activate
def test_401_오류에_토큰_힌트가_붙는다(settings):
    responses.add(responses.GET, CHAT_URL, json={}, status=401)

    with pytest.raises(ExternalAPIError, match="토큰이 만료"):
        ChatAPIClient(settings, token="tok").fetch_chat("student-1")


@responses.activate
def test_경로와_loginType_을_설정에서_읽는다(chat_payload):
    """게이트웨이 프리픽스가 다른 환경에서도 환경변수만으로 대응할 수 있어야 한다."""
    custom = Settings(
        database_url="postgresql://unused",
        api_base_url=BASE_URL,
        api_token=None,
        api_login_id=None,
        api_password=None,
        api_timeout=1.0,
        score_options=("1점",),
        scale_stages=("1단계",),
        api_login_path="/custom/login",
        api_chat_path="/custom/chat",
        api_login_type="MEDICAL",
    )
    responses.add(responses.POST, f"{BASE_URL}/custom/login", json={"accessToken": "t"}, status=200)
    responses.add(responses.GET, f"{BASE_URL}/custom/chat", json=chat_payload, status=200)

    client = ChatAPIClient(custom)
    client.login("id", "pw")
    turns = client.fetch_turns("student-1")

    assert json.loads(responses.calls[0].request.body)["loginType"] == "MEDICAL"
    assert "/custom/chat" in responses.calls[1].request.url
    assert len(turns) == 4
