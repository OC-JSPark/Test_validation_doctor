-- 할당에 척도(진단 단계)를 저장한다.
--
-- 척도는 턴이 아니라 **세션의 속성**이다. AI 대화 엔진이 세션마다 어떤 척도를
-- 진행했는지 `aimie_kids_ai.checkpoints` 의 channel_values.stage 에 남기므로,
-- 할당을 만들 때 그 값을 옮겨 담아 전문의 화면에서 기본값으로 쓴다.
-- 전문의가 화면에서 다른 척도로 바꾸면 턴별 doctor_evaluations.scale_stage 가 우선한다.

ALTER TABLE evaluation_assignments
    ADD COLUMN IF NOT EXISTS scale_stage VARCHAR(100);
