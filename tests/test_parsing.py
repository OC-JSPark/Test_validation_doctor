"""Q&A 턴 파싱 (SPEC.md §2-2, §3)."""

from __future__ import annotations

from app.parsing import extract_messages, parse_chat_payload, parse_turns


def test_teacher_student_쌍이_턴으로_묶인다(chat_payload):
    turns = parse_chat_payload(chat_payload)

    assert len(turns) == 4
    assert [t.turn_index for t in turns] == [0, 1, 2, 3]
    assert turns[0].ai_question == "요즘 학원 생활은 어때?"
    assert turns[0].user_answer == "학원 숙제가 너무 많아요."
    assert turns[3].user_answer == "요즘 잘 못 자요."


def test_date_가_턴에_보존된다(chat_payload):
    turns = parse_chat_payload(chat_payload)
    assert all(t.date == "26.08.31" for t in turns)


def test_teacher가_연달아_오면_마지막_질문과_짝지어진다():
    messages = [
        {"sender": "teacher", "text": "안녕?"},
        {"sender": "teacher", "text": "오늘 기분은 어때?"},
        {"sender": "student", "text": "좋아요."},
    ]
    turns = parse_turns(messages)

    assert len(turns) == 1
    assert turns[0].ai_question == "오늘 기분은 어때?"
    assert turns[0].user_answer == "좋아요."


def test_선행_질문_없는_답변도_턴으로_남는다():
    """대화를 임의로 버리지 않는다 — 질문은 빈 문자열로 둔다."""
    messages = [
        {"sender": "student", "text": "저 왔어요."},
        {"sender": "teacher", "text": "반가워!"},
        {"sender": "student", "text": "네!"},
    ]
    turns = parse_turns(messages)

    assert len(turns) == 2
    assert turns[0].ai_question == ""
    assert turns[0].user_answer == "저 왔어요."
    assert turns[1].ai_question == "반가워!"


def test_답변이_아직_없는_마지막_질문도_턴이_된다():
    messages = [
        {"sender": "teacher", "text": "질문1"},
        {"sender": "student", "text": "답변1"},
        {"sender": "teacher", "text": "질문2"},
    ]
    turns = parse_turns(messages)

    assert len(turns) == 2
    assert turns[1].ai_question == "질문2"
    assert turns[1].user_answer == ""


def test_알_수_없는_sender_는_무시된다():
    messages = [
        {"sender": "system", "text": "세션 시작"},
        {"sender": "teacher", "text": "질문"},
        {"sender": "student", "text": "답변"},
    ]
    turns = parse_turns(messages)

    assert len(turns) == 1
    assert turns[0].ai_question == "질문"


def test_sender_대소문자를_구분하지_않는다():
    turns = parse_turns(
        [{"sender": "Teacher", "text": "Q"}, {"sender": "STUDENT", "text": "A"}]
    )
    assert len(turns) == 1


def test_빈_응답이나_깨진_응답은_빈_목록():
    assert parse_chat_payload(None) == []
    assert parse_chat_payload({}) == []
    assert parse_chat_payload({"data": None}) == []
    assert parse_chat_payload({"data": {"messages": "이건 배열이 아님"}}) == []
    assert parse_chat_payload({"data": {"messages": []}}) == []


def test_messages_안의_비정상_항목은_걸러진다():
    payload = {"data": {"messages": [{"sender": "teacher", "text": "Q"}, "쓰레기", None]}}
    assert len(extract_messages(payload)) == 1


def test_text_가_없으면_빈_문자열로_처리된다():
    turns = parse_turns([{"sender": "teacher"}, {"sender": "student"}])
    assert turns[0].ai_question == ""
    assert turns[0].user_answer == ""
