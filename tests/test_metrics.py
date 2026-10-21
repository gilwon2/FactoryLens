"""집계 테스트. 데이터셋은 conftest.py 상단 표 참고.

손계산 기대값
- 전체   : 생산 600 (100+300+200+0+0), 불량 18 (3+2+3+10), 불량률 3.0%
- PRESS  : 생산 400, 불량 8  -> 2.0%   (LOT별 5%, 1%의 단순 평균 3.0%가 아님)
- WELD   : 생산 200, 불량 10 -> 5.0%
- PAINT  : 생산 0,   불량 0  -> None  (0으로 나누기 방지)
- 제품 A : 생산 300, 불량 15 -> 5.0% / 제품 B : 생산 300, 불량 3 -> 1.0%
- 유형   : CRACK 10, SCRATCH 6, DENT 2 (합 18)
"""
import pandas as pd
import pytest

from factorylens.db import create_db, load_data
from factorylens.metrics import (
    build_where,
    by_defect_type,
    by_group,
    date_range,
    distinct_values,
    safe_rate,
    summary_kpi,
)


def rows(df):
    return df.to_dict("records")


# ---------- safe_rate ----------

@pytest.mark.parametrize("numerator, denominator, expected", [
    (5, 100, 5.0),
    (0, 100, 0.0),
    (1, 3, 33.33),
    (0, 0, None),
    (3, 0, None),
    (0, None, None),
])
def test_safe_rate(numerator, denominator, expected):
    assert safe_rate(numerator, denominator) == expected


# ---------- KPI ----------

def test_summary_kpi_all(conn):
    assert summary_kpi(conn) == {"lot_count": 5, "produced_qty": 600, "defect_qty": 18, "defect_rate_pct": 3.0}


def test_summary_kpi_with_no_matching_rows(conn):
    # 0건이어도 오류 없이 0과 None을 돌려준다
    assert summary_kpi(conn, start_date="2030-01-01") == {
        "lot_count": 0, "produced_qty": 0, "defect_qty": 0, "defect_rate_pct": None,
    }


def test_summary_kpi_empty_db():
    assert summary_kpi(create_db())["defect_rate_pct"] is None


# ---------- 그룹별 ----------

def test_by_process_has_no_join_fanout(conn):
    # L1은 불량 유형이 2개(SCRATCH, DENT). 그냥 JOIN하면 PRESS 생산량이 500(100x2+300)이 된다
    press = by_group(conn, "process").set_index("process").loc["PRESS"]
    assert press["produced_qty"] == 400
    assert press["defect_qty"] == 8


def test_by_process_values_and_order(conn):
    result = by_group(conn, "process")
    assert rows(result.head(2)) == [
        {"process": "WELD", "lot_count": 2, "produced_qty": 200, "defect_qty": 10, "defect_rate_pct": 5.0},
        {"process": "PRESS", "lot_count": 2, "produced_qty": 400, "defect_qty": 8, "defect_rate_pct": 2.0},
    ]
    # 불량률이 NULL인 그룹은 맨 뒤. SQL의 NULL은 pandas로 읽으면 NaN(결측)이 된다
    paint = result.iloc[2]
    assert (paint["process"], paint["produced_qty"], paint["defect_qty"]) == ("PAINT", 0, 0)
    assert pd.isna(paint["defect_rate_pct"])


def test_group_rate_is_weighted_not_simple_average(conn):
    # PRESS의 LOT별 불량률: L1 5/100 = 5%, L2 3/300 = 1% -> 단순 평균 3%, 가중 평균 8/400 = 2%
    press = by_group(conn, "process").set_index("process").loc["PRESS"]
    assert press["defect_rate_pct"] == 2.0


def test_lot_without_defects_is_included(conn):
    # L4(WELD, 생산 0, 불량 행 없음)도 LEFT JOIN으로 LOT 수에 포함된다
    weld = by_group(conn, "process").set_index("process").loc["WELD"]
    assert weld["lot_count"] == 2


def test_by_product(conn):
    result = by_group(conn, "product")
    assert result[["product", "produced_qty", "defect_qty", "defect_rate_pct"]].values.tolist() == [
        ["A", 300, 15, 5.0],
        ["B", 300, 3, 1.0],
    ]


def test_by_work_date_is_chronological(conn):
    result = by_group(conn, "work_date")
    assert result["work_date"].tolist() == ["2026-09-01", "2026-09-02", "2026-09-03"]
    assert result["defect_rate_pct"].tolist()[:2] == [2.0, 5.0]
    assert result["defect_rate_pct"].isna().tolist()[2]


def test_invalid_group_column_rejected(conn):
    with pytest.raises(ValueError):
        by_group(conn, "process; DROP TABLE production")


# ---------- 불량 유형별 ----------

def test_by_defect_type(conn):
    assert rows(by_defect_type(conn)) == [
        {"defect_type": "CRACK", "defect_qty": 10, "share_pct": 55.56},
        {"defect_type": "SCRATCH", "defect_qty": 6, "share_pct": 33.33},
        {"defect_type": "DENT", "defect_qty": 2, "share_pct": 11.11},
    ]


def test_by_defect_type_with_filter(conn):
    assert rows(by_defect_type(conn, processes=["PRESS"])) == [
        {"defect_type": "SCRATCH", "defect_qty": 6, "share_pct": 75.0},
        {"defect_type": "DENT", "defect_qty": 2, "share_pct": 25.0},
    ]


def test_zero_quantity_defect_type_hidden(production_df, defects_df):
    defects_df.loc[len(defects_df)] = ["L2", "BURR", 0]
    connection = create_db()
    load_data(connection, production_df, defects_df)
    assert "BURR" not in by_defect_type(connection)["defect_type"].tolist()


# ---------- 필터 ----------

def test_date_filter_includes_both_ends(conn):
    kpi = summary_kpi(conn, start_date="2026-09-01", end_date="2026-09-02")
    assert (kpi["lot_count"], kpi["produced_qty"], kpi["defect_qty"]) == (4, 600, 18)


def test_single_day_filter(conn):
    assert summary_kpi(conn, start_date="2026-09-01", end_date="2026-09-01")["defect_rate_pct"] == 2.0


def test_process_and_product_filters_combined(conn):
    kpi = summary_kpi(conn, processes=["PRESS", "WELD"], products=["A"])
    assert (kpi["lot_count"], kpi["produced_qty"], kpi["defect_qty"]) == (2, 300, 15)


def test_empty_selection_means_no_rows(conn):
    assert summary_kpi(conn, processes=[])["lot_count"] == 0
    assert by_group(conn, "process", processes=[]).empty
    assert by_defect_type(conn, processes=[]).empty


def test_filter_values_are_parameters_not_sql(conn):
    # 값에 SQL을 넣어도 문자열로 비교될 뿐 실행되지 않는다
    assert summary_kpi(conn, processes=["PRESS' OR '1'='1"])["lot_count"] == 0
    assert summary_kpi(conn)["lot_count"] == 5


def test_build_where_uses_placeholders():
    where, params = build_where(start_date="2026-09-01", processes=["PRESS", "WELD"])
    assert where == "1 = 1 AND p.work_date >= ? AND p.process IN (?, ?)"
    assert params == ["2026-09-01", "PRESS", "WELD"]


# ---------- 필터 선택지 ----------

def test_filter_options(conn):
    assert distinct_values(conn, "process") == ["PAINT", "PRESS", "WELD"]
    assert distinct_values(conn, "product") == ["A", "B"]
    assert date_range(conn) == ("2026-09-01", "2026-09-03")
    with pytest.raises(ValueError):
        distinct_values(conn, "lot_id")
