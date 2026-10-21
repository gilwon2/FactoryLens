import pytest

from factorylens.validation import DEFECT_COLUMNS, PRODUCTION_COLUMNS, FileError, read_csv

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
