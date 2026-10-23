"""여러 테스트 파일이 함께 쓰는 fixture.

손으로 결과를 계산할 수 있는 작은 데이터셋 (기대값은 test_metrics.py 참고)

production                                         defect
lot  work_date   process product produced_qty      lot  type     qty
L1   2026-09-01  PRESS   A       100               L1   SCRATCH  3
L2   2026-09-01  PRESS   B       300               L1   DENT     2   <- 불량 유형 2개 (JOIN 팬아웃 확인용)
L3   2026-09-02  WELD    A       200               L2   SCRATCH  3
L4   2026-09-02  WELD    B       0    (설비 정지)   L3   CRACK    10
L5   2026-09-03  PAINT   A       0    (설비 정지)

DB 백엔드
- 기본은 인메모리 SQLite.
- 환경 변수 FACTORYLENS_TEST_DATABASE_URL에 PostgreSQL 주소를 주면, make_db를 쓰는 테스트(집계·적재)가
  PostgreSQL에서도 한 번 더 실행된다. 같은 SQL이 두 DB에서 같은 결과를 내는지 확인하기 위해서다.
  CI(GitHub Actions)는 PostgreSQL 서비스를 띄워 항상 두 DB로 실행한다.
  예) FACTORYLENS_TEST_DATABASE_URL=postgresql://postgres:postgres@localhost:5432/factorylens_test
  ※ 테스트마다 이 DB의 FactoryLens 테이블을 지우므로 테스트 전용 DB를 써야 한다.
"""
import os

import pandas as pd
import pytest

from factorylens.db import TABLES, connect, create_db, load_data

PG_TEST_URL = os.environ.get("FACTORYLENS_TEST_DATABASE_URL")
BACKENDS = [
    "sqlite",
    pytest.param("postgresql", marks=pytest.mark.skipif(
        not PG_TEST_URL, reason="FACTORYLENS_TEST_DATABASE_URL이 없어 PostgreSQL 테스트를 건너뜀")),
]


def reset_postgres(url: str) -> None:
    db = connect(url)
    with db.transaction():
        db.execute("DROP TABLE IF EXISTS " + ", ".join(reversed(TABLES)) + " CASCADE")
    db.close()


@pytest.fixture(params=BACKENDS)
def make_db(request):
    """빈 DB를 만드는 함수. sqlite(인메모리)와 postgresql(설정된 경우) 두 번 실행된다."""
    opened = []

    def factory():
        if request.param == "postgresql":
            reset_postgres(PG_TEST_URL)
            db = connect(PG_TEST_URL)
        else:
            db = create_db()
        opened.append(db)
        return db

    yield factory
    for db in opened:
        db.close()


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
