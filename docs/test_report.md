# 테스트 결과

## 실행 방법

```bash
pip install -r requirements-dev.txt
python -m pytest -q                                   # 전체 테스트
python -m pytest -q --cov=factorylens --cov=app       # 커버리지 포함

# PostgreSQL에서도 실행 (테스트 전용 DB. 테스트마다 테이블을 지운다)
FACTORYLENS_TEST_DATABASE_URL=postgresql://postgres:postgres@localhost:5432/factorylens_test python -m pytest -q
```

## 결과 요약 (2026-10-23 실행)

- 환경: Linux, Python 3.13.17, PostgreSQL 16.15, pandas 2.3.3, streamlit 1.65.0, psycopg 3.3.6, pytest 9.1.1, pytest-cov 7.1.0
- 결과: **208개 통과, 0개 실패** (테스트 168개 + 집계·적재 테스트 40개를 PostgreSQL에서 한 번 더 실행)
- PostgreSQL 없이 실행하면 `168 passed, 40 skipped`입니다. CI는 PostgreSQL 서비스를 띄우고, 하나라도 건너뛰면 실패로 처리합니다.
- 이전 결과(2026-10-22, Windows 10, Python 3.10.11): 128개 통과, 커버리지 98%

| 파일 | SQLite | PostgreSQL | 대상 |
|---|---|---|---|
| `tests/test_validation.py` | 64 | - | CSV 읽기, 행·중복·교차 검증, 분석 결과 파일 오업로드 안내 |
| `tests/test_metrics.py` | 33 | 26 | 집계 SQL, 불량률, 필터, **파레토 누적 비중, p 관리도** |
| `tests/test_ingest.py` | 17 | 14 | **적재·배치 기록, 재업로드 멱등성(K1), 기존 LOT 충돌(K2), 롤백, 명령줄 적재** |
| `tests/test_db.py` | 17 | - | 스키마, 적재, DB 제약, **DB 주소·저장 모드, 중첩 트랜잭션, PostgreSQL 결과 타입 변환** |
| `tests/test_app.py` | 16 | - | Streamlit 화면 흐름 (AppTest), **누적 모드 화면, 관리 한계 이탈 표시, DB 연결 실패 문구** |
| `tests/test_export.py` | 10 | - | 다운로드 CSV (BOM, 수식 주입 방지) |
| `tests/test_invalid_examples.py` | 10 | - | `data/invalid_examples/` 시나리오별 기대 결과 |
| `tests/test_smoke.py` | 1 | - | 패키지 import |

### 커버리지 (PostgreSQL 포함 실행)

| 모듈 | 구문 수 | 커버리지 |
|---|---|---|
| `factorylens/validation.py` | 185 | 100% |
| `factorylens/metrics.py` | 87 | 100% |
| `factorylens/db.py` | 106 | 100% |
| `factorylens/export.py` | 12 | 100% |
| `factorylens/ingest.py` | 117 | 97% |
| `app.py` | 276 | 95% |
| **합계** | 783 | **98%** |

실행되지 않은 줄은 아래와 같으며, 수동 점검과 다른 테스트로 확인합니다.
- `app.py`: 파일 업로드 버튼 처리(AppTest가 파일 업로드를 흉내 낼 수 없음), DB 적재 예외 처리(2차 방어선, 같은 경로는 `test_db_error_rolls_back_whole_batch`가 확인), 누적 모드의 거부 배치 안내 문구
- `ingest.py`: 명령줄 적재 중 DB 오류 출력(같은 예외 처리는 `ingest()` 테스트가 확인)

### 실행 환경별 확인

| 확인 | 결과 |
|---|---|
| 데모 모드 브라우저 실행 (Playwright 스크린샷, `docs/images/`) | ☑ 샘플: 생산 288,347, 불량률 2.23%, 탭 5개, 파레토 상위 7개 80.2%, PAINT 관리도 중심선 3.68%·이탈 0일 |
| 누적 모드 브라우저 실행 (PostgreSQL 16) | ☑ 샘플 적재 → 같은 파일 재적재(534 LOT 건너뜀) → 오류 예시 05(5행 제외) → 빈 파일(거부) 순서로 배치 4건 기록, 적재 이력·배치 상세 표시 |
| 깨끗한 가상환경에 `requirements.txt`만 설치 + Dockerfile이 복사하는 파일만으로 실행 | ☑ 명령줄 적재와 앱 상태 확인(`/_stcore/health`) 정상 |
| `docker compose config` 문법 검사 | ☑ |
| Docker 이미지 빌드 | ☐ 작업 환경에서 Docker Hub 접근이 막혀 미실행. GitHub Actions의 `docker` 작업이 빌드·실행·적재를 확인 |

