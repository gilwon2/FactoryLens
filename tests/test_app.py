"""Streamlit 화면 스모크 테스트 (브라우저 없이 AppTest로 실행).

화면 배치까지 검사하지는 않고, 주요 흐름이 예외 없이 끝나고 핵심 값이 보이는지만 확인한다.
"""
from datetime import date
from pathlib import Path

import pytest
from streamlit import config
from streamlit.testing.v1 import AppTest

from factorylens.db import create_db, load_data
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
    load_data(conn, result.production, result.defects)
    at.session_state["conn"] = conn
    at.session_state["source"] = "test"
    at.run()
    assert not at.exception
    assert metric_values(at)["생산 LOT 적재"] == "1 / 2"
    assert metric_values(at)["불량률"] == "5.00%"
    assert "일부 행이 제외되었습니다" in at.warning[0].value


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
    assert [t.label for t in at.tabs] == ["공정별", "제품별", "불량 유형별", "일자별 추이"]
    assert len(at.dataframe) >= 4  # 탭마다 표 1개 (+ 접힌 상세 표가 있을 수 있음)


def test_process_filter_changes_kpi():
    at = load_sample()
    at.multiselect(key="process_1").set_value(["PAINT"]).run()
    assert not at.exception
    assert metric_values(at)["불량률"] == "3.68%"   # S6에서 확인한 PAINT 불량률과 같아야 한다


def test_empty_selection_shows_message_instead_of_error():
    at = load_sample()
    at.multiselect(key="process_1").set_value([]).run()
    assert not at.exception
    assert "해당하는 데이터가 없습니다" in at.info[0].value
    assert "생산량" not in metric_values(at)


def test_single_day_date_filter():
    at = load_sample()
    at.date_input(key="dates_1").set_value((date(2026, 9, 1), date(2026, 9, 1))).run()
    assert not at.exception
    assert metric_values(at)["LOT 수"] == "18"  # 샘플에서 2026-09-01 LOT는 18개


def test_reload_resets_filters():
    at = load_sample()
    at.multiselect(key="process_1").set_value(["PAINT"]).run()
    [sample_button] = [b for b in at.button if b.label == "샘플 데이터로 체험"]
    sample_button.click().run()
    # 다시 불러오면 새 위젯(process_2)이 만들어지고 전체 선택으로 돌아간다
    assert len(at.multiselect(key="process_2").value) == 4
    assert metric_values(at)["불량률"] == "2.23%"


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
