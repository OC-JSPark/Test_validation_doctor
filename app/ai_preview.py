"""AI 테스트 데이터 응답 파싱 (순수 함수).

`GET /api-kids/dev/ai-preview/*` 응답을 도메인 모델로 옮긴다.
네트워크 호출은 `app/external_api.py` 가 하고, 여기는 받은 dict 만 다룬다
(로직과 I/O 를 갈라 유닛 테스트하기 위한 분리 — CLAUDE.md).

AI DB 에만 있는 생성 데이터라서 응답이 느슨하다. `name` 이 해시값이거나
`loginId`/`grade` 가 `null` 인 경우가 많고, 날짜 필드도 일부만 올 수 있다.
그래서 파싱은 **빠진 값에 관대하고, 형식이 틀린 값은 버린다.**
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

from app.models import AIPreviewDates, AIStudent

# latest-date 응답의 날짜 형식. 정의서 예시는 "2026-09-18".
_DATE_FORMATS = ("%Y-%m-%d", "%Y/%m/%d", "%y.%m.%d")


def _text(value: Any) -> str | None:
    """문자열로 쓸 수 있는 값만 남긴다. null·빈 문자열은 None."""
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _int(value: Any) -> int | None:
    """숫자로 쓸 수 있는 값만. 0 도 유효한 값이라 그대로 둔다."""
    if value is None or isinstance(value, bool):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def parse_date(value: Any) -> date | None:
    """`2026-09-18` 같은 날짜 문자열을 date 로. 못 읽으면 None.

    형식이 틀린 날짜로 대화를 조회하면 엉뚱한 결과가 나오므로,
    읽히지 않으면 추측하지 않고 버린다 (호출부가 '날짜 없음'으로 처리한다).
    """
    text = _text(value)
    if not text:
        return None
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    return None


def _data(payload: dict[str, Any] | None) -> Any:
    """응답 봉투에서 `data` 를 꺼낸다.

    이 API 군은 `{"success": true, "code": "OK", "data": ...}` 로 감싸서 준다.
    봉투 없이 본문만 오는 경우도 받아들인다.
    """
    if not isinstance(payload, dict):
        return None
    return payload.get("data", payload)


def parse_students(payload: dict[str, Any] | None) -> list[AIStudent]:
    """AI 학생 목록 응답을 `AIStudent` 목록으로.

    표시용 일련번호(`seq`) 를 1부터 매긴다. **API 가 준 `id` 오름차순**으로
    매겨서, 다시 불러와도 같은 학생이 같은 `[AIuser01]` 번호를 받는다.
    (응답 순서에만 의존하면 호출할 때마다 번호가 뒤바뀔 수 있다.)

    `studentId` 가 없는 항목은 할당할 수 없으므로 버린다.
    """
    rows = _data(payload)
    if not isinstance(rows, list):
        return []

    parsed: list[tuple[int | None, dict]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        student_id = _text(row.get("studentId")) or _text(row.get("userUuid"))
        if not student_id:
            continue
        parsed.append((_int(row.get("id")), {**row, "studentId": student_id}))

    # id 가 없는 항목은 뒤로 보내되, 원래 순서는 유지한다.
    ordered = sorted(
        enumerate(parsed),
        key=lambda pair: (pair[1][0] is None, pair[1][0] if pair[1][0] is not None else 0, pair[0]),
    )

    students: list[AIStudent] = []
    for seq, (_, (source_id, row)) in enumerate(ordered, start=1):
        students.append(
            AIStudent(
                student_id=row["studentId"],
                seq=seq,
                source_id=source_id,
                name=_text(row.get("name")),
                login_id=_text(row.get("loginId")),
                grade=_int(row.get("grade")),
                class_name=_text(row.get("class")),
                level=_int(row.get("level")),
                level_text=_text(row.get("levelText")),
            )
        )
    return students


def parse_latest_date(payload: dict[str, Any] | None) -> AIPreviewDates:
    """최신 날짜 응답을 `AIPreviewDates` 로.

    정의서 응답에는 `date` / `reportDate` / `chatDate` 세 개가 있다.
    대화 조회에 쓰는 것은 `chatDate` 이고, `chatDate` 가 비면 `date` 로 떨어진다
    (`date` 는 두 날짜를 아우르는 대표값으로 보인다).
    """
    data = _data(payload)
    if not isinstance(data, dict):
        return AIPreviewDates()

    fallback = parse_date(data.get("date"))
    return AIPreviewDates(
        chat_date=parse_date(data.get("chatDate")) or fallback,
        report_date=parse_date(data.get("reportDate")) or fallback,
    )


def parse_report(payload: dict[str, Any] | None) -> str:
    """AI 리포트 응답을 본문 텍스트로.

    `data` 가 통째로 문자열인 응답이다. 이스케이프된 개행(`\\n`)이 그대로
    들어 있어 화면에서 줄바꿈이 먹지 않으므로 실제 개행으로 바꿔 준다.
    """
    data = _data(payload)
    if data is None:
        return ""
    if isinstance(data, dict):
        # 혹시 `{"report": "..."}` 형태로 바뀌어도 읽히도록 둔다.
        data = data.get("report") or data.get("content") or ""
    return str(data).replace("\\n", "\n").strip()


def filter_students(students: list[AIStudent], query: str) -> list[AIStudent]:
    """말머리/이름/ID 검색 (순수 함수)."""
    if not query or not query.strip():
        return students
    needle = query.strip().lower()
    return [s for s in students if s.matches(needle)]
