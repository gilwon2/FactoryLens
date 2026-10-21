"""SQL로 생산량·불량 수량·불량률을 집계한다.

지표 정의 (docs/metrics.md와 동일)
- 생산량      = SUM(produced_qty)
- 불량 수량   = SUM(defect_qty)                     (LOT 수가 아니라 불량품 개수)
- 불량률(%)   = 불량 수량 / 생산량 x 100, 소수 둘째 자리 반올림. 생산량이 0이면 NULL(None)
- 그룹 불량률은 가중 평균(합계 / 합계)이다. LOT별 불량률의 단순 평균이 아니다.
- 불량유형 비중(%) = 유형별 불량 수량 / 전체 불량 수량 x 100. 전체 불량이 0이면 None

필터 인자 (모든 집계 함수 공통)
- start_date, end_date : 'YYYY-MM-DD' 문자열. None이면 제한 없음. 생산일(work_date) 기준, 양 끝 포함
- processes, products  : 값 목록. None이면 전체, 빈 리스트 []이면 아무것도 선택하지 않은 것(결과 0건)
"""
import sqlite3

import pandas as pd

# SQL 문자열에 직접 넣는 컬럼명은 반드시 이 목록에 있는 것만 허용한다 (SQL 인젝션 방지)
GROUP_COLUMNS = {"process", "product", "work_date"}
FILTER_COLUMNS = {"process", "product"}

# 0으로 나누기를 피하는 불량률 계산식. 100.0을 곱해 정수 나눗셈(5/400 = 0)을 막는다.
RATE_SQL = """
    CASE WHEN SUM(produced_qty) = 0 THEN NULL
         ELSE ROUND(100.0 * SUM(defect_qty) / SUM(produced_qty), 2)
    END
"""


def safe_rate(numerator, denominator):
    """분모가 0(또는 None)이면 None, 아니면 백분율을 소수 둘째 자리로 반올림해 반환한다."""
    if not denominator:
        return None
    return round(100 * numerator / denominator, 2)


def build_where(start_date=None, end_date=None, processes=None, products=None) -> tuple[str, list]:
    """필터 조건을 WHERE 절 문자열과 ? 자리에 들어갈 값 목록으로 만든다.

    값은 절대 SQL 문자열에 직접 넣지 않고 ? 플레이스홀더로 전달한다.
    """
    conditions = ["1 = 1"]  # 조건이 하나도 없을 때도 WHERE 절이 성립하도록
    params = []
    if start_date is not None:
        conditions.append("p.work_date >= ?")
        params.append(start_date)
    if end_date is not None:
        conditions.append("p.work_date <= ?")
        params.append(end_date)
    for column, values in (("process", processes), ("product", products)):
        if values is None:
            continue
        if len(values) == 0:
            conditions.append("1 = 0")  # 아무것도 선택하지 않음 -> 결과 없음
        else:
            placeholders = ", ".join("?" for _ in values)  # 값 3개면 "?, ?, ?"
            conditions.append(f"p.{column} IN ({placeholders})")
            params.extend(values)
    return " AND ".join(conditions), params


def lot_cte(where: str) -> str:
    """필터를 적용한 LOT 목록에 LOT별 불량 합계를 붙인 CTE.

    불량을 LOT 단위로 먼저 합산한 뒤 JOIN한다. defect를 production에 바로 JOIN하면
    불량 유형이 2개인 LOT의 produced_qty가 2번 더해지는 'JOIN 팬아웃'이 생긴다.
    LEFT JOIN + COALESCE로 불량 행이 없는 LOT도 불량 0으로 포함한다.
    """
    return f"""
    WITH lot_defect AS (
        SELECT lot_id, SUM(defect_qty) AS defect_qty
        FROM defect
        GROUP BY lot_id
    ),
    lot AS (
        SELECT p.lot_id, p.work_date, p.process, p.product, p.produced_qty,
               COALESCE(ld.defect_qty, 0) AS defect_qty
        FROM production p
        LEFT JOIN lot_defect ld ON ld.lot_id = p.lot_id
        WHERE {where}
    )
    """


def summary_kpi(conn: sqlite3.Connection, **filters) -> dict:
    """필터 범위 전체의 LOT 수, 생산량, 불량 수량, 불량률."""
    where, params = build_where(**filters)
    sql = lot_cte(where) + f"""
    SELECT COUNT(*)                      AS lot_count,
           COALESCE(SUM(produced_qty), 0) AS produced_qty,   -- 행이 없으면 SUM은 NULL이므로 0으로
           COALESCE(SUM(defect_qty), 0)   AS defect_qty,
           {RATE_SQL}                     AS defect_rate_pct
    FROM lot
    """
    lot_count, produced, defects, rate = conn.execute(sql, params).fetchone()
    return {"lot_count": lot_count, "produced_qty": produced, "defect_qty": defects, "defect_rate_pct": rate}


def by_group(conn: sqlite3.Connection, group_column: str, **filters) -> pd.DataFrame:
    """공정별·제품별·일자별 집계. 일자별은 날짜순, 나머지는 불량률이 높은 순(NULL은 맨 뒤)."""
    if group_column not in GROUP_COLUMNS:
        raise ValueError(f"지원하지 않는 그룹 컬럼입니다: {group_column}")
    order_by = "work_date" if group_column == "work_date" else "defect_rate_pct IS NULL, defect_rate_pct DESC, " + group_column

    where, params = build_where(**filters)
    sql = lot_cte(where) + f"""
    SELECT {group_column},
           COUNT(*)          AS lot_count,
           SUM(produced_qty) AS produced_qty,
           SUM(defect_qty)   AS defect_qty,
           {RATE_SQL}        AS defect_rate_pct
    FROM lot
    GROUP BY {group_column}
    ORDER BY {order_by}
    """
    return pd.read_sql_query(sql, conn, params=params)


def by_defect_type(conn: sqlite3.Connection, **filters) -> pd.DataFrame:
    """불량 유형별 수량과 전체 불량 대비 비중. 수량이 많은 순."""
    where, params = build_where(**filters)
    # 불량 유형별 집계는 defect 행 단위로 합하므로 팬아웃이 없다. 필터를 위해 production과 JOIN한다.
    sql = f"""
    SELECT d.defect_type, SUM(d.defect_qty) AS defect_qty
    FROM defect d
    JOIN production p ON p.lot_id = d.lot_id
    WHERE {where}
    GROUP BY d.defect_type
    HAVING SUM(d.defect_qty) > 0
    ORDER BY defect_qty DESC, d.defect_type
    """
    df = pd.read_sql_query(sql, conn, params=params)
    total = int(df["defect_qty"].sum())
    df["share_pct"] = [safe_rate(qty, total) for qty in df["defect_qty"]]
    return df


def distinct_values(conn: sqlite3.Connection, column: str) -> list[str]:
    """필터 선택지로 쓸 공정·제품 목록."""
    if column not in FILTER_COLUMNS:
        raise ValueError(f"지원하지 않는 필터 컬럼입니다: {column}")
    return [row[0] for row in conn.execute(f"SELECT DISTINCT {column} FROM production ORDER BY {column}")]


def date_range(conn: sqlite3.Connection) -> tuple[str | None, str | None]:
    """데이터의 최초·최종 생산일."""
    return conn.execute("SELECT MIN(work_date), MAX(work_date) FROM production").fetchone()
