"""FactoryLens Streamlit 앱.

Streamlit은 버튼·위젯을 조작할 때마다 이 파일 전체를 위에서부터 다시 실행한다.
그래서 다시 실행되어도 유지해야 하는 값(DB 연결, 검증 결과)은 st.session_state에 저장한다.

저장 모드 (factorylens/db.py 참고)
- 데모 모드: 환경 변수 FACTORYLENS_DATABASE_URL이 없을 때. 업로드마다 새 인메모리 DB를 만든다.
- 누적 모드: 환경 변수가 있을 때. 업로드가 그 DB에 쌓이고, 화면은 누적된 전체 데이터를 보여 준다.
"""
import logging
import os
from datetime import date
from pathlib import Path

import altair as alt
import pandas as pd
import streamlit as st

from factorylens import metrics
from factorylens.db import DATABASE_URL_ENV, connect_from_env, create_db
from factorylens.export import to_csv_bytes
from factorylens.ingest import IngestError, ingest
from factorylens.validation import DEFECT_COLUMNS, PRODUCTION_COLUMNS, validate_upload

SAMPLE_DIR = Path(__file__).resolve().parent / "data" / "sample"
logger = logging.getLogger("factorylens")

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
    "cum_share_pct": "누적 비중(%)",
    "p_pct": "불량률(%)",
    "cl_pct": "중심선(%)",
    "ucl_pct": "관리 상한(%)",
    "lcl_pct": "관리 하한(%)",
    "out_of_control": "관리 이탈",
    "batch_id": "배치",
    "ingested_at": "적재 시각",
    "source": "출처",
    "status": "상태",
    "production_rows_read": "생산 행",
    "lots_inserted": "신규 LOT",
    "lots_skipped": "건너뜀(재업로드)",
    "lots_conflicted": "충돌 제외",
    "defect_rows_inserted": "신규 불량 행",
    "error_count": "오류",
    "warning_count": "경고",
}
RATE_COLUMNS = ("defect_rate_pct", "share_pct", "cum_share_pct", "p_pct", "cl_pct", "ucl_pct", "lcl_pct")
COUNT_COLUMNS = ("lot_count", "produced_qty", "defect_qty", "production_rows_read", "lots_inserted",
                 "lots_skipped", "lots_conflicted", "defect_rows_inserted", "error_count", "warning_count")
STATUS_LABELS = {"loaded": "적재", "rejected": "거부"}
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
    for column in COUNT_COLUMNS:
        if column in shown:
            shown[column] = shown[column].map(lambda n: f"{int(n):,}")
    for column in RATE_COLUMNS:
        if column in shown:
            shown[column] = shown[column].map(format_rate)
    if "out_of_control" in shown:
        shown["out_of_control"] = shown["out_of_control"].map(lambda v: "이탈" if v else "")
    if "status" in shown:
        shown["status"] = shown["status"].map(STATUS_LABELS)
    return shown.rename(columns=COLUMN_LABELS)


def issues_table(issues: list[dict]) -> pd.DataFrame:
    table = pd.DataFrame(issues, columns=["level", "file", "row", "column", "reason"])
    table["file"] = table["file"].fillna("-")
    table["order"] = table["level"].map(LEVEL_ORDER)
    table = table.sort_values(["order", "file", "row"], na_position="first").drop(columns="order")
    # 줄 번호·컬럼이 없는 이슈(파일 단위 안내, 완전중복 경고)는 None 대신 빈칸으로 표시한다
    table["row"] = table["row"].map(lambda n: "" if pd.isna(n) else str(int(n)))  # 2.0 -> "2"
    table["column"] = table["column"].fillna("")
    table["level"] = table["level"].map(LEVEL_LABELS)
    return table.rename(columns={"level": "구분", "file": "파일", "row": "줄 번호", "column": "컬럼", "reason": "사유"})


def download_button(df: pd.DataFrame, name: str, filters: dict) -> None:
    """화면의 표와 같은 내용을 CSV로 내려받는다. 숫자는 서식 없이 원래 값으로 저장한다."""
    period = f"{filters['start_date']}_{filters['end_date']}".replace("-", "")
    st.download_button(
        "CSV 다운로드",
        to_csv_bytes(df.rename(columns=COLUMN_LABELS)),
        file_name=f"factorylens_{name}_{period}.csv",
        mime="text/csv",
        on_click="ignore",  # 다운로드 때문에 화면이 다시 실행되지 않게 한다
        key=f"download_{name}",
    )