## 핵심 예외 상황과 검증 테스트

| 구분 | 상황 | 처리 | 대표 테스트 |
|---|---|---|---|
| 파일 | 0바이트, 공백만 있음, BOM만 있음 | 파일 거부 | `test_empty_bytes_rejected`, `test_bom_only_file_rejected` |
| 파일 | UTF-8, BOM, CP949 이외의 인코딩 | 파일 거부 | `test_undecodable_bytes_rejected` |
| 파일 | 열 개수가 다른 줄, 닫히지 않은 따옴표 | 파일 거부 (줄 번호 표시) | `test_first_row_with_too_many_fields_rejected` |
| 파일 | 필수 컬럼 누락, 컬럼명 중복 | 파일 거부 | `test_missing_required_column_rejected` |
| 파일 | 헤더만 있음 | production은 거부, defects는 "불량 0건"으로 허용 | `test_header_only_rejected`, `test_defects_header_only_means_no_defects` |
| 행 | 결측, 공백만 있는 값 | 행 제외 | `test_missing_value_row_excluded_with_line_and_column` |
| 행 | 음수, 소수, 문자, 상한 초과 수량 | 행 제외 | `test_check_quantity_rejects` |
| 행 | 형식 오류 날짜, 존재하지 않는 날짜 | 행 제외 | `test_check_date` |
| 중복 | 완전중복 | 첫 행만 반영 + 경고 | `test_exact_duplicate_keeps_first_with_warning` |
| 중복 | 키는 같고 값이 다름 | 그 키의 행 전부 제외 | `test_key_conflict_excludes_all_rows_of_that_key` |
| 중복 | 충돌 행 중 하나가 타입 오류 | 그래도 전부 제외 (우회 통과 방지) | `test_conflict_is_checked_before_type_errors` |
| 교차 | production에 없는 LOT의 불량 | 불량 행 제외 | `test_orphan_defect_rows_excluded` |
| 교차 | 불량 합계 > 생산량 | 해당 LOT의 생산·불량 모두 제외 | `test_lot_with_more_defects_than_produced_is_fully_excluded` |
| 0건 | 검증 후 유효 생산 데이터 0건 | 적재 중단, 오류 목록 유지 | `test_all_production_rows_invalid_is_fatal_but_keeps_issues` |
| 0건 | 생산량 0 (분모 0) | 불량률 NULL → `-` | `test_safe_rate`, `test_by_process_values_and_order` |
| 0건 | 필터 결과 0건 | 0과 NULL 반환, 화면에 안내 | `test_summary_kpi_with_no_matching_rows`, `test_empty_selection_shows_message_instead_of_error` |
| 집계 | 불량 유형이 여러 개인 LOT | 생산량 1회만 합산 (팬아웃 방지) | `test_by_process_has_no_join_fanout` |
| 집계 | 그룹 불량률 | 가중 평균 | `test_group_rate_is_weighted_not_simple_average` |
| 보안 | 필터 값에 SQL 삽입 시도 | 값으로만 비교 | `test_filter_values_are_parameters_not_sql` |
| 보안 | `=`로 시작하는 텍스트를 CSV로 내보냄 | `'` 접두어 | `test_sanitize_cell` |
| 보안 | 처리되지 않은 예외 발생 | 예외 종류·메시지·트레이스를 숨기고 일반 문구만 표시 | `test_uncaught_exception_details_are_redacted` |
| 성능 | 20만 행(7MB) 검증 | 약 2초 (개선 전 8.4초) | 수동 측정 |
| 적재 | 같은 파일 재업로드 | 모든 LOT 건너뜀, 합계 그대로 (멱등성) | `test_reupload_same_file_is_idempotent`, `test_persistent_mode_reupload_is_idempotent` |
| 적재 | 기존 LOT와 생산량·날짜·불량 수량·불량 유형 구성이 다름 | 새 LOT 행 제외, 기존 값 유지, 배치 번호와 함께 사유 기록 | `test_conflicting_lot_excluded_and_existing_kept` (5가지 경우) |
| 적재 | 일부는 기존, 일부는 신규 LOT | 신규만 적재 | `test_overlapping_upload_inserts_only_new_lots` |
| 적재 | 파일 거부된 업로드 | 데이터 없이 'rejected' 배치와 사유 기록 | `test_rejected_upload_recorded_without_data` |
| 적재 | 적재 도중 DB 제약 위반 | 배치 기록까지 전부 롤백, 화면에는 일반 문구 | `test_db_error_rolls_back_whole_batch` |
| 적재 | 기존 LOT 조회가 500개 단위를 넘음 | 나눠 조회해도 정확히 분류 | `test_large_upload_spans_query_chunks` |
| 파레토 | 반올림한 비중의 합이 100이 아님 (33.33×3) | 누적 비중은 수량 누적합으로 계산해 마지막 값 100.0 | `test_cumulative_share_uses_quantities_not_rounded_shares` |
| 관리도 | 하루만 불량이 급증 | 그날만 이탈 표시 (손계산 UCL 3.722%) | `test_p_chart_limits_and_out_of_control`, `test_p_chart_out_of_control_day_is_reported` |
| 관리도 | p̄ − 3σ < 0 | 하한 0 | `test_p_chart_lower_limit_not_negative` |
| 관리도 | 사이드바에서 여러 공정 선택 | 지정한 공정 하나로만 계산 | `test_p_chart_ignores_process_filter_from_sidebar` |
| 보안 | 누적 DB 접속 실패 | 접속 주소·비밀번호 없이 일반 문구만 표시 | `test_persistent_mode_db_unreachable_shows_message` |
| 화면 | 생산량이 모두 0 / 불량이 모두 0 | 축이 깨진 차트 대신 안내 문구 | `test_rate_charts_replaced_by_message` |

