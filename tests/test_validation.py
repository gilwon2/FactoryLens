from pathlib import Path

import pytest

from factorylens.validation import (
    DEFECT_COLUMNS,
    PRODUCTION_COLUMNS,
    FileError,
    check_date,
    check_quantity,
    cross_validate,
    read_csv,
    validate_defects,
    validate_production,
    validate_upload,
)

HEADER = "lot_id,work_date,process,product,produced_qty\n"
ROW = "L1,2026-09-01,PRESS,P-A100,500\n"


def read_production(text: str, encoding: str = "utf-8"):
    return read_csv(text.encode(encoding), PRODUCTION_COLUMNS, "production")


# ---------- F1: 빈 파일 / 인코딩 / CSV 구조 ----------

def test_empty_bytes_rejected():
    with pytest.raises(FileError, match="빈 파일"):
        read_csv(b"", PRODUCTION_COLUMNS, "production")


def test_whitespace_only_file_rejected():
    with pytest.raises(FileError, match="빈 파일"):
        read_production("\n  \n\n")


def test_bom_only_file_rejected():
    # 엑셀에서 빈 시트를 "CSV UTF-8"로 저장하면 BOM 3바이트만 남는다 (0바이트가 아님)
    with pytest.raises(FileError, match="빈 파일"):
        read_csv(b"\xef\xbb\xbf", PRODUCTION_COLUMNS, "production")


def test_unclosed_quote_rejected():
    with pytest.raises(FileError, match="CSV 형식"):
        read_production(HEADER + 'L1,2026-09-01,"PRESS,P-A100,500\n')


def test_undecodable_bytes_rejected():
    # 0x80 단독 바이트는 UTF-8과 CP949 모두에서 잘못된 문자다
    with pytest.raises(FileError, match="인코딩"):
        read_csv(b"lot_id\n\x80\n", PRODUCTION_COLUMNS, "production")


def test_first_row_with_too_many_fields_rejected():
    # pandas 기본 설정에서는 이 경우 오류 없이 남는 열을 index로 써 버린다 (회귀 테스트)
    with pytest.raises(FileError, match="2번 줄의 열 개수가 7개"):
        read_production(HEADER + "L1,2026-09-01,PRESS,P-A100,500,EXTRA,MORE\n")


def test_later_row_with_too_many_fields_rejected():
    with pytest.raises(FileError, match="3번 줄"):
        read_production(HEADER + ROW + "L2,2026-09-01,PRESS,P-A,100,500\n")


def test_cp949_file_is_read():
    df, _ = read_production(HEADER + "L1,2026-09-01,프레스,P-A100,500\n", encoding="cp949")
    assert df.loc[2, "process"] == "프레스"


def test_utf8_bom_header_is_read():
    # 엑셀에서 "CSV UTF-8"로 저장하면 파일 앞에 BOM이 붙는다
    df, _ = read_csv("﻿".encode() + (HEADER + ROW).encode(), PRODUCTION_COLUMNS, "production")
    assert list(df.columns) == PRODUCTION_COLUMNS


# ---------- F2: 필수 컬럼 ----------

def test_missing_required_column_rejected():
    with pytest.raises(FileError, match="produced_qty"):
        read_production("lot_id,work_date,process,product\nL1,2026-09-01,PRESS,P-A100\n")


def test_result_csv_uploaded_as_input_gets_hint():
    # 공정별 탭에서 내려받은 결과 CSV를 생산 데이터로 잘못 올린 경우
    with pytest.raises(FileError, match="분석 결과 파일을 올린 것 같습니다"):
        read_production("공정,LOT 수,생산량,불량 수량,불량률(%)\nPAINT,132,72390,2661,3.68\n")
    with pytest.raises(FileError) as error:      # 결과 파일이 아니면 안내를 붙이지 않는다
        read_production("lot_id,work_date,process,product\nL1,2026-09-01,PRESS,P-A100\n")
    assert "분석 결과" not in str(error.value)


def test_column_names_are_normalized():
    df, _ = read_production(" LOT_ID ,Work_Date,PROCESS,product,Produced_Qty\n" + ROW)
    assert list(df.columns) == PRODUCTION_COLUMNS


def test_duplicate_column_after_normalization_rejected():
    with pytest.raises(FileError, match="중복"):
        read_production("lot_id,LOT_ID,work_date,process,product,produced_qty\nL1,L1,2026-09-01,PRESS,P-A100,500\n")


# ---------- F3: 데이터 행 없음 ----------

def test_header_only_rejected():
    with pytest.raises(FileError, match="데이터 행이 없습니다"):
        read_production(HEADER)


