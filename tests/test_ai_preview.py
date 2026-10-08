"""AI 테스트 데이터 응답 파싱 (API 정의서 4.n.n "AI 데이터 확인").

AI DB 에만 있는 생성 데이터라 응답이 느슨하다. 이름이 해시값이거나
loginId/grade 가 null 인 경우가 많아서, 파싱이 빠진 값을 견뎌야 한다.
"""

from __future__ import annotations

from datetime import date

from app.ai_preview import (
    filter_students,
    normalize_chat_date,
    parse_activities,
    parse_date,
    parse_latest_date,
    parse_report,
    parse_students,
    synthesize_session_id,
)
from app.models import AIPreviewDates, AIStudent

# 정의서의 응답 예시.
_SAMPLE = {
    "success": True,
    "code": "OK",
    "message": "Success",
    "data": [
        {
            "id": 1,
            "studentId": "0fffe77c82d450b0115e12915be4d9fe",
            "loginId": None,
            "userSeq": None,
            "userUuid": "0fffe77c82d450b0115e12915be4d9fe",
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
            "userSeq": None,
            "userUuid": "1fdceef6c065d0690eb2177a000cdfff",
            "name": "1fdceef6c065d0690eb2177a000cdfff",
            "grade": 0,
            "level": 2,
            "levelText": "2단계",
            "class": 0,
        },
    ],
}


# --- 학생 목록 --------------------------------------------------------------


def test_정의서_예시_응답을_읽는다():
    students = parse_students(_SAMPLE)

    assert len(students) == 2
    assert students[0].student_id == "0fffe77c82d450b0115e12915be4d9fe"
    assert students[1].student_id == "1fdceef6c065d0690eb2177a000cdfff"
    assert students[0].level_text == "1단계"


def test_말머리_번호가_1부터_매겨진다():
    students = parse_students(_SAMPLE)

    assert [s.tag for s in students] == ["[AIuser01]", "[AIuser02]"]


def test_번호는_API_의_id_순서를_따른다():
    """응답 순서가 뒤바뀌어도 같은 학생이 같은 번호를 받아야 한다.

    번호가 호출마다 달라지면, 관리자가 'AIuser03' 을 골랐다가
    다시 들어왔을 때 다른 학생을 보게 된다.
    """
    shuffled = {"data": [_SAMPLE["data"][1], _SAMPLE["data"][0]]}

    students = parse_students(shuffled)

    assert students[0].source_id == 1
    assert students[0].tag == "[AIuser01]"
    assert students[1].source_id == 2
    assert students[1].tag == "[AIuser02]"


def test_번호가_두_자리로_채워진다():
    payload = {"data": [{"id": i, "studentId": f"s{i}"} for i in range(1, 12)]}

    students = parse_students(payload)

    assert students[0].tag == "[AIuser01]"
    assert students[8].tag == "[AIuser09]"
    assert students[9].tag == "[AIuser10]"
    assert students[10].tag == "[AIuser11]"


def test_백명이_넘으면_자릿수가_늘어난다():
    """두 자리로 고정하면 100번째부터 번호가 겹친다."""
    payload = {"data": [{"id": i, "studentId": f"s{i}"} for i in range(1, 102)]}

    students = parse_students(payload)

    assert students[99].tag == "[AIuser100]"
    assert students[100].tag == "[AIuser101]"


def test_studentId_가_없으면_버린다():
    """할당에 저장할 값이 없으면 목록에 띄워도 쓸 수 없다."""
    payload = {"data": [{"id": 1, "studentId": None}, {"id": 2, "studentId": "ok"}]}

    students = parse_students(payload)

    assert [s.student_id for s in students] == ["ok"]
    assert students[0].tag == "[AIuser01]"  # 번호는 살아남은 것부터 다시 센다


def test_studentId_가_없으면_userUuid_로_떨어진다():
    payload = {"data": [{"id": 1, "userUuid": "uuid-1"}]}

    assert parse_students(payload)[0].student_id == "uuid-1"


def test_id_가_없는_항목은_뒤로_가되_순서는_유지된다():
    payload = {
        "data": [
            {"studentId": "no-id-a"},
            {"id": 5, "studentId": "has-id"},
            {"studentId": "no-id-b"},
        ]
    }

    students = parse_students(payload)

    assert [s.student_id for s in students] == ["has-id", "no-id-a", "no-id-b"]


def test_응답이_비거나_깨져도_빈_목록():
    assert parse_students(None) == []
    assert parse_students({}) == []
    assert parse_students({"data": None}) == []
    assert parse_students({"data": "문자열"}) == []
    assert parse_students({"data": [None, 3, "x"]}) == []


def test_봉투_없이_리스트만_와도_읽는다():
    assert len(parse_students({"data": _SAMPLE["data"]})) == 2


# --- 표시 이름 --------------------------------------------------------------