## 테스트가 잡아낸 문제

1. **pandas가 초과 열을 조용히 index로 사용**
   - 첫 데이터 행의 열이 헤더보다 많으면 pandas는 오류 없이 남는 열을 index로 씁니다.
   - `header=None`으로 읽도록 바꾸고 회귀 테스트를 추가했습니다.
2. **완전중복 검사 속도**
   - 행 단위 반복(`iterrows`)으로 20만 행에 8.4초가 걸렸습니다.
   - pandas 연산으로 바꿔 2.0초로 줄였습니다.
3. **SQL NULL과 pandas NaN**
   - 집계 결과의 NULL은 pandas에서 NaN으로 읽힙니다.
   - 화면에서는 `-`, CSV에서는 빈 칸으로 표시되도록 처리했습니다.
4. **PostgreSQL에서 정렬 SQL 오류** (2026-10-23, PostgreSQL 지원 추가 중)
   - `ORDER BY defect_rate_pct IS NULL, ...`은 SQLite에서는 되지만, PostgreSQL은 출력 별칭을 식 안에서 쓸 수 없습니다.
   - 두 DB가 모두 지원하는 `DESC NULLS LAST`로 바꿨습니다. 같은 집계 테스트를 두 DB에서 돌려 확인합니다.
5. **PostgreSQL 합계가 실수로 나옴**
   - `SUM(bigint)`의 결과 타입이 `numeric`이라 불량 수량 18이 `Decimal('18')` → 18.0으로 보였습니다.
   - 소수 자릿수가 없는 `numeric`은 정수로, 있는 값은 실수로 바꾸도록 했습니다(`test_postgres_values_normalized`).
6. **SQLite 중첩 트랜잭션이 중간에 커밋됨**
   - 적재(`ingest`)가 트랜잭션 안에서 `load_data`를 부르는데, `load_data`도 `with conn:`을 써서 안쪽 블록이 끝날 때 COMMIT됐습니다. 이후 단계가 실패해도 데이터가 남을 수 있었습니다.
   - 이미 트랜잭션 안이면 새로 열지 않고 합류하도록 바꿨습니다(`test_nested_transaction_rolls_back_as_one`, `test_db_error_rolls_back_whole_batch`).
7. **굵은 글씨 뒤 한글 조사**
   - 화면 문구 `**80.2%**를`이 굵게 표시되지 않고 별표가 그대로 보였습니다. 마크다운은 닫는 `**` 바로 뒤에 글자가 오면 굵게로 인식하지 않습니다.
   - 문구를 바꿔 `**80.2%**` 뒤에 공백이 오게 했습니다(브라우저 스크린샷으로 발견).

## 수동 점검 (브라우저)

