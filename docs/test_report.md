# 테스트 결과

## 실행 방법

```bash
pip install -r requirements-dev.txt
python -m pytest -q                                   # 전체 테스트
python -m pytest -q --cov=factorylens --cov=app       # 커버리지 포함
```

## 결과 요약 (2026-10-22 실행)

- 환경: Windows 10, Python 3.10.11, pandas 2.3.3, streamlit 1.65.0, pytest 9.1.1, pytest-cov 7.1.0
- 결과: **128개 통과, 0개 실패** (배포 설정 테스트 2개 추가 후. 변경 전후 결과는 아래 "배포 설정 변경" 참고)

| 파일 | 개수 | 대상 |
|---|---|---|
| `tests/test_validation.py` | 63 | CSV 읽기, 행·중복·교차 검증 |
| `tests/test_metrics.py` | 26 | 집계 SQL, 불량률, 필터 |
| `tests/test_export.py` | 10 | 다운로드 CSV (BOM, 수식 주입 방지) |
| `tests/test_invalid_examples.py` | 10 | `data/invalid_examples/` 시나리오별 기대 결과 |
| `tests/test_app.py` | 11 | Streamlit 화면 흐름 (AppTest, 브라우저 없이 실행), 오류 상세 노출 제한 설정 |
| `tests/test_db.py` | 7 | 스키마, 적재, DB 제약 |
| `tests/test_smoke.py` | 1 | 패키지 import |

### 커버리지

| 모듈 | 구문 수 | 커버리지 |
|---|---|---|
| `factorylens/validation.py` | 181 | 100% |
| `factorylens/metrics.py` | 54 | 100% |
| `factorylens/db.py` | 16 | 100% |
| `factorylens/export.py` | 12 | 100% |
| `app.py` | 160 | 96% |
| **합계** | 423 | **98%** |

`app.py`에서 실행되지 않은 7줄은 아래 세 가지이며, 수동 점검으로 확인합니다.
- 파일 업로드 버튼 처리: AppTest가 파일 업로드를 흉내 낼 수 없음
- DB 적재 예외 처리: 검증을 통과한 데이터에서는 발생하지 않는 2차 방어선
- 필터 결과에 불량이 0건일 때의 안내 문구

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
| M5 | 배포 환경 (배포 후, 별도 승인) | 플랫폼이 설정을 덮어쓰지 않음: 일반 문구만 표시, 11MB 업로드 거부 | ☐ 미실행 |

## 공개 배포 전 필수 후속 작업

1. **`app.py:92` SQLite 예외 원문 노출:** DB 적재 실패 시 `st.error()`에 `sqlite3.Error` 원문(`{e}`)이 그대로 표시됩니다. `showErrorDetails`는 처리되지 않은 예외에만 적용되므로 이 메시지는 가려지지 않으며, 테이블·컬럼 이름이 노출될 수 있습니다. 화면에는 일반 문구만 보여 주고 원문은 서버 로그로 남기도록 바꿔야 합니다.
2. **배포 환경 확인(M5):** 배포 플랫폼이 실행 옵션 등으로 `showErrorDetails`를 덮어쓰지 않는지, 프로젝트 설정 파일이 읽히는지 확인합니다.

## 화면 개선 후보 (기능 오류 아님, 포트폴리오 정리 전 처리 예정)

수동 점검 중 발견했습니다. 계산 결과는 정상이고 표시 방식만 개선 대상입니다.

1. **오류·경고 상세 표의 `None`:** 줄 번호·컬럼이 없는 이슈(파일 단위 경고, 완전중복 경고)에서 빈칸 대신 `None`이 표시됩니다 (`app.py`의 `issues_table`).
2. **불량률을 계산할 수 없거나 0일 때의 차트:** 생산량이 모두 0이면(`09_zero_production`) 공정별 차트 세로축에 `null`이 표시되고, 불량이 0건이면 세로축에 `0.000000`만 보입니다. 표는 `-`와 0.00으로 정상 표시됩니다 (`app.py`의 `show_group`).
3. **분석 결과 CSV를 입력으로 올린 경우:** 탭에서 받은 결과 CSV(한글 헤더)를 생산 데이터로 올리면 "필수 컬럼이 없습니다: lot_id, …"로 거부됩니다. 동작은 맞지만, 결과 파일을 올린 것 같다는 안내가 있으면 더 이해하기 쉽습니다.
