"""적재 파이프라인(ingest) 테스트: 배치 기록, 재업로드 멱등성(K1), 기존 LOT와의 충돌(K2), 명령줄 적재.

모든 테스트는 SQLite와 PostgreSQL(설정된 경우)에서 각각 실행된다 (conftest.py의 make_db).
"""
from pathlib import Path

import pytest

from factorylens import metrics
from factorylens.ingest import IngestError, ingest, main
from factorylens.validation import validate_upload

SAMPLE_DIR = Path(__file__).resolve().parent.parent / "data" / "sample"
HEADER_P = "lot_id,work_date,process,product,produced_qty\n"
HEADER_D = "lot_id,defect_type,defect_qty\n"


def upload(production: str, defects: str | None = None):
    return validate_upload((HEADER_P + production).encode(), None if defects is None else (HEADER_D + defects).encode())


def count(db, table):
    return db.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]


FIRST = upload("L1,2026-09-01,PRESS,A,100\nL2,2026-09-01,PRESS,B,300\n", "L1,SCRATCH,3\nL1,DENT,2\nL2,SCRATCH,3\n")


def test_first_ingest_records_batch(make_db):
    db = make_db()
    report = ingest(db, FIRST, "day1.csv")
    assert (report.status, report.lots_inserted, report.defect_rows_inserted) == ("loaded", 2, 3)
    [batch] = metrics.batch_history(db).to_dict("records")
    assert batch["batch_id"] == report.batch_id
    assert (batch["source"], batch["status"], batch["lots_inserted"], batch["error_count"]) == ("day1.csv", "loaded", 2, 0)
    assert db.execute("SELECT DISTINCT batch_id FROM production").fetchall() == [(report.batch_id,)]


def test_batches_accumulate(make_db):
    db = make_db()
    ingest(db, FIRST, "day1.csv")
    report = ingest(db, upload("L3,2026-09-02,WELD,A,200\n", "L3,CRACK,10\n"), "day2.csv")
    assert report.lots_inserted == 1
    assert metrics.summary_kpi(db) == {"lot_count": 3, "produced_qty": 600, "defect_qty": 18, "defect_rate_pct": 3.0}
    assert metrics.batch_history(db)["source"].tolist() == ["day2.csv", "day1.csv"]   # 최근 배치부터


def test_reupload_same_file_is_idempotent(make_db):
    # K1: 같은 파일을 두 번 올려도 합계가 두 배가 되지 않는다
    db = make_db()
    ingest(db, FIRST, "day1.csv")
    again = ingest(db, FIRST, "day1.csv")
    assert (again.lots_inserted, again.lots_skipped, again.lots_conflicted) == (0, 2, 0)
    assert (again.defect_rows_inserted, again.defect_rows_skipped) == (0, 3)
    assert metrics.summary_kpi(db)["produced_qty"] == 400
    [info] = [i for i in again.issues if i["level"] == "info"]
    assert "2개는 내용이 같아 건너뛰었습니다" in info["reason"]


def test_overlapping_upload_inserts_only_new_lots(make_db):
    db = make_db()
    ingest(db, FIRST, "day1.csv")
    report = ingest(db, upload("L2,2026-09-01,PRESS,B,300\nL3,2026-09-02,WELD,A,200\n", "L2,SCRATCH,3\n"), "week.csv")
    assert (report.lots_inserted, report.lots_skipped) == (1, 1)
    assert count(db, "production") == 3


@pytest.mark.parametrize("production, defects, what", [
    ("L1,2026-09-01,PRESS,A,999\n", "L1,SCRATCH,3\nL1,DENT,2\n", "생산량이 다름"),
    ("L1,2026-09-02,PRESS,A,100\n", "L1,SCRATCH,3\nL1,DENT,2\n", "생산일이 다름"),
    ("L1,2026-09-01,PRESS,A,100\n", "L1,SCRATCH,4\nL1,DENT,2\n", "불량 수량이 다름"),
    ("L1,2026-09-01,PRESS,A,100\n", "L1,SCRATCH,3\n", "불량 유형 하나가 빠짐"),
    ("L1,2026-09-01,PRESS,A,100\n", "L1,SCRATCH,3\nL1,DENT,2\nL1,BURR,1\n", "불량 유형이 추가됨"),
])
def test_conflicting_lot_excluded_and_existing_kept(make_db, production, defects, what):
    # K2: 같은 LOT인데 내용이 다르면 새 행을 모두 제외하고 기존 데이터는 그대로 둔다
    db = make_db()
    first = ingest(db, FIRST, "day1.csv")
    report = ingest(db, upload(production + "L9,2026-09-03,ASSY,A,50\n", defects), "fix.csv")
    assert (report.lots_inserted, report.lots_conflicted) == (1, 1), what
    [error] = [i for i in report.issues if i["level"] == "error"]
    assert (error["row"], error["column"]) == (2, "lot_id")
    assert f"배치 #{first.batch_id}" in error["reason"]
    assert metrics.summary_kpi(db, processes=["PRESS"]) == {
        "lot_count": 2, "produced_qty": 400, "defect_qty": 8, "defect_rate_pct": 2.0}