def test_header_with_blank_lines_only_rejected():
    with pytest.raises(FileError, match="데이터 행이 없습니다"):
        read_production(HEADER + "\n\n,,,,\n")


# ---------- F4: 추가 컬럼 ----------

def test_extra_column_ignored_with_info():
    df, issues = read_production("lot_id,work_date,process,product,produced_qty,memo\n" + "L1,2026-09-01,PRESS,P-A100,500,hello\n")
    assert list(df.columns) == PRODUCTION_COLUMNS
    assert len(issues) == 1
    assert issues[0]["level"] == "info"
    assert "memo" in issues[0]["reason"]


# ---------- 읽기 결과 형태 ----------

def test_index_is_original_line_number_even_with_blank_lines():
    df, _ = read_production(HEADER + ROW + "\n" + "L2,2026-09-01,WELD,P-A100,300\n")
    # 1번 줄 헤더, 2번 줄 L1, 3번 줄 빈 줄, 4번 줄 L2
    assert list(df.index) == [2, 4]
    assert df.loc[4, "lot_id"] == "L2"


def test_values_are_stripped_strings_and_missing_becomes_empty():
    df, _ = read_production(HEADER + " L1 , 2026-09-01 ,PRESS,P-A100,007\nL2,2026-09-01,PRESS,P-A100\n")
    assert df.loc[2, "lot_id"] == "L1"
    assert df.loc[2, "produced_qty"] == "007"  # 숫자로 자동 변환되지 않는다
    assert df.loc[3, "produced_qty"] == ""     # 모자란 열은 빈 문자열


def test_defect_file_columns():
    df, _ = read_csv(b"lot_id,defect_type,defect_qty\nL1,SCRATCH,3\n", DEFECT_COLUMNS, "defects")
    assert list(df.columns) == DEFECT_COLUMNS
    assert len(df) == 1


# =====================================================================
# S3: 행 단위 검증(R1·R2)과 중복 처리(D1·D2)
# =====================================================================

def check_production(rows: str):
    df, _ = read_production(HEADER + rows)
    return validate_production(df)


def errors_of(issues):
    return [i for i in issues if i["level"] == "error"]


# ---------- R2: 값 검사 함수 ----------

@pytest.mark.parametrize("value", ["0", "500", "007", str(1_000_000_000)])
def test_check_quantity_accepts_non_negative_integers(value):
    assert check_quantity(value) is None


@pytest.mark.parametrize("value, expected", [
    ("-1", "음수"),
    ("-2.5", "음수"),
    ("12.5", "소수점"),
    ("12.0", "소수점"),
    ("abc", "0 이상의 정수"),
    ("+5", "0 이상의 정수"),
    ("1,000", "0 이상의 정수"),
    (str(1_000_000_001), "너무 큽니다"),
])
def test_check_quantity_rejects(value, expected):
    assert expected in check_quantity(value)


@pytest.mark.parametrize("value, expected", [
    ("2026-09-01", None),
    ("2024-02-29", None),               # 윤년
    ("2026-13-01", "존재하지 않는"),
    ("2026-02-30", "존재하지 않는"),
    ("2026/09/01", "형식"),
    ("2026-9-1", "형식"),
    ("20260901", "형식"),
])
def test_check_date(value, expected):
    result = check_date(value)
    if expected is None:
        assert result is None
    else:
        assert expected in result


# ---------- R1·R2: 행 제외 ----------

def test_valid_rows_pass_and_quantity_becomes_int():
    clean, issues = check_production(ROW + "L2,2026-09-02,WELD,P-B200,0\n")
    assert list(clean["lot_id"]) == ["L1", "L2"]
    assert clean["produced_qty"].tolist() == [500, 0]
    assert clean["produced_qty"].dtype.kind == "i"
    assert issues == []


def test_missing_value_row_excluded_with_line_and_column():
    clean, issues = check_production(ROW + "L2,2026-09-01,,P-A100,300\n")
    assert list(clean["lot_id"]) == ["L1"]          # 정상 행은 그대로 적재 (부분 적재)
    assert errors_of(issues) == [
        {"file": "production", "row": 3, "column": "process", "level": "error", "reason": "값이 비어 있습니다"}
    ]


def test_whitespace_only_value_is_missing():
    clean, issues = check_production("L1,2026-09-01,   ,P-A100,500\n")
    assert clean.empty
    assert issues[0]["reason"] == "값이 비어 있습니다"


def test_bad_values_excluded_with_input_shown():
    clean, issues = check_production(
        "L1,2026-13-01,PRESS,P-A100,500\n"
        "L2,2026-09-01,PRESS,P-A100,12.5\n"
        "L3,2026-09-01,PRESS,P-A100,-3\n"
    )
    assert clean.empty
    assert [(i["row"], i["column"]) for i in issues] == [(2, "work_date"), (3, "produced_qty"), (4, "produced_qty")]
    assert "'12.5'" in issues[1]["reason"]