| # | 시나리오 | 기대 결과 | 확인 |
|---|---|---|---|
| 1 | 샘플 데이터로 체험 | 생산량 288,347, 불량률 2.23% | ☑ (2026-10-22, 아래 M1) |
| 2 | `data/invalid_examples/` 폴더별 업로드 | 폴더 README의 기대 결과와 일치 | ☑ (2026-10-22) 9개 폴더 모두 일치. 03 "헤더만 있고…", 04 "5번 줄의 열 개수가 6개", 06 2/5·2/4·제외 5·상세 5건, 07 3/4·3/6·제외 4·상세 4건, 08 한글 공정 '프레스'·불량 유형 '긁힘'(비중 100%) 생산 500·불량률 1.00%, 09 불량률 '-'·불량 유형별 탭 '발생한 불량이 없습니다' (01·02·05는 사용자 보고) |
| 3 | production만 업로드 | "불량 파일이 없어…" 경고, 불량률 0.00% | ☑ (2026-10-22) 534/534, 불량 0/0, 제외 0, 경고 1건 "불량 파일이 없어…", 생산량 288,347·불량 0·불량률 0.00% |
| 4 | 공정 선택을 모두 해제 | "해당하는 데이터가 없습니다" 안내 | ☑ (2026-10-22) "공정 0개" 표시, 안내 문구 표시, KPI·탭 없음, 오류 없음 |
| 5 | 탭별 CSV 다운로드 후 엑셀로 열기 | 한글이 깨지지 않고 숫자 서식 없음 | ☑ (2026-10-22) 공정별 CSV: 헤더 '공정, LOT 수, 생산량, 불량 수량, 불량률(%)' 정상, 생산량 72390처럼 쉼표 없음 |
| 6 | 다른 파일을 다시 불러오기 | 필터가 전체 선택으로 초기화 | ☑ (2026-10-22) PAINT만 선택(3.68%) 후 샘플 다시 불러오기 → 공정 ASSY·PAINT·PRESS·WELD 전체 선택, 2.23% |

## 배포 설정 변경: 오류 상세 노출 제한 (2026-10-22)

변경: `.streamlit/config.toml`에 `[client] showErrorDetails = "none"` 추가. `app.py`와 `factorylens/`는 변경하지 않음.

### 자동 테스트 결과 (실제 실행값)

명령: `python -m pytest -q --cov=factorylens --cov=app` (프로젝트 루트에서 실행)

| 시점 | 결과 | 테스트 수 | 전체 커버리지 | app.py 미실행 줄 |
|---|---|---|---|---|
| 변경 전 | 통과 | 126 | 98% | 91-93, 112-113, 221-222 |
| 설정 변경 직후 (테스트 추가 전) | 통과 | 126 | 98% | 91-93, 112-113, 221-222 |
| 테스트 2개 추가 후 | 통과 | 128 | 98% | 91-93, 112-113, 221-222 |

추가한 테스트(`tests/test_app.py`)가 확인하는 범위와 한계:
- `test_config_hides_error_details_and_keeps_upload_limit`: 프로젝트 설정 파일을 읽었을 때 `showErrorDetails == "none"`, `maxUploadSize == 10`인지 확인합니다.
- `test_uncaught_exception_details_are_redacted`: AppTest에서 예외가 발생했을 때 Streamlit이 만드는 예외 데이터에 원래 메시지, 트레이스, 예외 종류가 없는지 확인합니다.
- **AppTest는 브라우저 화면, 실행 옵션·환경변수, 배포 플랫폼 설정을 거치지 않습니다.** 이 부분은 아래 수동 점검으로 확인합니다.

### 수동 점검 (브라우저)

오류 재현 스크립트는 저장소 밖에 둡니다. 설정 파일은 실행 위치(cwd) 기준으로 읽으므로 **반드시 프로젝트 루트에서 실행**합니다. 다른 폴더에서 실행하면 경고 없이 기본값(`full`, 200MB)이 적용됩니다.

```python
# 저장소 밖 임시 폴더의 boom.py (커밋하지 않음)
import streamlit as st
st.write("적용된 showErrorDetails:", st.get_option("client.showErrorDetails"))
st.write("적용된 maxUploadSize:", st.get_option("server.maxUploadSize"))
raise ValueError("C:/secret/path leaked")
```

