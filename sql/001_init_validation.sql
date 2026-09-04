-- 전문의 평가 시스템 — 신규 로컬 DB 스키마 (SPEC.md §4)
--
-- 대상 DB: validation_db  (기존 서비스 DB 인 aimie_kids_app / aimie_kids_ai 와 완전히 분리)
-- 실행:    uv run python -m scripts.init_db
--
-- 멱등(idempotent)하게 작성되어 있어 여러 번 실행해도 안전하다.

-- 1. 사용자 (관리자 및 전문의)
CREATE TABLE IF NOT EXISTS users (
    user_id       VARCHAR(100) PRIMARY KEY,
    name          VARCHAR(50)  NOT NULL,
    role          VARCHAR(20)  NOT NULL, -- 'ADMIN', 'DOCTOR'
    password_hash VARCHAR(255) NOT NULL,
    created_at    TIMESTAMP DEFAULT NOW(),
    CONSTRAINT users_role_check CHECK (role IN ('ADMIN', 'DOCTOR'))
);

-- 2. 평가 할당 및 진도 관리 (관리자가 전문의에게 할당)
CREATE TABLE IF NOT EXISTS evaluation_assignments (
    id              SERIAL PRIMARY KEY,
    doctor_id       VARCHAR(100) NOT NULL REFERENCES users(user_id),
    student_id      VARCHAR(100) NOT NULL,        -- 외부 API 의 studentId
    session_id      VARCHAR(100) NOT NULL,        -- 외부 API 의 sessionId
    chat_date       VARCHAR(20)  NOT NULL DEFAULT '',  -- 외부 API 의 date (YY.MM.DD)
    total_turns     INT NOT NULL DEFAULT 0,       -- 해당 세션의 전체 Q&A 세트 수
    completed_turns INT DEFAULT 0,                -- 완료된 Q&A 세트 수
    status          VARCHAR(20) DEFAULT 'PENDING',-- PENDING, IN_PROGRESS, COMPLETED
    created_at      TIMESTAMP DEFAULT NOW(),
    updated_at      TIMESTAMP DEFAULT NOW(),
    completed_at    TIMESTAMP,                    -- 최종 완료 시각 (CSV 의 '완료일시')
    CONSTRAINT assignments_status_check
        CHECK (status IN ('PENDING', 'IN_PROGRESS', 'COMPLETED')),
    -- 같은 전문의에게 같은 (학생, 세션, 날짜) 를 중복 할당하지 않는다.
    CONSTRAINT assignments_unique_target
        UNIQUE (doctor_id, student_id, session_id, chat_date)
);

CREATE INDEX IF NOT EXISTS idx_assignments_doctor ON evaluation_assignments (doctor_id);
CREATE INDEX IF NOT EXISTS idx_assignments_status ON evaluation_assignments (status);

-- 3. Q&A 턴별 전문의 평가 결과 (1:1 매칭 데이터)
CREATE TABLE IF NOT EXISTS doctor_evaluations (
    id              SERIAL PRIMARY KEY,
    assignment_id   INT REFERENCES evaluation_assignments(id) ON DELETE CASCADE,
    evaluation_code VARCHAR(50),  -- 평가 ID (예: KID-001-00)
    turn_index      INT NOT NULL, -- 질문/답변 순서 (0, 1, 2...)
    scale_stage     VARCHAR(100), -- 진단 단계 (예: 1단계 KIDSCREEN-10)
    ai_question     TEXT,         -- 외부 API 에서 가져온 AI 질문
    user_answer     TEXT,         -- 외부 API 에서 가져온 학생 답변
    doctor_score    VARCHAR(50),  -- 전문의 점수/조치 (예: Very (4점))
    doctor_opinion  TEXT,         -- 전문의 판단 이유
    created_at      TIMESTAMP DEFAULT NOW(),
    updated_at      TIMESTAMP DEFAULT NOW(),
    -- 턴 단위 Upsert(ON CONFLICT) 를 위해 반드시 필요하다.
    CONSTRAINT evaluations_unique_turn UNIQUE (assignment_id, turn_index)
);

CREATE INDEX IF NOT EXISTS idx_evaluations_assignment
    ON doctor_evaluations (assignment_id, turn_index);
