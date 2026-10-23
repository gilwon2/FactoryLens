"""Streamlit 화면 스모크 테스트 (브라우저 없이 AppTest로 실행).

화면 배치까지 검사하지는 않고, 주요 흐름이 예외 없이 끝나고 핵심 값이 보이는지만 확인한다.
"""
from datetime import date
from pathlib import Path

import pytest
from streamlit import config
from streamlit.testing.v1 import AppTest

from factorylens.db import connect, create_db
from factorylens.ingest import ingest
from factorylens.validation import validate_upload

APP = str(Path(__file__).resolve().parent.parent / "app.py")


def metric_values(at):
    return {m.label: m.value for m in at.metric}


def test_first_screen_shows_guide():
    at = AppTest.from_file(APP).run()
    assert not at.exception
    assert "샘플 데이터로 체험" in [b.label for b in at.button]
    assert len(at.metric) == 0


def test_sample_data_flow():
    at = AppTest.from_file(APP).run()
    [sample_button] = [b for b in at.button if b.label == "샘플 데이터로 체험"]
    sample_button.click().run()
    assert not at.exception
    values = metric_values(at)
    assert values["생산 LOT 적재"] == "534 / 534"
    assert values["생산량"] == "288,347"
    assert values["불량률"] == "2.23%"
    assert len(at.warning) == 0  # 샘플은 제외 행이 없다


def test_partial_load_shows_warning_and_issue_table():
    at = AppTest.from_file(APP)
    at.session_state["result"] = result = validate_upload(
        b"lot_id,work_date,process,product,produced_qty\nL1,2026-09-01,PRESS,A,100\nL2,2026-09-01,PRESS,A,abc\n",
        b"lot_id,defect_type,defect_qty\nL1,SCRATCH,5\n",
    )
    conn = create_db()
    at.session_state["report"] = ingest(conn, result, "test")
    at.session_state["conn"] = conn
    at.session_state["source"] = "test"
    at.run()
    assert not at.exception
    assert metric_values(at)["생산 LOT 적재"] == "1 / 2"
    assert metric_values(at)["불량률"] == "5.00%"
    assert "일부 행이 제외되었습니다" in at.warning[0].value


def set_loaded(at, production: bytes, defects: bytes | None):
    """파일 업로드 대신 검증·적재 결과를 session_state에 직접 넣는다 (AppTest는 업로드를 흉내 낼 수 없다)."""
    result = validate_upload(production, defects)
    conn = create_db()
    at.session_state["report"] = ingest(conn, result, "test")
    at.session_state["result"] = result
    at.session_state["conn"] = conn
    at.session_state["source"] = "test"


def test_issue_table_shows_blank_instead_of_none():
    at = AppTest.from_file(APP)
    set_loaded(at, b"lot_id,work_date,process,product,produced_qty\nL1,2026-09-01,PRESS,A,100\n", None)
    at.run()
    issues = at.dataframe[0].value             # 오류·경고 상세 ("불량 파일이 없어…" 경고: 줄 번호·컬럼 없음)
    assert issues[["줄 번호", "컬럼"]].values.tolist() == [["", ""]]


@pytest.mark.parametrize("production, defects, message", [
    (b"lot_id,work_date,process,product,produced_qty\nL1,2026-09-01,PRESS,A,0\n", None, "생산량이 모두 0"),
    (b"lot_id,work_date,process,product,produced_qty\nL1,2026-09-01,PRESS,A,100\n", None, "불량이 없습니다"),
])
def test_rate_charts_replaced_by_message(production, defects, message):
    at = AppTest.from_file(APP)
    set_loaded(at, production, defects)
    at.run()
    assert not at.exception
    # 공정별·제품별·일자별 탭은 차트 대신 안내 문구 (불량이 없으면 불량 유형 탭의 안내도 같은 말을 포함한다)
    assert sum(message in i.value for i in at.info) >= 3
    if message == "생산량이 모두 0":
        assert len(at.get("arrow_vega_lite_chart")) == 0   # 'null' 축 차트가 하나도 그려지지 않는다


def test_fatal_result_shows_error_and_no_kpi():
    at = AppTest.from_file(APP)
    at.session_state["result"] = validate_upload(b"", None)
    at.session_state["conn"] = None
    at.session_state["source"] = "test"
    at.run()
    assert not at.exception
    assert "빈 파일" in at.error[0].value
    assert "생산량" not in metric_values(at)


# ---------- S8: 필터와 탭 ----------

def load_sample():
    at = AppTest.from_file(APP).run()
    [sample_button] = [b for b in at.button if b.label == "샘플 데이터로 체험"]
    sample_button.click().run()
    return at


def test_tabs_and_downloads_render():
    at = load_sample()
    assert not at.exception
    assert [t.label for t in at.tabs] == ["공정별", "제품별", "불량 유형(파레토)", "일자별 추이", "관리도(p-chart)"]
    assert len(at.dataframe) >= 5  # 탭마다 표 1개 (+ 접힌 상세 표가 있을 수 있음)


def test_pareto_and_p_chart_summaries():
    at = load_sample()
    # 샘플 데이터: 상위 7개 유형이 누적 80%를 처음 넘는다 (BUBBLE ... LOOSE_BOLT = 80.17%)
    assert any("상위 **7개 유형**" in m.value for m in at.markdown)
    # 관리도는 기본으로 첫 번째 공정(ASSY). 샘플 데이터는 관리 한계를 벗어난 날이 없다
    assert at.selectbox(key="pchart_process_1").value == "ASSY"
    assert "관리 한계를 벗어난 날이 없습니다" in at.success[0].value
    at.selectbox(key="pchart_process_1").set_value("PAINT").run()
    assert not at.exception
    assert metric_values(at)["중심선 (평균 불량률)"] == "3.68%"


