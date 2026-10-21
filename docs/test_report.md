# 테스트 결과

## 실행 방법

```bash
pip install -r requirements-dev.txt
python -m pytest -q                                   # 전체 테스트
python -m pytest -q --cov=factorylens --cov=app       # 커버리지 포함
```

## 결과 요약 (2026-10-22 실행)

- 환경: Windows 10, Python 3.10.11, pandas 2.3.3, streamlit 1.65.0, pytest 9.1.1, pytest-cov 7.1.0
- 결과: **126개 통과, 0개 실패**

| 파일 | 개수 | 대상 |
|---|---|---|
| `tests/test_validation.py` | 63 | CSV 읽기, 행·중복·교차 검증 |
| `tests/test_metrics.py` | 26 | 집계 SQL, 불량률, 필터 |
| `tests/test_export.py` | 10 | 다운로드 CSV (BOM, 수식 주입 방지) |
| `tests/test_invalid_examples.py` | 10 | `data/invalid_examples/` 시나리오별 기대 결과 |
| `tests/test_app.py` | 9 | Streamlit 화면 흐름 (AppTest, 브라우저 없이 실행) |
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
| 1 | 샘플 데이터로 체험 | 생산량 288,347, 불량률 2.23% | ☐ |
| 2 | `data/invalid_examples/` 폴더별 업로드 | 폴더 README의 기대 결과와 일치 | ☐ |
| 3 | production만 업로드 | "불량 파일이 없어…" 경고, 불량률 0.00% | ☐ |
| 4 | 공정 선택을 모두 해제 | "해당하는 데이터가 없습니다" 안내 | ☐ |
| 5 | 탭별 CSV 다운로드 후 엑셀로 열기 | 한글이 깨지지 않고 숫자 서식 없음 | ☐ |
| 6 | 다른 파일을 다시 불러오기 | 필터가 전체 선택으로 초기화 | ☐ |
