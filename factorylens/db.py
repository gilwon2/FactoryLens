"""DB 연결과 스키마. SQLite(인메모리·파일)와 PostgreSQL을 같은 방식으로 쓴다.

저장 모드
- 데모 모드 (기본): FACTORYLENS_DATABASE_URL이 없으면 세션마다 인메모리 SQLite를 새로 만든다.
  업로드할 때마다 새 DB로 교체하므로 다른 사용자의 데이터와 섞이지 않고 서버 디스크에도 남지 않는다.
  공개 배포(Streamlit Community Cloud)는 이 모드로 실행한다.
- 누적 모드: FACTORYLENS_DATABASE_URL에 DB 주소를 주면 업로드가 그 DB에 계속 쌓인다.
    postgresql://user:password@host:5432/dbname   (Docker Compose 실행 시)
    sqlite:///factorylens.db                       (Docker 없이 로컬에서 누적 저장을 써 볼 때)

PK·CHECK·FK 제약은 Python 검증(validation.py)이 놓친 것을 잡는 2차 안전장치다.

SQL은 두 DB에서 똑같이 동작하는 문법만 쓴다. 차이는 이 모듈이 흡수한다.
- 값 자리표시자: SQL에는 '?'로 쓰고, PostgreSQL 드라이버(psycopg)에 넘길 때 '%s'로 바꾼다.
- 결과 값: PostgreSQL이 돌려주는 Decimal·date는 int/float·'YYYY-MM-DD' 문자열로 바꿔
  어느 DB를 쓰든 화면·테스트가 같은 값을 받게 한다.
"""
import os
import sqlite3
from contextlib import contextmanager
from datetime import date, datetime
from decimal import Decimal

import pandas as pd

DATABASE_URL_ENV = "FACTORYLENS_DATABASE_URL"

# 테이블 생성 순서 = FK가 가리키는 쪽이 먼저
TABLES = ["ingest_batch", "production", "defect", "ingest_issue"]

SQLITE_SCHEMA = """
CREATE TABLE IF NOT EXISTS ingest_batch (
    batch_id             INTEGER PRIMARY KEY,
    ingested_at          TEXT    NOT NULL,
    source               TEXT    NOT NULL,
    status               TEXT    NOT NULL CHECK (status IN ('loaded', 'rejected')),
    production_rows_read INTEGER NOT NULL,
    defect_rows_read     INTEGER NOT NULL,
    lots_inserted        INTEGER NOT NULL,
    lots_skipped         INTEGER NOT NULL,
    lots_conflicted      INTEGER NOT NULL,
    defect_rows_inserted INTEGER NOT NULL,
    error_count          INTEGER NOT NULL,
    warning_count        INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS production (
    lot_id       TEXT PRIMARY KEY,
    work_date    TEXT NOT NULL,              -- 'YYYY-MM-DD' 문자열 (문자열 비교로 날짜 범위 검색 가능)
    process      TEXT NOT NULL,
    product      TEXT NOT NULL,
    produced_qty INTEGER NOT NULL CHECK (produced_qty >= 0),
    batch_id     INTEGER REFERENCES ingest_batch(batch_id)
);

CREATE TABLE IF NOT EXISTS defect (
    lot_id      TEXT NOT NULL REFERENCES production(lot_id),
    defect_type TEXT NOT NULL,
    defect_qty  INTEGER NOT NULL CHECK (defect_qty >= 0),
    PRIMARY KEY (lot_id, defect_type)
);

CREATE TABLE IF NOT EXISTS ingest_issue (
    batch_id    INTEGER NOT NULL REFERENCES ingest_batch(batch_id),
    level       TEXT    NOT NULL,
    file        TEXT,
    row_no      INTEGER,
    column_name TEXT,
    reason      TEXT    NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_production_date ON production(work_date);
CREATE INDEX IF NOT EXISTS idx_issue_batch ON ingest_issue(batch_id);
"""

