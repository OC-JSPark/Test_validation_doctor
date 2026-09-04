"""외부 서비스 API 클라이언트 (Read-only).

SPEC.md §1 제약: 기존 서비스 DB 에 직접 붙지 않는다. 학생 대화 내역은
`GET /api-kids/risk-students/student/chat` 호출로만 가져온다.
"""

from __future__ import annotations

from typing import Any

import requests

from app.config import DEFAULT_CHAT_PATH, DEFAULT_LOGIN_PATH, Settings, get_settings
from app.models import QATurn
from app.parsing import parse_chat_payload

# 기본 경로. 실제 사용 경로는 Settings 에서 읽으며 환경변수로 덮어쓸 수 있다.
CHAT_PATH = DEFAULT_CHAT_PATH
LOGIN_PATH = DEFAULT_LOGIN_PATH


class ExternalAPIError(RuntimeError):
    """외부 API 호출 실패. 상태코드를 함께 들고 다닌다."""

    def __init__(self, message: str, *, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


class ChatAPIClient:
    """대화 내역 조회 전용 클라이언트.

    `session` 을 주입할 수 있게 열어 두어, 테스트에서 fake 로 갈아끼운다
    (테스트가 네트워크·토큰에 의존하면 안 된다 — CLAUDE.md).
    """

    def __init__(
        self,
        settings: Settings | None = None,
        *,
        token: str | None = None,
        session: requests.Session | None = None,
    ) -> None:
        self.settings = settings or get_settings()
        self.token = token if token is not None else self.settings.api_token
        self.session = session or requests.Session()

    # --- 인증 -------------------------------------------------------------
    def login(self, login_id: str, password: str, *, login_type: str | None = None) -> str:
        """POST {login_path} 로 로그인해 accessToken 을 확보한다.

        토큰은 인스턴스에 보관되어 이후 대화 조회에 Bearer 로 붙는다.
        """
        payload = {
            "loginType": login_type or self.settings.api_login_type,
            "loginId": login_id,
            "password": password,
            "rememberMe": False,
        }
        body = self._request("POST", self.settings.api_login_path, json=payload)
        token = body.get("accessToken")
        if not token:
            result = body.get("result") or body.get("message") or "(응답에 사유 없음)"
            raise ExternalAPIError(f"로그인 응답에 accessToken 이 없습니다: {result}")
        self.token = token
        return token

    def ensure_token(self) -> str | None:
        """토큰이 없고 로그인 정보가 설정돼 있으면 자동으로 로그인한다."""
        if self.token:
            return self.token
        return self._relogin()

    def _relogin(self) -> str | None:
        """설정된 계정으로 로그인해 토큰을 새로 받는다. 계정이 없으면 None."""
        if self.settings.api_login_id and self.settings.api_password:
            return self.login(self.settings.api_login_id, self.settings.api_password)
        return None

    # --- 대화 조회 ---------------------------------------------------------
    def fetch_chat(
        self, student_id: str, *, date: str | None = None, session_id: str | None = None
    ) -> dict[str, Any]:
        """원본 응답(JSON) 을 그대로 돌려준다."""
        params: dict[str, str] = {"studentId": student_id}
        if date:
            params["date"] = date
        if session_id:
            params["sessionId"] = session_id
        self.ensure_token()
        try:
            return self._request("GET", self.settings.api_chat_path, params=params)
        except ExternalAPIError as exc:
            # 토큰은 만료된다. 401 이면 한 번만 재로그인 후 재시도한다.
            if exc.status_code != 401:
                raise
            self.token = None
            if self._relogin() is None:
                raise
            return self._request("GET", self.settings.api_chat_path, params=params)

    def fetch_turns(
        self, student_id: str, *, date: str | None = None, session_id: str | None = None
    ) -> list[QATurn]:
        """대화를 가져와 Q&A 턴 목록으로 파싱해 돌려준다."""
        return parse_chat_payload(
            self.fetch_chat(student_id, date=date, session_id=session_id)
        )

    # --- 내부 -------------------------------------------------------------
    def _headers(self) -> dict[str, str]:
        headers = {"Accept": "application/json"}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        return headers

    def _request(self, method: str, path: str, **kwargs: Any) -> dict[str, Any]:
        url = f"{self.settings.api_base_url}{path}"
        try:
            response = self.session.request(
                method,
                url,
                headers=self._headers(),
                timeout=self.settings.api_timeout,
                **kwargs,
            )
        except requests.RequestException as exc:  # 네트워크 자체가 실패한 경우
            raise ExternalAPIError(f"외부 API 연결 실패: {exc}") from exc

        if response.status_code >= 400:
            # 원인별로 손볼 곳이 달라서 힌트를 붙여 준다.
            hint = {
                401: " → 토큰이 만료됐거나 로그인 정보가 틀렸습니다.",
                403: " → 이 계정에 조회 권한이 없습니다.",
                404: f" → 경로 '{path}' 가 서버에 없습니다. EXTERNAL_API_BASE_URL /"
                " EXTERNAL_API_LOGIN_PATH / EXTERNAL_API_CHAT_PATH 를 확인하세요.",
                502: " → 게이트웨이 뒤의 백엔드가 응답하지 않습니다 (서버 점검/다운).",
                504: " → 백엔드 응답이 지연되고 있습니다.",
            }.get(response.status_code, "")
            raise ExternalAPIError(
                f"외부 API 오류 ({response.status_code}){hint} {response.text[:200]}",
                status_code=response.status_code,
            )
        try:
            body = response.json()
        except ValueError as exc:
            raise ExternalAPIError("외부 API 응답이 JSON 이 아닙니다.") from exc

        if not isinstance(body, dict):
            raise ExternalAPIError("외부 API 응답 형식이 올바르지 않습니다.")
        return body
