import sqlite3
from pathlib import Path

import pandas as pd
import pytest

from factorylens.db import create_db, load_data
from factorylens.validation import validate_upload

SAMPLE_DIR = Path(__file__).resolve().parent.parent / "data" / "sample"


def count(connection, table):
    return connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]


def test_create_db_has_tables_and_foreign_keys_on():
    connection = create_db()
    tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
    assert tables == {"production", "defect"}
    assert connection.execute("PRAGMA foreign_keys").fetchone()[0] == 1


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