def test_row_with_several_problems_reports_each_but_excluded_once():
    clean, issues = check_production(ROW + ",2026/09/01,PRESS,,abc\n")
    assert list(clean.index) == [2]
    assert {i["column"] for i in issues} == {"lot_id", "work_date", "product", "produced_qty"}
    assert {i["row"] for i in issues} == {3}


# ---------- D1·D2: 중복 ----------

def test_exact_duplicate_keeps_first_with_warning():
    clean, issues = check_production(ROW + "L2,2026-09-01,WELD,P-A100,300\n" + ROW)
    assert list(clean["lot_id"]) == ["L1", "L2"]
    assert list(clean.index) == [2, 3]              # 처음 나온 줄이 남는다
    assert len(issues) == 1
    assert issues[0]["level"] == "warning"
    assert issues[0]["row"] == 4
    assert "2번 줄" in issues[0]["reason"]


def test_exact_duplicate_ignores_surrounding_spaces():
    clean, issues = check_production(ROW + " L1 ,2026-09-01, PRESS ,P-A100, 500\n")
    assert len(clean) == 1
    assert issues[0]["level"] == "warning"


def test_key_conflict_excludes_all_rows_of_that_key():
    clean, issues = check_production(
        ROW
        + "L2,2026-09-01,WELD,P-A100,300\n"
        + "L1,2026-09-01,PRESS,P-A100,450\n"
    )
    assert list(clean["lot_id"]) == ["L2"]
    assert [(i["row"], i["level"]) for i in issues] == [(2, "error"), (4, "error")]
    assert "2, 4" in issues[0]["reason"]


def test_conflict_is_checked_before_type_errors():
    # 한 줄이 타입 오류여도 같은 키의 다른 줄이 살아남으면 안 된다
    clean, issues = check_production(ROW + "L1,2026-09-01,PRESS,P-A100,abc\n")
    assert clean.empty
    assert all("서로 다른 값" in i["reason"] for i in issues)


def test_exact_duplicate_plus_conflict():
    # 2·4번 줄은 완전중복(1행 반영) -> 남은 2번 줄과 3번 줄이 충돌 -> 둘 다 제외
    clean, issues = check_production(ROW + "L1,2026-09-01,PRESS,P-A100,450\n" + ROW)
    assert clean.empty
    assert [(i["row"], i["level"]) for i in issues] == [(4, "warning"), (2, "error"), (3, "error")]


def test_rows_with_empty_key_are_not_treated_as_duplicates():
    clean, issues = check_production(",2026-09-01,PRESS,P-A100,500\n" * 2)
    assert clean.empty
    assert [i["level"] for i in issues] == ["error", "error"]
    assert all(i["reason"] == "값이 비어 있습니다" for i in issues)


def test_defects_same_lot_different_types_are_not_duplicates():
    df, _ = read_csv(b"lot_id,defect_type,defect_qty\nL1,SCRATCH,3\nL1,DENT,2\n", DEFECT_COLUMNS, "defects")
    clean, issues = validate_defects(df)
    assert len(clean) == 2
    assert issues == []


def test_defects_key_conflict_on_lot_and_type():
    df, _ = read_csv(b"lot_id,defect_type,defect_qty\nL1,SCRATCH,3\nL1,SCRATCH,5\nL1,DENT,2\n",
                     DEFECT_COLUMNS, "defects")
    clean, issues = validate_defects(df)
    assert clean[["defect_type", "defect_qty"]].values.tolist() == [["DENT", 2]]
    assert all(i["column"] == "lot_id+defect_type" for i in issues)
    assert "L1/SCRATCH" in issues[0]["reason"]


# =====================================================================
# S4: 교차 검증(X1·X2)과 전체 흐름(validate_upload, Z1)
# =====================================================================
DEFECT_HEADER = "lot_id,defect_type,defect_qty\n"
SAMPLE_DIR = Path(__file__).resolve().parent.parent / "data" / "sample"


def upload(production_rows: str, defect_rows: str | None):
    defects_data = None if defect_rows is None else (DEFECT_HEADER + defect_rows).encode()
    return validate_upload((HEADER + production_rows).encode(), defects_data)


# ---------- X1: 고아 불량 행 ----------

def test_orphan_defect_rows_excluded():
    result = upload(ROW, "L1,SCRATCH,3\nL9,DENT,2\n")
    assert result.ok
    assert result.defects["lot_id"].tolist() == ["L1"]
    [issue] = result.issues
    assert (issue["file"], issue["row"], issue["column"]) == ("defects", 3, "lot_id")
    assert "L9" in issue["reason"]