# PostgreSQL: 날짜는 DATE 타입, 배치 번호는 IDENTITY로 자동 증가.
# 코드값 컬럼은 COLLATE "C"로 SQLite와 같은 정렬(바이트 순서)이 되게 한다.
POSTGRES_SCHEMA = """
CREATE TABLE IF NOT EXISTS ingest_batch (
    batch_id             BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    ingested_at          TIMESTAMPTZ NOT NULL,
    source               TEXT    NOT NULL,
    status               TEXT    NOT NULL CHECK (status IN ('loaded', 'rejected')),
    production_rows_read INTEGER NOT NULL,
    defect_rows_read     INTEGER NOT NULL,
    lots_inserted        INTEGER NOT NULL,
    lots_skipped         INTEGER NOT NULL,
    lots_conflicted      INTEGER NOT NULL,
    defect_rows_inserted INTEGER NOT NULL,
    error_count          INTEGER NOT NULL,
    warning_count        INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS production (
    lot_id       TEXT COLLATE "C" PRIMARY KEY,
    work_date    DATE NOT NULL,
    process      TEXT COLLATE "C" NOT NULL,
    product      TEXT COLLATE "C" NOT NULL,
    produced_qty INTEGER NOT NULL CHECK (produced_qty >= 0),
    batch_id     BIGINT REFERENCES ingest_batch(batch_id)
);

CREATE TABLE IF NOT EXISTS defect (
    lot_id      TEXT COLLATE "C" NOT NULL REFERENCES production(lot_id),
    defect_type TEXT COLLATE "C" NOT NULL,
    defect_qty  INTEGER NOT NULL CHECK (defect_qty >= 0),
    PRIMARY KEY (lot_id, defect_type)
);

CREATE TABLE IF NOT EXISTS ingest_issue (
    batch_id    BIGINT  NOT NULL REFERENCES ingest_batch(batch_id),
    level       TEXT    NOT NULL,
    file        TEXT,
    row_no      INTEGER,
    column_name TEXT,
    reason      TEXT    NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_production_date ON production(work_date);
CREATE INDEX IF NOT EXISTS idx_issue_batch ON ingest_issue(batch_id);
"""

PRODUCTION_INSERT = """
INSERT INTO production (lot_id, work_date, process, product, produced_qty, batch_id)
VALUES (?, ?, ?, ?, ?, ?)
"""
DEFECT_INSERT = "INSERT INTO defect (lot_id, defect_type, defect_qty) VALUES (?, ?, ?)"


def _normalize(value):
    """PostgreSQL 결과 값을 SQLite와 같은 파이썬 타입으로 맞춘다."""
    if isinstance(value, Decimal):
        # SUM(bigint)처럼 소수 자릿수가 없는 numeric은 정수로, ROUND(..., 2) 결과는 실수로
        return int(value) if value.as_tuple().exponent >= 0 else float(value)
    if isinstance(value, datetime):  # datetime은 date의 하위 클래스라 먼저 검사한다
        return value.isoformat(sep=" ", timespec="seconds")
    if isinstance(value, date):
        return value.isoformat()
    return value


class Rows:
    """execute() 결과. sqlite3 커서처럼 fetchone()·fetchall()·반복을 지원한다."""

    def __init__(self, cursor):
        self.columns = [d[0] for d in cursor.description] if cursor.description else []
        self._rows = [tuple(_normalize(v) for v in row) for row in cursor.fetchall()] if self.columns else []

    def fetchone(self):
        return self._rows[0] if self._rows else None

    def fetchall(self):
        return list(self._rows)

    def __iter__(self):
        return iter(self._rows)