| # | 실행 (프로젝트 루트) | 기대 결과 | 확인 |
|---|---|---|---|
| M1 | `streamlit run app.py` → 샘플 체험, 필터, 탭, 다운로드 | 생산량 288,347, 불량률 2.23%, 기존 기능 정상 | ☑ 적재 534/534·1,353/1,353·제외 0, 생산량 288,347, 불량 6,441, 불량률 2.23%, 공정별 차트 표시, 공정별 CSV 다운로드됨, 필터(생산일 범위·공정·제품) 선택·해제 동작 제품별·불량 유형별·일자별 추이 탭 차트와 표 표시. 공정별·제품별 표의 합계가 KPI와 일치(생산 288,347 / 불량 6,441 / LOT 534) |
| M1-a | `01_empty_file`, `02_missing_column`, `05_bad_values` 업로드 | "빈 파일입니다.", "필수 컬럼이 없습니다: produced_qty", 부분 적재 경고 | ☑ 사용자 보고: 세 폴더 모두 기대 결과와 같음(스크린샷 없음). 참고: 공정별 결과 CSV(한글 헤더)를 생산 데이터로 올리자 "필수 컬럼이 없습니다: lot_id, …"가 한글로 정상 표시됨 |
| M1-b | 약 11MB CSV 업로드 | 업로더가 거부함 (실제 문구 기록) | ☑ 11.0MB 파일에 오류 아이콘이 표시되고 "검증 후 불러오기" 버튼이 비활성화됨 / 문구: "File must be 10.0MB or smaller." |
| M2 | `streamlit run boom.py` | 화면에 `none`/`10`, 일반 오류 문구만 표시. 터미널에는 트레이스 | ☑ 브라우저: `none`/`10`, "This app has encountered an error…"만 표시, `ValueError`·경로·트레이스 없음 / 터미널: `ValueError: C:/secret/path leaked` 전체 트레이스 출력 확인 |
| M3 | `streamlit run boom.py --client.showErrorDetails=full` | 화면에 `full`, `ValueError`·메시지·트레이스 표시 | ☑ `full`/`10`, `ValueError: C:/secret/path leaked`와 Traceback 표시 |
| M4 | `$env:STREAMLIT_CLIENT_SHOW_ERROR_DETAILS="full"` 설정 후 실행 | M3와 같음 | ☑ `full`/`10`, M3와 같은 상세 표시 |
| M4-b | 환경변수 삭제 후 다시 실행 | M2와 같음 | ☑ `none`/`10`, 일반 문구만 표시 |
| M5 | 배포 환경 (Streamlit Community Cloud, https://factorylens-1.streamlit.app) | 플랫폼이 설정을 덮어쓰지 않음: 일반 문구만 표시, 11MB 업로드 거부 | ☑ 2026-10-23: 업로더에 "10MB per file" 표시(플랫폼 기본값 200MB가 아니라 프로젝트 `config.toml`이 적용됨). 샘플 체험 결과 로컬과 동일(534/534, 1,353/1,353, 제외 0, 불량 6,441, 불량률 2.23%, 탭 5개, 파레토 상위 7개 80.2%, ASSY 관리도 중심선 1.05%·이탈 0일). 11.0MB 파일(`production_11mb.csv`) 업로드 시 오류 아이콘과 "File must be 10.0MB or smaller." 표시, "검증 후 불러오기" 버튼 비활성화 (사용자 확인, 스크린샷 있음) |

## 공개 배포 전 필수 후속 작업

1. ~~**`app.py:92` SQLite 예외 원문 노출**~~ → **해결 (2026-10-23).** DB 오류는 `IngestError`로 바꾸고, 화면에는 일반 문구("DB 적재 중 오류가 발생해 이번 업로드 전체를 취소했습니다…")만, 원문은 서버 로그(`logger.error`)와 명령줄 출력에만 남깁니다. `test_db_error_rolls_back_whole_batch`가 화면용 문구에 컬럼 이름이 없는지 확인합니다. 누적 DB 접속 실패 문구에도 접속 정보를 넣지 않습니다.
2. ~~**배포 환경 확인(M5)**~~ → **완료 (2026-10-23).** 설정 파일 적용, 샘플 결과, 11MB 업로드 거부를 모두 확인했습니다(위 M5 행). 공개 배포 전 필수 후속 작업이 모두 끝났습니다.

## 화면 개선 후보 → 모두 반영 (2026-10-23)

1. ~~오류·경고 상세 표의 `None`~~ → 줄 번호·컬럼이 없으면 빈칸으로 표시 (`test_issue_table_shows_blank_instead_of_none`)
2. ~~불량률을 계산할 수 없거나 0일 때의 차트~~ → 공정별·제품별·일자별 탭에서 차트 대신 안내 문구 (`test_rate_charts_replaced_by_message`)
3. ~~분석 결과 CSV를 입력으로 올린 경우~~ → "FactoryLens에서 내려받은 분석 결과 파일을 올린 것 같습니다" 안내 추가 (`test_result_csv_uploaded_as_input_gets_hint`)