def test_이름이_해시값이면_이름으로_쓰지_않는다():
    """정의서 예시처럼 name 과 studentId 가 같은 해시인 경우가 많다."""
    student = parse_students(_SAMPLE)[0]

    assert student.display_name == "0fffe77c…"
    assert student.label.startswith("[AIuser01] 0fffe77c…")


def test_진짜_이름이_있으면_그대로_쓴다():
    payload = {"data": [{"id": 1, "studentId": "abc123", "name": "김테스트"}]}

    assert parse_students(payload)[0].display_name == "김테스트"


def test_이름이_없으면_loginId_로_떨어진다():
    payload = {"data": [{"id": 1, "studentId": "abc123", "loginId": "ai_user_1"}]}

    assert parse_students(payload)[0].display_name == "ai_user_1"


def test_말머리가_항상_맨_앞이다():
    """일반 학생 목록과 섞여도 눈으로 바로 갈려야 한다."""
    for student in parse_students(_SAMPLE):
        assert student.label.startswith("[AIuser")


def test_학년과_반이_0이면_표시하지_않는다():
    """AI 생성 데이터는 grade/class 가 0 으로 채워져 온다. '0학년 0반' 은 무의미하다."""
    student = parse_students(_SAMPLE)[0]

    assert "학년" not in student.label
    assert student.label == "[AIuser01] 0fffe77c… · 1단계"


# --- 검색 ------------------------------------------------------------------


def test_말머리_번호로_검색된다():
    students = parse_students(_SAMPLE)

    assert [s.tag for s in filter_students(students, "AIuser02")] == ["[AIuser02]"]


def test_학생ID_로_검색된다():
    students = parse_students(_SAMPLE)

    found = filter_students(students, "1fdceef6")
    assert len(found) == 1
    assert found[0].student_id.startswith("1fdceef6")


def test_검색어가_비면_전체():
    students = parse_students(_SAMPLE)

    assert filter_students(students, "") == students
    assert filter_students(students, "   ") == students


# --- 최신 날짜 --------------------------------------------------------------


def test_정의서_예시_날짜를_읽는다():
    payload = {
        "success": True,
        "data": {
            "date": "2026-09-18",
            "reportDate": "2026-09-18",
            "chatDate": "2026-09-22",
        },
    }

    dates = parse_latest_date(payload)

    assert dates.chat_date == date(2026, 9, 22)
    assert dates.report_date == date(2026, 9, 18)


def test_대화_조회용_날짜는_YY_MM_DD_로_바뀐다():
    """대화 API 는 `26.09.22` 형식을 받는다. 원본은 `2026-09-22` 다."""
    dates = parse_latest_date({"data": {"chatDate": "2026-09-22"}})

    assert dates.chat_date_param == "26.09.22"


def test_chatDate_가_없으면_date_로_떨어진다():
    dates = parse_latest_date({"data": {"date": "2026-09-18", "chatDate": None}})

    assert dates.chat_date == date(2026, 9, 18)
    assert dates.report_date == date(2026, 9, 18)


def test_날짜가_하나도_없으면_비어_있다():
    assert parse_latest_date({"data": {}}).is_empty
    assert parse_latest_date(None).is_empty
    assert parse_latest_date({"data": "문자열"}).is_empty
    assert AIPreviewDates().chat_date_param == ""


def test_읽을_수_없는_날짜는_버린다():
    """형식이 틀린 날짜로 조회하면 엉뚱한 대화가 온다. 추측하지 않는다."""
    assert parse_date("어제") is None
    assert parse_date("2026-13-45") is None
    assert parse_date("") is None
    assert parse_date(None) is None


def test_여러_날짜_형식을_받아들인다():
    assert parse_date("2026-09-22") == date(2026, 9, 22)
    assert parse_date("2026/09/22") == date(2026, 9, 22)
    assert parse_date("26.09.22") == date(2026, 9, 22)


# --- 리포트 ----------------------------------------------------------------


def test_리포트_본문을_읽는다():
    payload = {"success": True, "data": "1. 정신건강 점수 분석\\n- 스트레스 점수는 0.0점"}

    report = parse_report(payload)

    assert report.startswith("1. 정신건강 점수 분석")


def test_이스케이프된_개행이_실제_개행이_된다():
    """원본에 `\\n` 이 문자 그대로 들어 있어 화면에서 줄바꿈이 먹지 않는다."""
    report = parse_report({"data": "첫째 줄\\n둘째 줄"})

    assert report == "첫째 줄\n둘째 줄"
    assert "\\n" not in report


def test_리포트가_없으면_빈_문자열():
    assert parse_report(None) == ""
    assert parse_report({"data": None}) == ""
    assert parse_report({}) == ""


# --- 활동 이력 (= 척도검사 목록) --------------------------------------------

# 운영 관리자 화면이 쓰는 응답 형태 (실측).
_ACTIVITY = {
    "success": True,
    "code": "OK",
    "data": [
        {
            "date": "26.09.18",
            "sessionId": "ef2207e1-cba7-42c8-9d1e-000000000001",
            "level": 1,
            "levelText": "스트레스",
            "concern": "학교, 친구",
            "chatTime": "131분",
        },
        {
            "date": "26.09.11",
            "sessionId": "8f1d049a-0432-4f9a-8e2b-000000000002",
            "level": 3,
            "levelText": "우울증",
            "concern": "기타",
            "chatTime": "3분",
        },
    ],
}


