# Test_validation_doctor

AIMIE Kids 하루톡 대화에 대한 **전문의 평가 시스템** (Streamlit + PostgreSQL).

관리자가 전문의에게 학생/세션 평가를 할당하고, 전문의가 Q&A 턴 단위로 점수·소견을
입력하며, 완료된 평가를 CSV 로 추출한다.

## 아키텍처

```
[외부 서비스 API]  ──(GET /api-kids/risk-students/student/chat, 읽기 전용)──┐
                                                                          ▼
                                          [Streamlit 앱] ── teacher/student 메시지를
                                                 │          Q&A 턴으로 파싱
                                                 ▼
                                    [신규 로컬 DB: validation_db]
                              users / evaluation_assignments / doctor_evaluations
```

- **대화 내역**은 외부 API 호출로만 읽는다 (`aimie_kids_ai` 에 직접 붙지 않는다).
- **학생 명부**는 학생 DB(`aimie_kids_app`)를 **읽기 전용**으로 조회한다.
  관리자 화면의 학생 선택 목록이 여기서 나온다 — `app/student_directory.py` 한 곳으로만 접근하며,
  커넥션은 `read_only` 로 열려 쓰기 쿼리가 DB 단계에서 거부된다.
  추후 인스턴스 DB 로 옮길 때는 `STUDENT_SOURCE_DATABASE_URL` 만 바꾸면 된다.
- 이 시스템이 **만들어내는** 데이터는 전부 신규 DB `validation_db` 에만 저장한다.

## 빠른 시작

```bash
# 1) DB 컨테이너 기동
docker compose up -d test-db

# 2) 평가용 데이터베이스 생성 (최초 1회)
docker exec local-postgres psql -U aimieapi -d postgres -c "CREATE DATABASE validation_db"

# 3) 환경변수 준비
cp .env.example .env   # 외부 API 계정(EXTERNAL_API_LOGIN_ID/PASSWORD)을 채운다

# 4) 의존성 설치 + 스키마 + 데모 계정
uv sync
uv run python -m scripts.init_db --seed

# 5) 외부 API 연동 점검 (로그인 → 대화 조회 → 턴 파싱)
uv run python -m scripts.check_api --student <studentId> --date 26.08.31

# 6) 앱 실행
uv run streamlit run Test_validation_doctor.py
```

### 외부 API 접속 정보

| 항목 | 값 |
| --- | --- |
| 호스트 | `https://admin-dev.aimie-m.com` |
| 로그인 | `POST /api-kids/adm/login` (`loginType: TEACHER`) |
| 대화 조회 | `GET /api-kids/risk-students/student/chat` (Bearer 토큰) |

앱은 토큰이 없으면 `.env` 의 `EXTERNAL_API_LOGIN_ID` / `EXTERNAL_API_PASSWORD` 로
자동 로그인해 `accessToken` 을 받아 사용한다.
게이트웨이 프리픽스가 바뀌면 `EXTERNAL_API_LOGIN_PATH` / `EXTERNAL_API_CHAT_PATH` 로 조정한다.

> `dev.aimie-m.com` 은 nginx 테스트 페이지만 떠 있어 모든 API 경로가 404 다. `admin-dev` 를 쓸 것.

데모 계정: `admin / admin1234`, `doctor01~03 / doctor1234`
(비밀번호는 `.env` 의 `SEED_ADMIN_PASSWORD`, `SEED_DOCTOR_PASSWORD` 로 바꿀 수 있다.)

## 테스트

```bash
uv run pytest                # 전체
uv run pytest -m "not db"    # DB 없이 순수 로직만
```

DB 테스트는 실제 `validation_db` 에 붙어 트랜잭션 롤백으로 격리한다.
컨테이너가 꺼져 있으면 안내 메시지와 함께 실패한다.

## 구조

| 경로 | 역할 |
| --- | --- |
| `Test_validation_doctor.py` | Streamlit 진입점 (로그인 → 역할별 화면 라우팅) |
| `app/parsing.py` | 외부 API 메시지 → Q&A 턴 파싱 (순수 함수) |
| `app/external_api.py` | 외부 대화 API 클라이언트 (Read-only) |
| `app/student_directory.py` | 학생 명부 조회 (학생 DB, Read-only) |
| `app/repositories/` | SQL 접근 계층 (커밋하지 않음) |
| `app/services/` | SPEC §6 API 컨트롤러에 대응하는 서비스 함수 |
| `app/ui/` | 로그인 / 관리자 / 전문의 화면 |
| `sql/` | 스키마 마이그레이션 (멱등) |
| `scripts/init_db.py` | 스키마 생성 + 데모 계정 시드 |

자세한 기능 명세는 `SPEC.md`, 작업 규칙은 `CLAUDE.md` 참고.