def test_issues_are_stored_per_batch(make_db):
    db = make_db()
    report = ingest(db, upload("L1,2026-09-01,PRESS,A,100\nL2,2026-13-01,PRESS,A,100\n"), "bad_date.csv")
    stored = metrics.batch_issues(db, report.batch_id).to_dict("records")
    levels = sorted(i["level"] for i in stored)
    assert levels == ["error", "warning"]                       # 잘못된 날짜(오류) + 불량 파일 없음(경고)
    [error] = [i for i in stored if i["level"] == "error"]
    assert (error["file"], error["row_no"], error["column_name"]) == ("production", 3, "work_date")
    history = metrics.batch_history(db).iloc[0]
    assert (history["error_count"], history["warning_count"]) == (1, 1)


def test_rejected_upload_recorded_without_data(make_db):
    db = make_db()
    report = ingest(db, validate_upload(b"", None), "empty.csv")
    assert report.status == "rejected"
    assert count(db, "production") == 0
    [batch] = metrics.batch_history(db).to_dict("records")
    assert (batch["status"], batch["lots_inserted"]) == ("rejected", 0)
    reasons = metrics.batch_issues(db, report.batch_id)["reason"].tolist()
    assert any("빈 파일" in r for r in reasons)


def test_db_error_rolls_back_whole_batch(make_db, monkeypatch):
    # 적재 도중 DB 오류가 나면 배치 기록까지 모두 취소된다 (일부만 남지 않는다)
    db = make_db()
    import factorylens.ingest as ingest_module

    def broken_load(db, production, defects, batch_id):
        db.execute("INSERT INTO production (lot_id, work_date, process, product, produced_qty) VALUES (?, ?, ?, ?, ?)",
                   ["L1", "2026-09-01", "PRESS", "A", -1])          # CHECK 제약 위반

    monkeypatch.setattr(ingest_module, "load_data", broken_load)
    with pytest.raises(IngestError) as error:
        ingest(db, FIRST, "day1.csv")
    assert "produced_qty" not in str(error.value)      # 화면용 문구에는 DB 원문(컬럼 이름 등)이 없다
    assert "produced_qty" in error.value.detail.lower() or "check" in error.value.detail.lower()
    assert count(db, "ingest_batch") == 0
    assert count(db, "production") == 0


def test_large_upload_spans_query_chunks(make_db):
    # 이미 적재된 LOT 조회를 500개씩 나눠 하므로, 그보다 많은 LOT도 정확히 분류되어야 한다
    rows = "".join(f"L{i:04d},2026-09-01,PRESS,A,10\n" for i in range(1200))
    db = make_db()
    ingest(db, upload(rows), "big.csv")
    again = ingest(db, upload(rows + "L9999,2026-09-02,PRESS,A,10\n"), "big2.csv")
    assert (again.lots_inserted, again.lots_skipped) == (1, 1200)


def test_sample_data_ingest(make_db):
    db = make_db()
    result = validate_upload((SAMPLE_DIR / "production_sample.csv").read_bytes(),
                             (SAMPLE_DIR / "defects_sample.csv").read_bytes())
    report = ingest(db, result, "sample")
    assert (report.lots_inserted, report.defect_rows_inserted) == (534, len(result.defects))
    assert metrics.summary_kpi(db)["defect_rate_pct"] == 2.23


# ---------- 명령줄 적재 ----------

def test_cli_ingest_twice(tmp_path, capsys):
    url = f"sqlite:///{tmp_path / 'cli.db'}"
    args = [str(SAMPLE_DIR / "production_sample.csv"), str(SAMPLE_DIR / "defects_sample.csv"), "--db", url]
    assert main(args) == 0
    assert "LOT 신규 534" in capsys.readouterr().out
    assert main(args) == 0
    assert "건너뜀 534" in capsys.readouterr().out


def test_cli_reports_errors_and_exit_code(tmp_path, capsys):
    url = f"sqlite:///{tmp_path / 'cli.db'}"
    bad = tmp_path / "production.csv"
    bad.write_text(HEADER_P + "".join(f"L{i},2026-09-01,PRESS,A,-1\n" for i in range(25)) + "OK,2026-09-01,PRESS,A,5\n")
    assert main([str(bad), "--db", url, "--source", "daily-batch"]) == 0   # 일부 행 오류여도 정상 행은 적재
    out = capsys.readouterr().out
    assert "[error] production 2번 줄" in out and "외 6건" in out          # 26건(오류 25 + 경고 1) 중 20건만 출력

    empty = tmp_path / "empty.csv"
    empty.write_text("")
    assert main([str(empty), "--db", url]) == 1                           # 파일 거부 -> 종료 코드 1


def test_cli_requires_persistent_db(monkeypatch, capsys):
    monkeypatch.delenv("FACTORYLENS_DATABASE_URL", raising=False)
    assert main([str(SAMPLE_DIR / "production_sample.csv")]) == 2
    assert "누적 저장할 DB가 필요합니다" in capsys.readouterr().err
    assert main([str(SAMPLE_DIR / "production_sample.csv"), "--db", "mysql://x"]) == 2
