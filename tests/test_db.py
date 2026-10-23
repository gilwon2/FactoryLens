import sqlite3
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

import pandas as pd
import pytest

from factorylens.db import _normalize, connect, connect_from_env, create_db, load_data
from factorylens.validation import validate_upload

SAMPLE_DIR = Path(__file__).resolve().parent.parent / "data" / "sample"


def count(connection, table):
    return connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]


def test_create_db_has_tables_and_foreign_keys_on():
    connection = create_db()
    tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
    assert tables == {"ingest_batch", "production", "defect", "ingest_issue"}
    assert connection.execute("PRAGMA foreign_keys").fetchone()[0] == 1
    assert not connection.persistent


def test_load_counts_and_python_types(conn):
    assert count(conn, "production") == 5
    assert count(conn, "defect") == 4
    # numpy int64가 아니라 정수(INTEGER)로 저장되어야 SUM 등이 올바로 동작한다
    assert conn.execute("SELECT typeof(produced_qty) FROM production LIMIT 1").fetchone()[0] == "integer"


def test_each_create_db_is_independent(conn):
    # 재업로드 = 새 DB. 이전 데이터가 남지 않는다
    assert count(create_db(), "production") == 0
    assert count(conn, "production") == 5


def test_duplicate_primary_key_rolls_back_everything(production_df, defects_df):
    connection = create_db()
    duplicated = pd.concat([production_df, production_df.head(1)])
    with pytest.raises(sqlite3.IntegrityError):
        load_data(connection, duplicated, defects_df)
    assert count(connection, "production") == 0   # 일부만 들어가지 않는다 (트랜잭션)


def test_foreign_key_violation_rejected(production_df):
    orphan = pd.DataFrame([["NOPE", "SCRATCH", 1]], columns=["lot_id", "defect_type", "defect_qty"])
    with pytest.raises(sqlite3.IntegrityError):
        load_data(create_db(), production_df, orphan)


def test_negative_quantity_rejected_by_check(production_df, defects_df):
    production_df.loc[0, "produced_qty"] = -1
    with pytest.raises(sqlite3.IntegrityError):
        load_data(create_db(), production_df, defects_df)


def test_load_validated_sample_data():
    result = validate_upload((SAMPLE_DIR / "production_sample.csv").read_bytes(),
                             (SAMPLE_DIR / "defects_sample.csv").read_bytes())
    connection = create_db()
    load_data(connection, result.production, result.defects)
    assert count(connection, "production") == len(result.production)
    assert count(connection, "defect") == len(result.defects)


# ---------- 저장 모드와 DB 주소 ----------

def test_sqlite_file_db_keeps_data_between_connections(tmp_path, production_df, defects_df):
    url = f"sqlite:///{tmp_path / 'factorylens.db'}"
    first = connect(url)
    assert first.persistent
    load_data(first, production_df, defects_df)
    first.close()
    second = connect(url)                       # 스키마는 IF NOT EXISTS라 다시 연결해도 데이터가 남는다
    assert count(second, "production") == 5


def test_connect_from_env(monkeypatch, tmp_path):
    monkeypatch.delenv("FACTORYLENS_DATABASE_URL", raising=False)
    assert not connect_from_env().persistent
    monkeypatch.setenv("FACTORYLENS_DATABASE_URL", f"sqlite:///{tmp_path / 'a.db'}")
    assert connect_from_env().persistent


def test_unknown_database_url_rejected():
    with pytest.raises(ValueError):
        connect("mysql://localhost/db")


def test_nested_transaction_rolls_back_as_one(production_df, defects_df):
    # load_data는 바깥 트랜잭션 안에서 부르면 그 일부가 된다. 바깥에서 실패하면 load_data 내용도 취소된다
    connection = create_db()
    with pytest.raises(RuntimeError):
        with connection.transaction():
            load_data(connection, production_df, defects_df)
            raise RuntimeError("적재 도중 실패")
    assert count(connection, "production") == 0


@pytest.mark.parametrize("value, expected", [
    (Decimal("18"), 18),          # SUM(bigint) -> 정수
    (Decimal("3.00"), 3.0),       # ROUND(..., 2) -> 실수
    (date(2026, 9, 1), "2026-09-01"),
    (datetime(2026, 9, 1, 8, 30, 5), "2026-09-01 08:30:05"),
    ("PRESS", "PRESS"),
    (None, None),
])
def test_postgres_values_normalized(value, expected):
    result = _normalize(value)
    assert result == expected and type(result) is type(expected)