def _scale(level):
    return {1: "1단계 PHQ-stress", 2: "2단계 PHQ-2", 3: "3단계 PHQ-A"}.get(level)


def test_활동_이력을_검사_목록으로_읽는다():
    acts = parse_activities(_ACTIVITY, "stu-1", scale_for_level=_scale)

    assert len(acts) == 2
    assert acts[0].chat_date == "26.09.18"
    assert acts[0].session_id == "ef2207e1-cba7-42c8-9d1e-000000000001"
    assert acts[0].concern == "학교, 친구"
    assert all(a.student_id == "stu-1" for a in acts)


def test_level_로_척도를_판별한다():
    acts = parse_activities(_ACTIVITY, "stu-1", scale_for_level=_scale)

    assert acts[0].scale_stage == "1단계 PHQ-stress"
    assert acts[1].scale_stage == "3단계 PHQ-A"


def test_미분류_검사는_척도를_비워_둔다():
    """level 0 은 아직 척도가 정해지지 않은 검사다. 전문의가 고른다."""
    payload = {"data": [{"date": "26.08.20", "sessionId": "s1", "level": 0}]}

    assert parse_activities(payload, "stu-1", scale_for_level=_scale)[0].scale_stage is None


def test_최신_검사가_먼저_온다():
    acts = parse_activities(_ACTIVITY, "stu-1")

    assert [a.chat_date for a in acts] == ["26.09.18", "26.09.11"]


def test_날짜가_없는_행은_버린다():
    """날짜가 없으면 어느 날짜 대화를 평가할지 정해지지 않는다."""
    payload = {"data": [{"sessionId": "s1"}, {"date": "26.08.20", "sessionId": "s2"}]}

    acts = parse_activities(payload, "stu-1")

    assert [a.session_id for a in acts] == ["s2"]


def test_같은_세션이_두_번_와도_한_건():
    payload = {"data": [_ACTIVITY["data"][0], _ACTIVITY["data"][0]]}

    assert len(parse_activities(payload, "stu-1")) == 1


def test_응답이_비거나_깨져도_빈_목록():
    assert parse_activities(None, "stu-1") == []
    assert parse_activities({"data": None}, "stu-1") == []
    assert parse_activities({"data": "문자열"}, "stu-1") == []
    assert parse_activities({"data": [None, 3]}, "stu-1") == []


def test_네자리_연도_날짜를_두자리로_맞춘다():
    """대화 조회 API 는 `26.08.20` 형식을 받는다."""
    assert normalize_chat_date("2026.08.20") == "26.08.20"
    assert normalize_chat_date("26.08.20") == "26.08.20"
    assert normalize_chat_date("2026-08-20") == "26.08.20"
    assert normalize_chat_date("어제") == ""
    assert normalize_chat_date(None) == ""


# --- 세션 ID 가 없을 때 --------------------------------------------------------


def test_세션ID_가_없으면_만들어_채운다():
    payload = {"data": [{"date": "26.08.20", "sessionId": None}]}

    session_id = parse_activities(payload, "stu-1")[0].session_id

    assert session_id
    assert len(session_id) == 36  # 기존 세션과 같은 UUID 모양이라 화면에서 튀지 않는다
    assert session_id.count("-") == 4


def test_만들어낸_세션ID_는_매번_같다():
    """난수로 만들면 같은 검사를 다시 할당할 때마다 새 ID 가 나와,
    중복 방지 제약을 빠져나가 같은 검사가 여러 건 쌓인다."""
    payload = {"data": [{"date": "26.08.20"}]}

    first = parse_activities(payload, "stu-1")[0].session_id
    second = parse_activities(payload, "stu-1")[0].session_id

    assert first == second
    assert first == synthesize_session_id("stu-1", "26.08.20")


def test_학생과_날짜가_다르면_다른_세션ID():
    a = synthesize_session_id("stu-1", "26.08.20")
    b = synthesize_session_id("stu-2", "26.08.20")
    c = synthesize_session_id("stu-1", "26.08.21")

    assert len({a, b, c}) == 3


def test_실제_세션ID_가_있으면_그것을_쓴다():
    """원본과 대조할 수 있는 진짜 ID 가 있으면 만들어낸 값으로 덮지 않는다."""
    acts = parse_activities(_ACTIVITY, "stu-1")

    assert acts[0].session_id == "ef2207e1-cba7-42c8-9d1e-000000000001"


def test_AIStudent_를_직접_만들_수도_있다():
    """파싱을 거치지 않는 호출부(테스트·수동 조립)를 위한 최소 계약."""
    student = AIStudent(student_id="abc", seq=7)

    assert student.tag == "[AIuser07]"
    assert student.label == "[AIuser07] abc…"