# ---------------------------------------------------------------------------
# 업로드 -> 검증 -> 적재
# ---------------------------------------------------------------------------

def persistent_mode() -> bool:
    return bool(os.environ.get(DATABASE_URL_ENV))


def get_db():
    """현재 세션이 볼 DB. 데모 모드에서 아직 아무것도 불러오지 않았으면 None."""
    if persistent_mode() and st.session_state.get("conn") is None:
        try:
            st.session_state["conn"] = connect_from_env()
        except Exception:  # 주소·비밀번호가 화면에 노출되지 않도록 상세 사유는 표시하지 않는다
            st.error("누적 DB에 연결할 수 없습니다. DB가 실행 중인지, "
                     f"환경 변수 {DATABASE_URL_ENV}의 주소가 맞는지 확인하세요.")
            st.stop()
    return st.session_state.get("conn")


def load(production_data: bytes, defects_data: bytes | None, source: str) -> None:
    """검증하고 통과한 데이터를 적재한 뒤 session_state를 교체한다.

    데모 모드: 새 인메모리 DB에 적재하고 이전 DB를 버린다. 파일이 거부되면 DB를 만들지 않는다.
    누적 모드: 같은 DB에 쌓는다. 거부된 업로드도 적재 이력(배치)에 남긴다.
    """
    result = validate_upload(production_data, defects_data)
    report = None

    if persistent_mode():
        try:
            report = ingest(get_db(), result, source)
        except IngestError as e:  # 검증을 통과했다면 일어나지 않아야 하는 2차 방어선
            logger.error("적재 실패 (%s): %s", source, e.detail)   # 원문은 서버 로그에만
            result.fatal = str(e)                                  # 화면에는 일반 문구만
    else:
        conn = None
        if result.ok:
            conn = create_db()
            try:
                report = ingest(conn, result, source)
            except IngestError as e:
                logger.error("적재 실패 (%s): %s", source, e.detail)
                result.fatal = str(e)
                conn = None
        old_conn = st.session_state.get("conn")
        if old_conn is not None:
            old_conn.close()
        st.session_state["conn"] = conn

    st.session_state["result"] = result
    st.session_state["report"] = report
    st.session_state["source"] = source
    # 불러올 때마다 번호를 올려 필터 위젯을 새로 만든다 (이전 데이터의 선택값이 남지 않도록)
    st.session_state["load_id"] = st.session_state.get("load_id", 0) + 1


def sidebar_upload() -> None:
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


def sidebar_filters(conn) -> dict:
    """필터 위젯을 그리고 metrics 함수에 넘길 필터 dict를 반환한다."""
    load_id = st.session_state.get("load_id", 0)
    first, last = (date.fromisoformat(d) for d in metrics.date_range(conn))
    processes = metrics.distinct_values(conn, "process")
    products = metrics.distinct_values(conn, "product")

    with st.sidebar:
        st.divider()
        st.header("분석 필터")
        picked = st.date_input("생산일 범위", value=(first, last), min_value=first, max_value=last,
                               key=f"dates_{load_id}")
        selected_processes = st.multiselect("공정", processes, default=processes, key=f"process_{load_id}")
        selected_products = st.multiselect("제품", products, default=products, key=f"product_{load_id}")

    # 달력에서 시작일만 고른 상태라면 값이 1개다 -> 그날 하루로 본다
    start = picked[0] if len(picked) > 0 else first
    end = picked[1] if len(picked) > 1 else start
    return {
        "start_date": start.isoformat(),
        "end_date": end.isoformat(),
        "processes": selected_processes,
        "products": selected_products,
    }


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