# ---------- 누적 모드 (FACTORYLENS_DATABASE_URL 설정) ----------

@pytest.fixture
def persistent_url(monkeypatch, tmp_path):
    url = f"sqlite:///{tmp_path / 'factorylens.db'}"
    monkeypatch.setenv("FACTORYLENS_DATABASE_URL", url)
    return url


def click(at, label):
    [button] = [b for b in at.button if b.label == label]
    button.click().run()


def test_persistent_mode_empty_db_shows_guide(persistent_url):
    at = AppTest.from_file(APP).run()
    assert not at.exception
    assert "누적 DB가 비어 있습니다" in at.info[0].value


def test_persistent_mode_shows_existing_data_without_upload(persistent_url):
    sample = validate_upload((Path(APP).parent / "data/sample/production_sample.csv").read_bytes(),
                             (Path(APP).parent / "data/sample/defects_sample.csv").read_bytes())
    bad_row = validate_upload(b"lot_id,work_date,process,product,produced_qty\nN1,2026-10-01,PRESS,P-A100,abc\n"
                              b"N2,2026-10-01,PRESS,P-A100,10\n", None)
    db = connect(persistent_url)
    ingest(db, sample, "명령줄 적재")      # 다른 경로(CLI)로 미리 적재된 데이터
    ingest(db, bad_row, "오류 있는 파일")
    db.close()
    at = AppTest.from_file(APP).run()
    assert not at.exception
    assert metric_values(at)["생산량"] == "288,357"
    assert at.tabs[-1].label == "적재 이력"
    # 배치 상세는 최근 배치(#2)가 기본: 제외된 행과 사유가 보인다
    detail = at.dataframe[-1].value
    assert "0 이상의 정수가 아닙니다" in " ".join(detail["사유"])
    assert detail["줄 번호"].tolist()[0] == "2"


def test_persistent_mode_db_unreachable_shows_message(monkeypatch):
    monkeypatch.setenv("FACTORYLENS_DATABASE_URL", "postgresql://user:secret@127.0.0.1:1/none")
    at = AppTest.from_file(APP).run()
    assert not at.exception
    assert "누적 DB에 연결할 수 없습니다" in at.error[0].value
    assert "secret" not in at.error[0].value       # 접속 정보(비밀번호)를 화면에 노출하지 않는다


def test_p_chart_out_of_control_day_is_reported():
    lines = ["lot_id,work_date,process,product,produced_qty"]
    defects = ["lot_id,defect_type,defect_qty"]
    for day in range(1, 11):   # test_metrics.py의 p 관리도 손계산 예시와 같은 데이터 (09-05만 이탈)
        lines.append(f"L{day},2026-09-{day:02d},PAINT,A,1000")
        defects.append(f"L{day},BUBBLE,{50 if day == 5 else 20}")
    result = validate_upload("\n".join(lines).encode(), "\n".join(defects).encode())
    conn = create_db()
    at = AppTest.from_file(APP)
    at.session_state["report"] = ingest(conn, result, "test")
    at.session_state["result"] = result
    at.session_state["conn"] = conn
    at.session_state["source"] = "test"
    at.run()
    assert not at.exception
    assert metric_values(at)["관리 한계 이탈"] == "1일"
    assert "관리 한계를 벗어난 날이 1일 있습니다" in at.error[0].value


def test_persistent_mode_reupload_is_idempotent(persistent_url):
    at = AppTest.from_file(APP).run()
    click(at, "샘플 데이터로 체험")
    assert metric_values(at)["생산 LOT 적재"] == "534 / 534"
    click(at, "샘플 데이터로 체험")          # 같은 파일을 한 번 더
    assert not at.exception
    values = metric_values(at)
    assert values["생산 LOT 적재"] == "0 / 534"
    assert values["재업로드로 건너뜀"] == "534 LOT"
    assert values["제외된 행"] == "0"
    assert values["생산량"] == "288,347"   # 두 번 올려도 두 배가 되지 않는다
    assert len(at.warning) == 0
    history = at.dataframe[-2].value       # 적재 이력 표 (마지막은 배치 상세 표)
    assert history["신규 LOT"].tolist() == ["0", "534"]


# ---------- 배포 설정: 오류 상세 노출 제한 ----------
# 아래 테스트는 설정값과 Streamlit 서버가 만드는 예외 메시지만 확인한다.
# 실제 브라우저 표시와 배포 플랫폼의 설정 덮어쓰기는 수동 점검(docs/test_report.md)으로 확인한다.

@pytest.fixture
def project_config(monkeypatch):
    """Streamlit은 실행 위치(cwd)의 .streamlit/config.toml을 읽으므로, 프로젝트 루트에서 설정을 다시 읽는다."""
    monkeypatch.chdir(Path(APP).parent)
    config.get_config_options(force_reparse=True)


def test_config_hides_error_details_and_keeps_upload_limit(project_config):
    assert config.get_option("client.showErrorDetails") == "none"
    assert config.get_option("server.maxUploadSize") == 10


def test_uncaught_exception_details_are_redacted(project_config):
    at = AppTest.from_string("raise ValueError('C:/secret/path')").run()
    [error] = at.exception
    assert "C:/secret/path" not in error.message   # 원래 메시지 대신 일반 문구
    assert error.stack_trace == []                  # 트레이스 없음
    assert error.proto.type == ""                   # 예외 종류(ValueError)도 숨김
