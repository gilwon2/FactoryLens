"""Streamlit 화면 스모크 테스트 (브라우저 없이 AppTest로 실행).

화면 배치까지 검사하지는 않고, 주요 흐름이 예외 없이 끝나고 핵심 값이 보이는지만 확인한다.
"""
from pathlib import Path

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
