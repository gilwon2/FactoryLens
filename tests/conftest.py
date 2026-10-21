"""여러 테스트 파일이 함께 쓰는 fixture.

손으로 결과를 계산할 수 있는 작은 데이터셋 (기대값은 test_metrics.py 참고)

production                                         defect
lot  work_date   process product produced_qty      lot  type     qty
L1   2026-09-01  PRESS   A       100               L1   SCRATCH  3
L2   2026-09-01  PRESS   B       300               L1   DENT     2   <- 불량 유형 2개 (JOIN 팬아웃 확인용)
L3   2026-09-02  WELD    A       200               L2   SCRATCH  3
L4   2026-09-02  WELD    B       0    (설비 정지)   L3   CRACK    10
L5   2026-09-03  PAINT   A       0    (설비 정지)
"""
import pandas as pd
import pytest

from factorylens.db import create_db, load_data


@pytest.fixture
def production_df():
    return pd.DataFrame(
        [
            ["L1", "2026-09-01", "PRESS", "A", 100],
            ["L2", "2026-09-01", "PRESS", "B", 300],
            ["L3", "2026-09-02", "WELD", "A", 200],
            ["L4", "2026-09-02", "WELD", "B", 0],
            ["L5", "2026-09-03", "PAINT", "A", 0],
        ],
        columns=["lot_id", "work_date", "process", "product", "produced_qty"],
    )


@pytest.fixture
def defects_df():
    return pd.DataFrame(
        [
            ["L1", "SCRATCH", 3],
            ["L1", "DENT", 2],
            ["L2", "SCRATCH", 3],
            ["L3", "CRACK", 10],
        ],
        columns=["lot_id", "defect_type", "defect_qty"],
    )


@pytest.fixture
def conn(production_df, defects_df):
    connection = create_db()
    load_data(connection, production_df, defects_df)
    yield connection
    connection.close()