def test_defects_of_excluded_production_lot_become_orphans():
    # L2는 생산량 오류로 제외되었으므로 L2의 불량 행도 함께 빠져야 한다
    result = upload(ROW + "L2,2026-09-01,PRESS,P-A100,abc\n", "L1,SCRATCH,3\nL2,DENT,2\n")
    assert result.production["lot_id"].tolist() == ["L1"]
    assert result.defects["lot_id"].tolist() == ["L1"]
    assert [(i["file"], i["row"]) for i in result.issues] == [("production", 3), ("defects", 3)]


# ---------- X2: 불량 수량 > 생산량 ----------

def test_lot_with_more_defects_than_produced_is_fully_excluded():
    result = upload(
        "L1,2026-09-01,PRESS,P-A100,5\nL2,2026-09-01,PRESS,P-A100,100\n",
        "L1,SCRATCH,3\nL1,DENT,3\nL2,DENT,1\n",
    )
    assert result.production["lot_id"].tolist() == ["L2"]
    assert result.defects["lot_id"].tolist() == ["L2"]
    errors = [(i["file"], i["row"]) for i in result.issues]
    assert errors == [("production", 2), ("defects", 2), ("defects", 3)]
    assert "합계(6)" in result.issues[0]["reason"] and "생산량(5)" in result.issues[0]["reason"]


def test_defects_equal_to_produced_is_allowed():
    result = upload("L1,2026-09-01,PRESS,P-A100,5\n", "L1,SCRATCH,3\nL1,DENT,2\n")
    assert len(result.production) == 1 and len(result.defects) == 2
    assert result.issues == []


def test_zero_production_lot():
    # 생산 0 + 불량 없음은 정상 (설비 정지), 생산 0 + 불량 1은 모순
    result = upload("L1,2026-09-01,PRESS,P-A100,0\nL2,2026-09-01,PRESS,P-A100,0\n", "L2,DENT,1\n")
    assert result.production["lot_id"].tolist() == ["L1"]
    assert result.defects.empty


def test_cross_validate_with_no_defects():
    production, _ = validate_production(read_production(HEADER + ROW)[0])
    empty_defects, _ = validate_defects(read_csv(DEFECT_HEADER.encode(), DEFECT_COLUMNS, "defects", allow_empty=True)[0])
    p, d, issues = cross_validate(production, empty_defects)
    assert len(p) == 1 and d.empty and issues == []


# ---------- 전체 흐름 ----------

def test_defects_header_only_means_no_defects():
    result = upload(ROW, "")
    assert result.ok
    assert result.defects.empty
    assert result.issues == []


def test_missing_defects_file_gives_warning():
    result = upload(ROW, None)
    assert result.ok
    assert result.defects.empty
    assert [i["level"] for i in result.issues] == ["warning"]


def test_production_header_only_is_still_rejected():
    result = upload("", "L1,SCRATCH,3\n")
    assert not result.ok
    assert result.fatal.startswith("[production]")


def test_both_file_errors_reported_together():
    result = validate_upload(b"", b"lot_id,defect_qty\nL1,3\n")
    assert not result.ok
    assert "[production] 빈 파일" in result.fatal
    assert "[defects] 필수 컬럼이 없습니다: defect_type" in result.fatal
    assert result.production is None and result.defects is None


def test_all_production_rows_invalid_is_fatal_but_keeps_issues():
    result = upload("L1,2026-09-01,PRESS,P-A100,-1\nL2,bad-date,PRESS,P-A100,5\n", "L1,SCRATCH,1\n")
    assert not result.ok
    assert "검증을 통과한 생산 데이터가 없어" in result.fatal
    assert len(result.issues) == 3        # 생산 오류 2건 + 고아 불량 1건 -> 사용자가 원인을 볼 수 있어야 한다
    assert result.production is None


def test_rows_read_counts_exclude_blank_lines():
    result = upload(ROW + "\n" + "L2,2026-09-01,PRESS,P-A100,abc\n", "L1,SCRATCH,1\n\n")
    assert result.production_rows_read == 2
    assert result.defects_rows_read == 1
    assert len(result.production) == 1


def test_sample_data_passes_cleanly():
    result = validate_upload((SAMPLE_DIR / "production_sample.csv").read_bytes(),
                             (SAMPLE_DIR / "defects_sample.csv").read_bytes())
    assert result.ok
    assert result.issues == []
    assert len(result.production) == result.production_rows_read
    assert len(result.defects) == result.defects_rows_read
