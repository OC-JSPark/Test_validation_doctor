"""외부 API 대화 응답 → Q&A 턴 파싱 (SPEC.md §2-2, §3).

UI/DB 와 무관한 순수 함수만 둔다. 여기서 외부 호출을 하지 않는다.
"""

from __future__ import annotations

from typing import Any, Iterable, Mapping, Sequence

from app.models import QATurn

SENDER_AI = "teacher"
SENDER_STUDENT = "student"


def extract_messages(payload: Mapping[str, Any] | None) -> list[dict[str, Any]]:
    """API 응답 본문에서 `data.messages` 배열만 안전하게 꺼낸다."""
    if not isinstance(payload, Mapping):
        return []
    data = payload.get("data")
    if not isinstance(data, Mapping):
        return []
    messages = data.get("messages")
    if not isinstance(messages, Sequence) or isinstance(messages, (str, bytes)):
        return []
    return [m for m in messages if isinstance(m, Mapping)]


def parse_turns(messages: Iterable[Mapping[str, Any]]) -> list[QATurn]:
    """teacher(AI 질문) → student(학생 답변) 순서로 1:1 짝지어 턴을 만든다.

    규칙:
    - `teacher` 메시지를 만나면 '열린 질문'으로 잡아 둔다.
    - 그 뒤 첫 `student` 메시지와 짝지어 하나의 턴을 확정한다.
    - 답변 없이 `teacher` 가 연달아 오면 마지막 질문으로 갱신한다
      (AI 가 이어서 말한 경우 → 학생은 마지막 질문에 답한 것으로 본다).
    - 짝 없는 `student` 메시지(선행 질문 없음)는 빈 질문의 턴으로 남긴다.
      대화를 임의로 버리지 않기 위함이다.
    - `teacher`/`student` 가 아닌 sender 는 무시한다.
    """
    turns: list[QATurn] = []
    pending_question: str | None = None
    pending_date: str | None = None

    for message in messages:
        sender = str(message.get("sender") or "").strip().lower()
        text = str(message.get("text") or "")
        date = message.get("date")
        date = str(date) if date is not None else None

        if sender == SENDER_AI:
            pending_question = text
            pending_date = date
        elif sender == SENDER_STUDENT:
            turns.append(
                QATurn(
                    turn_index=len(turns),
                    ai_question=pending_question or "",
                    user_answer=text,
                    date=date or pending_date,
                )
            )
            pending_question = None
            pending_date = None

    # 답변이 아직 안 온 마지막 질문도 하나의 턴으로 남긴다 (평가 대상에서 누락 방지).
    if pending_question is not None:
        turns.append(
            QATurn(
                turn_index=len(turns),
                ai_question=pending_question,
                user_answer="",
                date=pending_date,
            )
        )

    return turns


def parse_chat_payload(payload: Mapping[str, Any] | None) -> list[QATurn]:
    """API 응답 전체를 받아 턴 목록으로 변환하는 편의 함수."""
    return parse_turns(extract_messages(payload))
