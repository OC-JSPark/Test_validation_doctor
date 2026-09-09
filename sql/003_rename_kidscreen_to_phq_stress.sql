-- 1단계 척도명을 KIDSCREEN-10 → PHQ-stress 로 개명한다.
--
-- 같은 척도(대화 엔진의 stage='stress')를 가리키는 이름만 바뀐 것이라,
-- 이미 저장된 평가의 척도명도 함께 옮겨야 CSV 추출 결과가 한 이름으로 통일된다.
-- 그대로 두면 예전 평가만 'KIDSCREEN-10' 으로 남아 집계가 갈린다.
--
-- 점수 라벨(doctor_score)은 건드리지 않는다. 5점 척도로 매긴 과거 평가를
-- 3점 척도로 자동 환산하는 것은 임상적 판단이라 사람이 결정해야 한다.

UPDATE doctor_evaluations
SET scale_stage = '1단계 PHQ-stress'
WHERE scale_stage LIKE '%KIDSCREEN%';

UPDATE evaluation_assignments
SET scale_stage = '1단계 PHQ-stress'
WHERE scale_stage LIKE '%KIDSCREEN%';
