"""SQL로 생산량·불량 수량·불량률을 집계한다.

지표 정의 (docs/metrics.md와 동일)
- 생산량      = SUM(produced_qty)
- 불량 수량   = SUM(defect_qty)                     (LOT 수가 아니라 불량품 개수)
- 불량률(%)   = 불량 수량 / 생산량 x 100, 소수 둘째 자리 반올림. 생산량이 0이면 NULL(None)
- 그룹 불량률은 가중 평균(합계 / 합계)이다. LOT별 불량률의 단순 평균이 아니다.
- 불량유형 비중(%) = 유형별 불량 수량 / 전체 불량 수량 x 100. 전체 불량이 0이면 None
- 누적 비중(%)     = 불량 수량이 많은 유형부터 더한 비중 (파레토 분석)
- p 관리도         = 일자별 불량률과 3σ 관리 한계. 공정마다 기준 불량률이 달라 공정 하나씩 그린다

필터 인자 (모든 집계 함수 공통)
- start_date, end_date : 'YYYY-MM-DD' 문자열. None이면 제한 없음. 생산일(work_date) 기준, 양 끝 포함
- processes, products  : 값 목록. None이면 전체, 빈 리스트 []이면 아무것도 선택하지 않은 것(결과 0건)
"""
import math

import pandas as pd

from factorylens.db import Database

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


def summary_kpi(conn: Database, **filters) -> dict:
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


def by_group(conn: Database, group_column: str, **filters) -> pd.DataFrame:
    """공정별·제품별·일자별 집계. 일자별은 날짜순, 나머지는 불량률이 높은 순(NULL은 맨 뒤)."""
    if group_column not in GROUP_COLUMNS:
        raise ValueError(f"지원하지 않는 그룹 컬럼입니다: {group_column}")
    # NULLS LAST: 불량률을 계산할 수 없는 그룹(생산량 0)은 맨 뒤. SQLite·PostgreSQL 모두 지원하는 문법이다
    order_by = "work_date" if group_column == "work_date" else f"defect_rate_pct DESC NULLS LAST, {group_column}"

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
    return conn.query_df(sql, params)


def by_defect_type(conn: Database, **filters) -> pd.DataFrame:
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
    df = conn.query_df(sql, params)
    if df.empty:
        return pd.DataFrame(columns=["defect_type", "defect_qty", "share_pct", "cum_share_pct"])
    total = int(df["defect_qty"].sum())
    df["share_pct"] = [safe_rate(qty, total) for qty in df["defect_qty"]]
    # 누적 비중은 반올림한 비중을 더하지 않고 수량의 누적합으로 계산한다 (마지막 값이 정확히 100이 되도록)
    df["cum_share_pct"] = [safe_rate(qty, total) for qty in df["defect_qty"].cumsum()]
    return df


def vital_few(defect_types: pd.DataFrame, threshold_pct: float = 80.0) -> int:
    """누적 비중이 threshold_pct에 처음 도달하는 데 필요한 상위 유형 수 (파레토의 '핵심 소수')."""
    if defect_types.empty:
        return 0
    reached = defect_types["cum_share_pct"] >= threshold_pct
    return int(reached.values.argmax()) + 1


P_CHART_COLUMNS = ["work_date", "produced_qty", "defect_qty", "p_pct", "cl_pct", "ucl_pct", "lcl_pct", "out_of_control"]


def p_chart(conn: Database, process: str, sigma: float = 3.0, **filters) -> pd.DataFrame:
    """공정 하나의 일자별 p 관리도(불량률 관리도).

    - 중심선 CL  = 기간 전체 불량 합계 / 생산량 합계 (가중 평균 불량률 p̄)
    - 관리 한계  = p̄ ± 3 x sqrt(p̄(1-p̄) / n_i)   (n_i = 그날 생산량. 날마다 생산량이 달라 한계도 날마다 다르다)
    - 하한이 0보다 작으면 0
    - 생산량이 0인 날은 불량률이 없으므로 제외한다
    - 한계 밖이면 out_of_control = True. 판정은 반올림 전 값으로 한다

    관리 한계를 같은 기간 데이터로 계산하는 '해석용(Phase I)' 관리도다.
    """
    daily = by_group(conn, "work_date", **{**filters, "processes": [process]})
    daily = daily[daily["produced_qty"] > 0]
    if daily.empty:
        return pd.DataFrame(columns=P_CHART_COLUMNS)

    n = daily["produced_qty"].astype(float)
    d = daily["defect_qty"].astype(float)
    p = d / n
    p_bar = d.sum() / n.sum()
    spread = sigma * (p_bar * (1 - p_bar) / n).map(math.sqrt)
    ucl = p_bar + spread
    lcl = (p_bar - spread).clip(lower=0)

    result = daily[["work_date", "produced_qty", "defect_qty"]].copy()
    result["p_pct"] = (100 * p).round(3)
    result["cl_pct"] = round(100 * p_bar, 3)
    result["ucl_pct"] = (100 * ucl).round(3)
    result["lcl_pct"] = (100 * lcl).round(3)
    result["out_of_control"] = (p > ucl) | (p < lcl)
    return result.reset_index(drop=True)


def distinct_values(conn: Database, column: str) -> list[str]:
    """필터 선택지로 쓸 공정·제품 목록."""
    if column not in FILTER_COLUMNS:
        raise ValueError(f"지원하지 않는 필터 컬럼입니다: {column}")
    return [row[0] for row in conn.execute(f"SELECT DISTINCT {column} FROM production ORDER BY {column}")]


def date_range(conn: Database) -> tuple[str | None, str | None]:
    """데이터의 최초·최종 생산일."""
    return conn.execute("SELECT MIN(work_date), MAX(work_date) FROM production").fetchone()


def batch_history(conn: Database) -> pd.DataFrame:
    """적재 이력 (최근 배치부터)."""
    return conn.query_df("""
    SELECT batch_id, ingested_at, source, status, production_rows_read, lots_inserted, lots_skipped,
           lots_conflicted, defect_rows_inserted, error_count, warning_count
    FROM ingest_batch
    ORDER BY batch_id DESC
    """)


def batch_issues(conn: Database, batch_id: int) -> pd.DataFrame:
    """배치 하나의 오류·경고·안내 기록."""
    return conn.query_df("""
    SELECT level, file, row_no, column_name, reason
    FROM ingest_issue
    WHERE batch_id = ?
    ORDER BY CASE level WHEN 'error' THEN 0 WHEN 'warning' THEN 1 ELSE 2 END, file, row_no NULLS FIRST
    """, [batch_id])