def show_load_report(result, report, persistent: bool) -> None:
    """적재 결과 요약. 일부 행이 제외되면 지표가 그 데이터 기준임을 항상 알린다."""
    st.subheader("적재 리포트")
    if not result.ok:
        st.error("**불러오지 못했습니다.**\n\n" + result.fatal.replace("\n", "\n\n"))
        if persistent and report is not None:
            st.caption(f"이 업로드는 적재 이력에 배치 #{report.batch_id}(거부)로 기록했습니다.")
    else:
        # 제외 = 원본 행 - 적재 - 재업로드로 건너뜀. 오류·완전중복·기존 LOT와의 충돌로 빠진 행이 여기에 들어간다
        excluded_lots = result.production_rows_read - report.lots_inserted - report.lots_skipped
        excluded_defects = result.defects_rows_read - report.defect_rows_inserted - report.defect_rows_skipped
        columns = st.columns(4 if persistent else 3)
        columns[0].metric("생산 LOT 적재", f"{report.lots_inserted:,} / {result.production_rows_read:,}",
                          help="새로 적재된 LOT 수 / 원본 데이터 행 수 (빈 줄 제외)")
        columns[1].metric("불량 행 적재", f"{report.defect_rows_inserted:,} / {result.defects_rows_read:,}")
        columns[2].metric("제외된 행", f"{excluded_lots + excluded_defects:,}",
                          help="오류로 제외된 행, 중복으로 1행만 반영된 행, 기존 LOT와 내용이 달라 제외된 행의 합계")
        if persistent:
            columns[3].metric("재업로드로 건너뜀", f"{report.lots_skipped:,} LOT",
                              help="이미 적재된 LOT와 내용이 같아 다시 넣지 않은 LOT 수")
            st.caption(f"적재 이력에 배치 #{report.batch_id}로 기록했습니다.")
        if excluded_lots or excluded_defects:
            st.warning(f"일부 행이 제외되었습니다 (생산 {excluded_lots:,}행, 불량 {excluded_defects:,}행). "
                       "아래 지표는 **적재된 데이터만으로** 계산한 값입니다.")

    issues = report.issues if (report is not None and result.ok) else result.issues
    if issues:
        has_problem = any(i["level"] in ("error", "warning") for i in issues)
        with st.expander(f"오류·경고 상세 ({len(issues):,}건)", expanded=has_problem):
            st.dataframe(issues_table(issues), hide_index=True)


def show_kpi(kpi: dict) -> None:
    col1, col2, col3, col4 = st.columns(4)
    col1.metric("생산량", f"{kpi['produced_qty']:,}", help="SUM(produced_qty)")
    col2.metric("불량 수량", f"{kpi['defect_qty']:,}", help="SUM(defect_qty). LOT 수가 아니라 불량품 개수입니다.")
    col3.metric("불량률", "-" if kpi["defect_rate_pct"] is None else f"{kpi['defect_rate_pct']:.2f}%",
                help="불량 수량 ÷ 생산량 × 100 (가중 평균). 생산량이 0이면 '-'로 표시합니다.")
    col4.metric("LOT 수", f"{kpi['lot_count']:,}")


def rate_chart_note(df: pd.DataFrame) -> str | None:
    """불량률 차트를 그릴 의미가 없을 때의 안내 문구. 그려도 되면 None.

    값이 모두 NULL이면 축에 'null', 모두 0이면 축에 '0.000000'만 표시되어 차트가 오히려 혼란스럽다.
    """
    rates = df["defect_rate_pct"]
    if rates.isna().all():
        return "생산량이 모두 0이라 불량률을 계산할 수 없어 차트를 표시하지 않습니다."
    if rates.fillna(0).eq(0).all():
        return "선택한 범위에 불량이 없습니다 (불량률 0%). 차트는 표시하지 않습니다."
    return None


def show_group(conn, group_column: str, filters: dict) -> None:
    df = metrics.by_group(conn, group_column, **filters)
    note = rate_chart_note(df)
    if note:
        st.info(note)
    else:
        st.bar_chart(df, x=group_column, y="defect_rate_pct", x_label=COLUMN_LABELS[group_column],
                     y_label="불량률(%)", sort=False)  # SQL에서 정렬한 순서(불량률 높은 순)를 유지
    st.dataframe(to_display(df), hide_index=True)
    download_button(df, group_column, filters)


def pareto_chart(df: pd.DataFrame) -> alt.LayerChart:
    """막대(유형별 불량 수량, 많은 순) + 꺾은선(누적 비중) + 80% 기준선."""
    base = alt.Chart(df).encode(x=alt.X("defect_type:N", sort=None, title="불량 유형"))
    tooltip = [alt.Tooltip("defect_type:N", title="불량 유형"), alt.Tooltip("defect_qty:Q", title="불량 수량", format=","),
               alt.Tooltip("share_pct:Q", title="비중(%)"), alt.Tooltip("cum_share_pct:Q", title="누적 비중(%)")]
    bars = base.mark_bar().encode(y=alt.Y("defect_qty:Q", title="불량 수량"), tooltip=tooltip)
    line = base.mark_line(point=True, color="#d62728").encode(
        y=alt.Y("cum_share_pct:Q", title="누적 비중(%)", scale=alt.Scale(domain=[0, 100])), tooltip=tooltip)
    rule = alt.Chart(pd.DataFrame({"y": [80]})).mark_rule(color="gray", strokeDash=[4, 4]).encode(y="y:Q")
    return alt.layer(bars, line + rule).resolve_scale(y="independent")


