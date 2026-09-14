"""척도별 점수 선택지 · stage → 척도 판별."""

from __future__ import annotations

from datetime import date

import pytest

from app.config import (
    DEFAULT_SCORE_OPTIONS,
    PHQ_STRESS_SCORE_OPTIONS,
    Settings,
    get_settings,
)
from app.models import ScaleSession
from app.session_directory import list_sessions


@pytest.fixture
def settings() -> Settings:
    return get_settings()


# --- 척도별 점수 선택지 -----------------------------------------------------


def test_PHQ_stress_는_3점_척도다(settings):
    options = settings.score_options_for("1단계 PHQ-stress")

    assert options == PHQ_STRESS_SCORE_OPTIONS
    assert len(options) == 3
    assert options[0].startswith("Not at all (0")
    assert options[1].startswith("Bothered a little (1")
    assert options[2].startswith("Bothered a lot (2")


def test_다른_척도는_기본_선택지를_쓴다(settings):
    """PHQ-2 / PHQ-A 는 설정된 기본 선택지를 그대로 쓴다 (.env 로 덮어쓸 수 있다)."""
    for scale in ("2단계 PHQ-2", "3단계 PHQ-A"):
        assert settings.score_options_for(scale) == settings.score_options
        assert settings.score_options_for(scale) != PHQ_STRESS_SCORE_OPTIONS


def test_척도를_모르면_기본_선택지(settings):
    assert settings.score_options_for(None) == settings.score_options
    assert settings.score_options_for("") == settings.score_options
    assert settings.score_options_for("듣도보도 못한 척도") == settings.score_options


def test_모든_척도가_0점부터_시작한다():
    """PHQ 채점 관례. 척도마다 최고점만 다르다 (stress 2점, 나머지 4점)."""
    assert DEFAULT_SCORE_OPTIONS[0].startswith("Not at all (0")
    assert PHQ_STRESS_SCORE_OPTIONS[0].startswith("Not at all (0")


def test_척도명_접두사가_붙어도_매칭된다(settings):
    """'1단계 PHQ-stress' 처럼 앞에 단계 표기가 붙어도 알아봐야 한다."""
    assert settings.score_options_for("PHQ-stress") == PHQ_STRESS_SCORE_OPTIONS
    assert settings.score_options_for("phq-stress") == PHQ_STRESS_SCORE_OPTIONS


def test_PHQ_stress_와_PHQ_2_가_섞이지_않는다(settings):
    """'PHQ-' 로 시작하는 이름이 여럿이라 부분 매칭이 잘못 걸리면 안 된다."""
    assert settings.score_options_for("2단계 PHQ-2") != PHQ_STRESS_SCORE_OPTIONS
    assert settings.score_options_for("3단계 PHQ-A") != PHQ_STRESS_SCORE_OPTIONS


# --- stage → 척도 판별 ------------------------------------------------------


def test_대화엔진_stage_를_척도명으로_바꾼다(settings):
    assert settings.scale_for_stage("stress") == "1단계 PHQ-stress"


def test_대소문자와_공백을_흡수한다(settings):
    assert settings.scale_for_stage("  STRESS  ") == settings.scale_for_stage("stress")


def test_모르는_stage_는_None(settings):
    """opening·finish 처럼 척도가 아닌 stage 는 판별하지 않는다."""
    assert settings.scale_for_stage("opening") is None
    assert settings.scale_for_stage("finish") is None
    assert settings.scale_for_stage(None) is None
    assert settings.scale_for_stage("") is None


# --- 개명 마이그레이션 -------------------------------------------------------


@pytest.mark.db
def test_옛_척도명이_DB_에_남아있지_않다(conn):
    """KIDSCREEN-10 → PHQ-stress 개명 후, 저장된 평가도 새 이름을 쓴다.

    이름만 바뀐 같은 척도라 CSV 집계가 두 이름으로 갈리면 안 된다
    (sql/003_rename_kidscreen_to_phq_stress.sql).
    """
    row = conn.execute(
        "SELECT COUNT(*) AS n FROM doctor_evaluations WHERE scale_stage LIKE '%KIDSCREEN%'"
    ).fetchone()
    assert row["n"] == 0

    row = conn.execute(
        "SELECT COUNT(*) AS n FROM evaluation_assignments WHERE scale_stage LIKE '%KIDSCREEN%'"
    ).fetchone()
    assert row["n"] == 0


# --- 실제 DB 에서 척도가 실려 오는지 ----------------------------------------


@pytest.mark.db
def test_실제_세션에_stage_와_척도가_붙는다(session_conn):
    row = session_conn.execute(
        """
        SELECT s.user_id
        FROM sessions s
        JOIN checkpoints c
          ON c.user_id = s.user_id AND c.date = s.date AND c.session_id = s.session_id
        WHERE c.checkpoint_json -> 'channel_values' ->> 'stage' = 'stress'
        LIMIT 1
        """
    ).fetchone()
    if row is None:
        pytest.skip("stage='stress' 인 세션이 로컬 덤프에 없습니다.")

    sessions = list_sessions([row["user_id"]], session_conn)
    stressed = [s for s in sessions if s.stage == "stress"]

    assert stressed, "stage 가 실려 오지 않았습니다."
    assert all(s.scale_stage == "1단계 PHQ-stress" for s in stressed)


@pytest.mark.db
def test_척도를_모르는_세션도_목록에_남는다(session_conn):
    """checkpoints 가 없거나 stage 가 opening 이어도 검사 자체는 할당 대상이다."""
    rows = session_conn.execute("SELECT DISTINCT user_id FROM sessions LIMIT 3").fetchall()
    sessions = list_sessions([r["user_id"] for r in rows], session_conn)

    assert sessions
    # scale_stage 가 None 인 세션이 있어도 목록에서 빠지지 않는다
    assert all(isinstance(s, ScaleSession) for s in sessions)
    assert all(isinstance(s.session_date, date) for s in sessions)
