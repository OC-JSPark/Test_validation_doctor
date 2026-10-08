-- 할당의 데이터 출처를 구분한다 (실제 사용자 데이터 vs AI 테스트 데이터).
--
-- 배경: 기존에는 백엔드 DB 와 매핑된 실제 사용자 데이터만 평가했다.
-- 여기에 AI DB 에만 있는 테스트용 생성 데이터를 평가하는 경로가 추가됐다.
--
-- 두 경로는 날짜를 정하는 방식이 다르다.
--   SERVICE    — 척도검사 DB(sessions) 에서 세션·날짜를 미리 알 수 있다.
--   AI_PREVIEW — 세션 목록 API 가 없다. 전문의가 열 때
--                `GET /api-kids/dev/ai-preview/latest-date` 로 최신 날짜를 받는다.
--
-- 그래서 전문의 화면이 "이 할당이 어느 쪽인지" 를 알아야 하고, 그 정보는
-- 할당이 만들어진 시점에 정해지므로 할당 행에 남긴다.
-- (학생 ID 만으로는 구분할 수 없다. 둘 다 같은 형태의 해시다.)
--
-- 기존 행은 전부 실제 사용자 데이터이므로 DEFAULT 로 'SERVICE' 가 채워진다.

ALTER TABLE evaluation_assignments
    ADD COLUMN IF NOT EXISTS source VARCHAR(20) NOT NULL DEFAULT 'SERVICE';

-- CHECK 제약은 ADD CONSTRAINT IF NOT EXISTS 가 없어 카탈로그를 직접 본다.
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'assignments_source_check'
    ) THEN
        ALTER TABLE evaluation_assignments
            ADD CONSTRAINT assignments_source_check
            CHECK (source IN ('SERVICE', 'AI_PREVIEW'));
    END IF;
END $$;

-- 관리자 화면에서 출처별로 묶어 보여주기 위한 인덱스.
CREATE INDEX IF NOT EXISTS idx_assignments_source
    ON evaluation_assignments (source);
