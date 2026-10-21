"""FactoryLens Streamlit 앱.

Streamlit은 버튼·위젯을 조작할 때마다 이 파일 전체를 위에서부터 다시 실행한다.
그래서 다시 실행되어도 유지해야 하는 값(DB 연결, 검증 결과)은 st.session_state에 저장한다.
"""
import sqlite3
from pathlib import Path

import pandas as pd
import streamlit as st

from factorylens import metrics
from factorylens.db import create_db, load_data
from factorylens.validation import DEFECT_COLUMNS, PRODUCTION_COLUMNS, validate_upload

SAMPLE_DIR = Path(__file__).resolve().parent / "data" / "sample"

COLUMN_LABELS = {
    "process": "공정",
    "product": "제품",
    "work_date": "생산일",
    "lot_count": "LOT 수",
    "produced_qty": "생산량",
    "defect_qty": "불량 수량",
    "defect_rate_pct": "불량률(%)",
    "defect_type": "불량 유형",
    "share_pct": "비중(%)",
}
LEVEL_LABELS = {"error": "오류(제외)", "warning": "경고", "info": "안내"}
LEVEL_ORDER = {"error": 0, "warning": 1, "info": 2}


# ---------------------------------------------------------------------------
# 표시용 도우미
# ---------------------------------------------------------------------------

def format_rate(value) -> str:
    """불량률 표시. 생산량 0으로 계산할 수 없는 값(None/NaN)은 '-'."""
    return "-" if pd.isna(value) else f"{value:.2f}"


def to_display(df: pd.DataFrame) -> pd.DataFrame:
    """집계 결과를 화면용으로 바꾼다: 숫자에 천 단위 쉼표, 비율은 소수 둘째 자리, 컬럼명은 한글."""
    shown = df.copy()
    for column in ("lot_count", "produced_qty", "defect_qty"):
        if column in shown:
            shown[column] = shown[column].map(lambda n: f"{int(n):,}")
    for column in ("defect_rate_pct", "share_pct"):
        if column in shown:
            shown[column] = shown[column].map(format_rate)
    return shown.rename(columns=COLUMN_LABELS)


def issues_table(issues: list[dict]) -> pd.DataFrame:
    table = pd.DataFrame(issues, columns=["level", "file", "row", "column", "reason"])
    table["order"] = table["level"].map(LEVEL_ORDER)
    table = table.sort_values(["order", "file", "row"], na_position="first").drop(columns="order")
    table["row"] = table["row"].astype("Int64")  # None이 섞여도 정수로 표시 (2.0 -> 2)
    table["level"] = table["level"].map(LEVEL_LABELS)
    return table.rename(columns={"level": "구분", "file": "파일", "row": "줄 번호", "column": "컬럼", "reason": "사유"})


# ---------------------------------------------------------------------------
# 업로드 -> 검증 -> 적재
# ---------------------------------------------------------------------------

def load(production_data: bytes, defects_data: bytes | None, source: str) -> None:
    """검증하고, 통과한 데이터만 새 세션 DB에 적재한 뒤 session_state를 교체한다."""
    result = validate_upload(production_data, defects_data)

    conn = None
    if result.ok:
        conn = create_db()
        try:
            load_data(conn, result.production, result.defects)
        except sqlite3.Error as e:  # 검증을 통과했다면 일어나지 않아야 하는 2차 방어선
            result.fatal = f"DB 적재 중 오류가 발생했습니다: {e}"
            conn = None

    old_conn = st.session_state.get("conn")
    if old_conn is not None:
        old_conn.close()
    st.session_state["result"] = result
    st.session_state["conn"] = conn
    st.session_state["source"] = source


def sidebar() -> None:
    with st.sidebar:
        st.header("데이터 불러오기")
        production_file = st.file_uploader("생산 데이터 (production.csv) · 필수", type=["csv"])
        defects_file = st.file_uploader("불량 데이터 (defects.csv)", type=["csv"],
                                        help="올리지 않으면 모든 LOT의 불량 수량을 0으로 계산합니다.")
        if st.button("검증 후 불러오기", type="primary", disabled=production_file is None, width="stretch"):
            with st.spinner("검증 중..."):
                load(production_file.getvalue(),
                     defects_file.getvalue() if defects_file is not None else None,
                     source=production_file.name)

        st.divider()
        st.caption("파일이 없다면 가상 샘플 데이터로 체험해 보세요.")
        if st.button("샘플 데이터로 체험", width="stretch"):
            load((SAMPLE_DIR / "production_sample.csv").read_bytes(),
                 (SAMPLE_DIR / "defects_sample.csv").read_bytes(),
                 source="가상 샘플 데이터")
        col1, col2 = st.columns(2)
        col1.download_button("샘플 생산 CSV", (SAMPLE_DIR / "production_sample.csv").read_bytes(),
                             file_name="production_sample.csv", mime="text/csv", on_click="ignore")
        col2.download_button("샘플 불량 CSV", (SAMPLE_DIR / "defects_sample.csv").read_bytes(),
                             file_name="defects_sample.csv", mime="text/csv", on_click="ignore")