def show_defect_types(conn, filters: dict) -> None:
    df = metrics.by_defect_type(conn, **filters)
    if df.empty:
        st.info("선택한 조건에서 발생한 불량이 없습니다.")
        return
    top = metrics.vital_few(df)
    names = ", ".join(df["defect_type"].head(top))
    # 굵게(**) 바로 뒤에 한글 조사가 오면 마크다운이 굵게로 인식하지 못하므로 뒤에 공백·괄호를 둔다
    st.markdown(f"상위 **{top}개 유형**({names})의 누적 비중: **{df['cum_share_pct'].iloc[top - 1]:.1f}%** "
                f"(전체 {len(df)}개 유형 중). 이 유형부터 개선하는 것이 효과가 큽니다.")
    st.altair_chart(pareto_chart(df))
    st.caption("파레토 차트: 막대는 불량 수량(많은 순), 빨간 선은 누적 비중, 점선은 80% 기준입니다.")
    st.dataframe(to_display(df), hide_index=True)
    download_button(df, "defect_type", filters)


def show_trend(conn, filters: dict) -> None:
    df = metrics.by_group(conn, "work_date", **filters)
    note = rate_chart_note(df)
    if note:
        st.info(note)
    else:
        st.line_chart(df, x="work_date", y="defect_rate_pct", x_label="생산일", y_label="불량률(%)")
        st.caption("생산량이 0인 날은 불량률을 계산할 수 없어 선이 끊겨 보일 수 있습니다.")
    st.dataframe(to_display(df), hide_index=True)
    download_button(df, "daily", filters)


def p_chart_figure(df: pd.DataFrame) -> alt.LayerChart:
    """일자별 불량률 + 중심선 + 날마다 달라지는 관리 상·하한(계단선) + 이탈한 날(빨간 점)."""
    x = alt.X("work_date:T", title="생산일", axis=alt.Axis(format="%m-%d"))
    tooltip = [alt.Tooltip("work_date:T", title="생산일", format="%Y-%m-%d"),
               alt.Tooltip("produced_qty:Q", title="생산량", format=","),
               alt.Tooltip("defect_qty:Q", title="불량 수량", format=","),
               alt.Tooltip("p_pct:Q", title="불량률(%)", format=".2f"),
               alt.Tooltip("ucl_pct:Q", title="관리 상한(%)", format=".2f"),
               alt.Tooltip("lcl_pct:Q", title="관리 하한(%)", format=".2f")]
    base = alt.Chart(df).encode(x=x)
    limits = [base.mark_line(interpolate="step", color="gray", strokeDash=[4, 4]).encode(y=column)
              for column in ("ucl_pct:Q", "lcl_pct:Q")]
    center = base.mark_line(color="green").encode(y="cl_pct:Q")
    rate = base.mark_line(point=True).encode(y=alt.Y("p_pct:Q", title="불량률(%)", scale=alt.Scale(zero=False)),
                                              tooltip=tooltip)
    out = base.transform_filter(alt.datum.out_of_control).mark_point(
        color="#d62728", size=120, filled=True).encode(y="p_pct:Q", tooltip=tooltip)
    return alt.layer(*limits, center, rate, out)


