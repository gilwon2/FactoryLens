"""세션 단위 SQLite DB를 만들고 검증된 데이터를 적재한다.

- DB는 메모리(:memory:)에만 존재한다. 업로드할 때마다 create_db()로 새 DB를 만들어 교체하므로
  이전 업로드나 다른 사용자의 데이터와 섞이지 않고, 서버 디스크에도 남지 않는다.
- PK·CHECK·FK 제약은 Python 검증(validation.py)이 놓친 것을 잡는 2차 안전장치다.
"""
import sqlite3

import pandas as pd

SCHEMA_SQL = """
CREATE TABLE production (
    lot_id       TEXT PRIMARY KEY,
    work_date    TEXT NOT NULL,              -- 'YYYY-MM-DD' 문자열 (문자열 비교로 날짜 범위 검색 가능)
    process      TEXT NOT NULL,
    product      TEXT NOT NULL,
    produced_qty INTEGER NOT NULL CHECK (produced_qty >= 0)
);

CREATE TABLE defect (
    lot_id      TEXT NOT NULL REFERENCES production(lot_id),
    defect_type TEXT NOT NULL,
    defect_qty  INTEGER NOT NULL CHECK (defect_qty >= 0),
    PRIMARY KEY (lot_id, defect_type)
);

CREATE INDEX idx_production_date ON production(work_date);
"""

PRODUCTION_INSERT = """
INSERT INTO production (lot_id, work_date, process, product, produced_qty)
VALUES (?, ?, ?, ?, ?)
"""
DEFECT_INSERT = "INSERT INTO defect (lot_id, defect_type, defect_qty) VALUES (?, ?, ?)"


def create_db() -> sqlite3.Connection:
    """빈 테이블이 있는 새 인메모리 DB 연결을 반환한다."""
    # Streamlit은 화면을 다시 그릴 때 다른 스레드에서 실행될 수 있어 check_same_thread=False가 필요하다.
    conn = sqlite3.connect(":memory:", check_same_thread=False)
    conn.execute("PRAGMA foreign_keys = ON")  # SQLite는 FK 검사가 기본으로 꺼져 있다
    conn.executescript(SCHEMA_SQL)
    return conn


def _to_rows(df: pd.DataFrame, columns: list[str]) -> list[tuple]:
    # astype(object)로 numpy 정수(int64)를 파이썬 int로 바꾼다. sqlite3는 numpy 타입을 바로 저장하지 못한다.
    return [tuple(row) for row in df[columns].astype(object).itertuples(index=False, name=None)]


def load_data(conn: sqlite3.Connection, production: pd.DataFrame, defects: pd.DataFrame) -> None:
    """두 테이블에 데이터를 넣는다. 하나라도 실패하면 전부 취소된다(트랜잭션)."""
    with conn:  # 블록이 정상 종료되면 COMMIT, 예외가 나면 ROLLBACK
        conn.executemany(PRODUCTION_INSERT, _to_rows(production, ["lot_id", "work_date", "process", "product", "produced_qty"]))
        conn.executemany(DEFECT_INSERT, _to_rows(defects, ["lot_id", "defect_type", "defect_qty"]))