class Database:
    """sqlite3·psycopg 연결을 같은 인터페이스로 감싼 얇은 래퍼."""

    def __init__(self, raw, dialect: str, url: str | None):
        self.raw = raw
        self.dialect = dialect          # "sqlite" | "postgresql"
        self.url = url                  # None이면 인메모리 데모 모드
        self._depth = 0                 # 진행 중인 transaction() 중첩 깊이
        if dialect == "postgresql":
            import psycopg
            self.Error = psycopg.Error
        else:
            self.Error = sqlite3.Error

    @property
    def persistent(self) -> bool:
        """업로드가 누적되는 DB인지 (False = 세션별 인메모리)."""
        return self.url is not None

    def _sql(self, sql: str) -> str:
        return sql.replace("?", "%s") if self.dialect == "postgresql" else sql

    def execute(self, sql: str, params=()) -> Rows:
        cursor = self.raw.execute(self._sql(sql), tuple(params))
        return Rows(cursor)

    def executemany(self, sql: str, rows: list[tuple]) -> None:
        if not rows:
            return
        if self.dialect == "postgresql":
            with self.raw.cursor() as cursor:
                cursor.executemany(self._sql(sql), rows)
        else:
            self.raw.executemany(sql, rows)

    def query_df(self, sql: str, params=()) -> pd.DataFrame:
        rows = self.execute(sql, params)
        return pd.DataFrame.from_records(rows.fetchall(), columns=rows.columns).infer_objects()

    def insert_returning_id(self, sql: str, params, id_column: str) -> int:
        """INSERT 후 자동 증가 번호를 돌려준다."""
        if self.dialect == "postgresql":
            return self.execute(f"{sql} RETURNING {id_column}", params).fetchone()[0]
        return self.raw.execute(sql, tuple(params)).lastrowid

    @contextmanager
    def transaction(self):
        """블록이 정상 종료되면 COMMIT, 예외가 나면 ROLLBACK.

        이미 트랜잭션 안이면 새로 열지 않고 바깥 트랜잭션에 합류한다.
        (sqlite3의 `with conn:`을 중첩하면 안쪽 블록이 끝날 때 COMMIT되어 버리기 때문이다.)
        """
        if self._depth:
            yield self
            return
        self._depth += 1
        try:
            if self.dialect == "postgresql":
                with self.raw.transaction():
                    yield self
            else:
                with self.raw:
                    yield self
        finally:
            self._depth -= 1

    def close(self) -> None:
        self.raw.close()


def connect(url: str | None = None) -> Database:
    """DB에 연결하고 스키마가 없으면 만든다.

    url이 None이면 새 인메모리 SQLite(데모 모드).
    """
    if url is None or url == "sqlite://":
        raw = sqlite3.connect(":memory:", check_same_thread=False)
        db = Database(raw, "sqlite", None)
    elif url.startswith("sqlite:///"):
        # Streamlit은 화면을 다시 그릴 때 다른 스레드에서 실행될 수 있어 check_same_thread=False가 필요하다.
        raw = sqlite3.connect(url.removeprefix("sqlite:///"), check_same_thread=False)
        db = Database(raw, "sqlite", url)
    elif url.startswith(("postgresql://", "postgres://")):
        import psycopg  # 누적 모드에서만 필요하므로 여기서 불러온다
        # autocommit: 조회는 바로 끝내고, 쓰기는 transaction() 블록으로 묶는다
        raw = psycopg.connect(url, autocommit=True, connect_timeout=5)
        db = Database(raw, "postgresql", url)
    else:
        raise ValueError("지원하지 않는 DB 주소입니다. postgresql:// 또는 sqlite:/// 로 시작해야 합니다.")

    if db.dialect == "sqlite":
        db.raw.execute("PRAGMA foreign_keys = ON")  # SQLite는 FK 검사가 기본으로 꺼져 있다
        db.raw.executescript(SQLITE_SCHEMA)
    else:
        with db.transaction():
            db.raw.execute(POSTGRES_SCHEMA)
    return db


def connect_from_env() -> Database:
    """환경 변수 FACTORYLENS_DATABASE_URL로 연결한다. 없으면 인메모리 데모 모드."""
    return connect(os.environ.get(DATABASE_URL_ENV) or None)


def create_db() -> Database:
    """빈 테이블이 있는 새 인메모리 DB를 반환한다 (데모 모드, 업로드마다 새로 만든다)."""
    return connect(None)


def _to_rows(df: pd.DataFrame, columns: list[str]) -> list[tuple]:
    # astype(object)로 numpy 정수(int64)를 파이썬 int로 바꾼다. DB 드라이버는 numpy 타입을 바로 저장하지 못한다.
    return [tuple(row) for row in df[columns].astype(object).itertuples(index=False, name=None)]


def load_data(db: Database, production: pd.DataFrame, defects: pd.DataFrame, batch_id: int | None = None) -> None:
    """두 테이블에 데이터를 넣는다. 하나라도 실패하면 전부 취소된다(트랜잭션).

    이미 transaction() 블록 안에서 호출하면 그 트랜잭션의 일부가 된다(ingest.py).
    """
    production = production.assign(batch_id=batch_id)
    with db.transaction():
        db.executemany(PRODUCTION_INSERT, _to_rows(production, ["lot_id", "work_date", "process", "product", "produced_qty", "batch_id"]))
        db.executemany(DEFECT_INSERT, _to_rows(defects, ["lot_id", "defect_type", "defect_qty"]))