def show_p_chart(conn, filters: dict) -> None:
    load_id = st.session_state.get("load_id", 0)
    process = st.selectbox("공정", filters["processes"], key=f"pchart_process_{load_id}",
                           help="공정마다 기준 불량률이 달라서, 여러 공정을 합쳐 그리면 공정 구성 변화가 이상처럼 보입니다.")
    df = metrics.p_chart(conn, process, **filters)
    if df.empty:
        st.info("선택한 조건에서 생산량이 있는 날이 없어 관리도를 그릴 수 없습니다.")
        return

    out = df[df["out_of_control"]]
    col1, col2, col3 = st.columns(3)
    col1.metric("중심선 (평균 불량률)", f"{df['cl_pct'].iloc[0]:.2f}%", help="기간 전체 불량 수량 ÷ 생산량 (가중 평균)")
    col2.metric("분석 일수", f"{len(df):,}일", help="생산량이 0인 날은 제외")
    col3.metric("관리 한계 이탈", f"{len(out):,}일")
    st.altair_chart(p_chart_figure(df))
    st.caption("p 관리도: 초록선은 중심선, 회색 점선은 관리 한계(중심선 ± 3σ)입니다. 한계는 그날 생산량에 따라 달라집니다. "
               "빨간 점은 한계를 벗어난 날입니다.")
    if out.empty:
        st.success(f"관리 한계를 벗어난 날이 없습니다. 이 기간 {process} 공정의 불량률 변동은 우연 변동 범위 안에 있습니다(3σ 기준).")
    else:
        st.error(f"관리 한계를 벗어난 날이 {len(out)}일 있습니다. 그날의 설비·자재·작업 조건 변경 이력을 확인하세요.")
        st.dataframe(to_display(out), hide_index=True)
    with st.expander("일자별 관리도 데이터"):
        st.dataframe(to_display(df), hide_index=True)
    download_button(df, f"p_chart_{process}", filters)


def show_batches(conn) -> None:
    history = metrics.batch_history(conn)
    st.caption("업로드 1회 = 배치 1건. 거부된 업로드도 기록하고, 제외된 행과 사유를 배치별로 남깁니다.")
    st.dataframe(to_display(history), hide_index=True)
    picked = st.selectbox("배치 상세", history["batch_id"].tolist(),
                          format_func=lambda b: f"#{b} · {history.set_index('batch_id').at[b, 'source']}")
    issues = metrics.batch_issues(conn, picked)
    if issues.empty:
        st.success("이 배치에는 기록된 오류·경고가 없습니다.")
    else:
        records = issues.rename(columns={"row_no": "row", "column_name": "column"}).to_dict("records")
        for issue in records:  # DB의 NULL(NaN)을 None으로 되돌린다
            issue["row"] = None if pd.isna(issue["row"]) else int(issue["row"])
        st.dataframe(issues_table(records), hide_index=True)


# ---------------------------------------------------------------------------
# 화면 실행
# ---------------------------------------------------------------------------

st.set_page_config(page_title="FactoryLens", page_icon="🏭", layout="wide")
st.title("FactoryLens")
st.caption("공정별 생산량·불량률 분석 · ⚠️ 이 앱의 샘플 데이터는 포트폴리오용으로 생성한 **가상 데이터**이며 실제 공장 데이터가 아닙니다.")

persistent = persistent_mode()
sidebar_upload()
conn = get_db()

if persistent:
    st.caption("저장 모드: **누적 DB** · 업로드한 데이터가 계속 쌓이고, 아래 지표는 누적된 전체 데이터 기준입니다.")
if "result" in st.session_state:
    st.caption(f"마지막 업로드: {st.session_state['source']}")
    show_load_report(st.session_state["result"], st.session_state.get("report"), persistent)

if conn is None or metrics.date_range(conn)[0] is None:  # 불러온 데이터가 없음
    if "result" not in st.session_state:
        if persistent:
            st.info("누적 DB가 비어 있습니다.")
        show_guide()
    st.stop()

filters = sidebar_filters(conn)

st.divider()
st.subheader("핵심 지표")
st.caption(f"기간 {filters['start_date']} ~ {filters['end_date']} · "
           f"공정 {len(filters['processes'])}개 · 제품 {len(filters['products'])}개 선택")
kpi = metrics.summary_kpi(conn, **filters)
if kpi["lot_count"] == 0:
    st.info("선택한 조건에 해당하는 데이터가 없습니다. 왼쪽 필터에서 기간·공정·제품을 다시 선택해 주세요.")
    st.stop()
show_kpi(kpi)

tab_names = ["공정별", "제품별", "불량 유형(파레토)", "일자별 추이", "관리도(p-chart)"]
if persistent:
    tab_names.append("적재 이력")
tabs = st.tabs(tab_names)
with tabs[0]:
    show_group(conn, "process", filters)
with tabs[1]:
    show_group(conn, "product", filters)
with tabs[2]:
    show_defect_types(conn, filters)
with tabs[3]:
    show_trend(conn, filters)
with tabs[4]:
    show_p_chart(conn, filters)
if persistent:
    with tabs[5]:
        show_batches(conn)