# ---------------------------------------------------------------------------
# 화면 구역
# ---------------------------------------------------------------------------

def show_guide() -> None:
    st.info("왼쪽에서 CSV를 업로드하거나 **샘플 데이터로 체험** 버튼을 눌러 주세요.")
    col1, col2 = st.columns(2)
    with col1:
        st.subheader("production.csv (LOT당 1행)")
        st.table(pd.DataFrame({
            "컬럼": PRODUCTION_COLUMNS,
            "규칙": ["필수, 중복 불가", "YYYY-MM-DD", "필수", "필수", "0 이상의 정수"],
        }))
    with col2:
        st.subheader("defects.csv (LOT×불량유형당 1행)")
        st.table(pd.DataFrame({
            "컬럼": DEFECT_COLUMNS,
            "규칙": ["production에 있는 LOT", "필수 (lot_id와 함께 중복 불가)", "0 이상의 정수"],
        }))


def show_load_report(result) -> None:
    """적재 결과 요약. 일부 행이 제외되면 지표가 그 데이터 기준임을 항상 알린다."""
    st.subheader("적재 리포트")
    if not result.ok:
        st.error("**불러오지 못했습니다.**\n\n" + result.fatal.replace("\n", "\n\n"))
    else:
        loaded_lots, loaded_defects = len(result.production), len(result.defects)
        excluded_lots = result.production_rows_read - loaded_lots
        excluded_defects = result.defects_rows_read - loaded_defects
        col1, col2, col3 = st.columns(3)
        col1.metric("생산 LOT 적재", f"{loaded_lots:,} / {result.production_rows_read:,}",
                    help="적재된 LOT 수 / 원본 데이터 행 수 (빈 줄 제외)")
        col2.metric("불량 행 적재", f"{loaded_defects:,} / {result.defects_rows_read:,}")
        col3.metric("제외된 행", f"{excluded_lots + excluded_defects:,}",
                    help="오류로 제외된 행과 중복으로 1행만 반영된 행의 합계")
        if excluded_lots or excluded_defects:
            st.warning(f"일부 행이 제외되었습니다 (생산 {excluded_lots:,}행, 불량 {excluded_defects:,}행). "
                       "아래 지표는 **적재된 데이터만으로** 계산한 값입니다.")

    if result.issues:
        has_problem = any(i["level"] in ("error", "warning") for i in result.issues)
        with st.expander(f"오류·경고 상세 ({len(result.issues):,}건)", expanded=has_problem):
            st.dataframe(issues_table(result.issues), hide_index=True)


def show_kpi(conn) -> None:
    kpi = metrics.summary_kpi(conn)
    col1, col2, col3, col4 = st.columns(4)
    col1.metric("생산량", f"{kpi['produced_qty']:,}", help="SUM(produced_qty)")
    col2.metric("불량 수량", f"{kpi['defect_qty']:,}", help="SUM(defect_qty). LOT 수가 아니라 불량품 개수입니다.")
    col3.metric("불량률", "-" if kpi["defect_rate_pct"] is None else f"{kpi['defect_rate_pct']:.2f}%",
                help="불량 수량 ÷ 생산량 × 100 (가중 평균). 생산량이 0이면 '-'로 표시합니다.")
    col4.metric("LOT 수", f"{kpi['lot_count']:,}")


def show_group(conn, group_column: str) -> None:
    df = metrics.by_group(conn, group_column)
    label = COLUMN_LABELS[group_column]
    st.bar_chart(df, x=group_column, y="defect_rate_pct", x_label=label, y_label="불량률(%)",
                 sort=False)  # SQL에서 정렬한 순서(불량률 높은 순)를 유지
    st.dataframe(to_display(df), hide_index=True)


# ---------------------------------------------------------------------------
# 화면 실행
# ---------------------------------------------------------------------------

st.set_page_config(page_title="FactoryLens", page_icon="🏭", layout="wide")
st.title("FactoryLens")
st.caption("공정별 생산량·불량률 분석 · ⚠️ 이 앱의 샘플 데이터는 포트폴리오용으로 생성한 **가상 데이터**이며 실제 공장 데이터가 아닙니다.")

sidebar()

if "result" not in st.session_state:
    show_guide()
    st.stop()

st.caption(f"데이터 출처: {st.session_state['source']}")
show_load_report(st.session_state["result"])

conn = st.session_state["conn"]
if conn is None:
    st.stop()

st.divider()
st.subheader("핵심 지표")
show_kpi(conn)

st.subheader("공정별 불량률")
show_group(conn, "process")
