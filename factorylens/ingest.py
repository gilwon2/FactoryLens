"""검증을 통과한 데이터를 DB에 적재하고, 적재 이력(배치)을 남긴다.

화면(app.py)의 업로드와 명령줄 적재가 모두 이 모듈의 ingest()를 쓴다.

    python -m factorylens.ingest production.csv [defects.csv] [--db URL] [--source 이름]

누적 모드에서는 이전 업로드에 이미 있는 LOT가 다시 들어올 수 있다. 그때의 규칙(K 규칙):
  K1 이미 적재된 LOT와 생산·불량 내용이 완전히 같음  -> 건너뛴다 (같은 파일을 다시 올려도 결과가 같다 = 멱등성)
  K2 같은 LOT인데 내용이 다름                        -> 그 LOT의 생산·불량 행을 모두 제외한다.
                                                     어느 쪽이 맞는지 시스템이 판단할 근거가 없기 때문이다(D2 규칙과 같은 원칙).
                                                     기존 데이터는 바꾸지 않는다.
비교 단위는 LOT 전체(생산 1행 + 불량 유형별 행)다. 불량 행만 따로 덧붙이는 업로드는 허용하지 않는다.

배치 1건 = 업로드 1회. 적재에 실패한(파일 거부) 업로드도 'rejected' 배치로 남기고,
검증 단계의 오류·경고는 ingest_issue에 저장해 나중에 어떤 데이터가 왜 빠졌는지 추적할 수 있게 한다.
배치 기록과 데이터 적재는 한 트랜잭션이다. 중간에 실패하면 아무것도 남지 않는다.
"""
import argparse
import sys
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

import pandas as pd

from factorylens.db import DATABASE_URL_ENV, Database, connect, connect_from_env, load_data
from factorylens.validation import UploadResult, make_issue, validate_upload

# IN (...)에 한 번에 넣을 값 개수. SQLite의 기본 자리표시자 한도(999 이하)를 넘지 않게 나눠 조회한다.
CHUNK = 500


class IngestError(Exception):
    """DB 적재 자체가 실패했을 때 (검증을 통과한 데이터라면 일어나지 않아야 하는 2차 방어선).

    str(e)는 화면에 보여도 되는 일반 문구다. DB가 돌려준 원문(테이블·컬럼 이름이 들어 있을 수 있음)은
    detail에만 담아 서버 로그·명령줄에만 남긴다.
    """

    def __init__(self, detail: str):
        super().__init__("DB 적재 중 오류가 발생해 이번 업로드 전체를 취소했습니다. 같은 문제가 반복되면 관리자에게 문의하세요.")
        self.detail = detail


@dataclass
class IngestReport:
    batch_id: int | None
    status: str                         # "loaded" | "rejected"
    lots_inserted: int = 0
    lots_skipped: int = 0               # K1 재업로드로 건너뛴 LOT
    lots_conflicted: int = 0            # K2 기존 LOT와 내용이 달라 제외한 LOT
    defect_rows_inserted: int = 0
    defect_rows_skipped: int = 0
    defect_rows_conflicted: int = 0
    issues: list = field(default_factory=list)   # 검증 이슈 + K 규칙 이슈 (화면에 보여 줄 전체 목록)


def _chunks(values: list, size: int = CHUNK):
    for start in range(0, len(values), size):
        yield values[start:start + size]


def _existing_lots(db: Database, lot_ids: list[str]) -> tuple[dict, dict]:
    """이미 적재된 LOT의 생산 행과 불량 행을 가져온다.

    반환: ({lot_id: (work_date, process, product, produced_qty, batch_id)}, {lot_id: {(defect_type, qty), ...}})
    """
    production, defects = {}, {}
    for chunk in _chunks(lot_ids):
        marks = ", ".join("?" for _ in chunk)
        for lot_id, work_date, process, product, qty, batch_id in db.execute(
                f"SELECT lot_id, work_date, process, product, produced_qty, batch_id FROM production WHERE lot_id IN ({marks})", chunk):
            production[lot_id] = (work_date, process, product, qty, batch_id)
        for lot_id, defect_type, qty in db.execute(
                f"SELECT lot_id, defect_type, defect_qty FROM defect WHERE lot_id IN ({marks})", chunk):
            defects.setdefault(lot_id, set()).add((defect_type, qty))
    return production, defects


def _classify(db: Database, production: pd.DataFrame, defects: pd.DataFrame):
    """업로드된 LOT를 신규 / K1 동일(건너뜀) / K2 충돌(제외)로 나눈다."""
    existing_production, existing_defects = _existing_lots(db, production["lot_id"].tolist())
    if not existing_production:
        return set(production["lot_id"]), set(), {}

    incoming_defects = {}
    for lot_id, defect_type, qty in defects[["lot_id", "defect_type", "defect_qty"]].itertuples(index=False):
        incoming_defects.setdefault(lot_id, set()).add((defect_type, int(qty)))

    new, same, conflict = set(), set(), {}
    for lot_id, work_date, process, product, qty in production[
            ["lot_id", "work_date", "process", "product", "produced_qty"]].itertuples(index=False):
        if lot_id not in existing_production:
            new.add(lot_id)
            continue
        *stored, batch_id = existing_production[lot_id]
        same_production = tuple(stored) == (work_date, process, product, int(qty))
        same_defects = existing_defects.get(lot_id, set()) == incoming_defects.get(lot_id, set())
        if same_production and same_defects:
            same.add(lot_id)
        else:
            conflict[lot_id] = batch_id
    return new, same, conflict


def _record_batch(db: Database, source: str, result: UploadResult, report: IngestReport) -> int:
    levels = [issue["level"] for issue in report.issues]
    return db.insert_returning_id(
        """INSERT INTO ingest_batch (ingested_at, source, status, production_rows_read, defect_rows_read,
                                     lots_inserted, lots_skipped, lots_conflicted, defect_rows_inserted,
                                     error_count, warning_count)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        [datetime.now().astimezone().isoformat(sep=" ", timespec="seconds"), source, report.status,
         result.production_rows_read, result.defects_rows_read,
         report.lots_inserted, report.lots_skipped, report.lots_conflicted, report.defect_rows_inserted,
         levels.count("error"), levels.count("warning")],
        "batch_id",
    )


def _record_issues(db: Database, batch_id: int, issues: list[dict]) -> None:
    db.executemany(
        "INSERT INTO ingest_issue (batch_id, level, file, row_no, column_name, reason) VALUES (?, ?, ?, ?, ?, ?)",
        [(batch_id, i["level"], i["file"], None if i["row"] is None else int(i["row"]), i["column"], i["reason"])
         for i in issues],
    )


def ingest(db: Database, result: UploadResult, source: str) -> IngestReport:
    """검증 결과를 적재하고 배치 기록을 남긴다. DB 오류는 IngestError로 바꿔 발생시킨다."""
    try:
        with db.transaction():
            if not result.ok:
                report = IngestReport(batch_id=None, status="rejected", issues=list(result.issues))
                report.issues.append(make_issue(None, None, None, "error", result.fatal.replace("\n", " / ")))
                report.batch_id = _record_batch(db, source, result, report)
                _record_issues(db, report.batch_id, report.issues)
                return report

            production, defects = result.production, result.defects
            new, same, conflict = _classify(db, production, defects)

            issues = list(result.issues)
            if same:
                issues.append(make_issue("production", None, "lot_id", "info",
                                         f"이미 적재된 LOT {len(same):,}개는 내용이 같아 건너뛰었습니다 (재업로드)"))
            for line in production.index[production["lot_id"].isin(conflict)]:
                lot_id = production.at[line, "lot_id"]
                issues.append(make_issue("production", line, "lot_id", "error",
                                         f"LOT '{lot_id}'가 이미 적재되어 있는데(배치 #{conflict[lot_id]}) 생산·불량 내용이 달라 "
                                         "이 LOT의 생산·불량 데이터를 모두 제외했습니다. 기존 데이터는 바꾸지 않았습니다"))

            defect_lot = defects["lot_id"]
            report = IngestReport(
                batch_id=None, status="loaded", issues=issues,
                lots_inserted=len(new), lots_skipped=len(same), lots_conflicted=len(conflict),
                defect_rows_inserted=int(defect_lot.isin(new).sum()),
                defect_rows_skipped=int(defect_lot.isin(same).sum()),
                defect_rows_conflicted=int(defect_lot.isin(conflict).sum()),
            )
            report.batch_id = _record_batch(db, source, result, report)
            load_data(db, production[production["lot_id"].isin(new)], defects[defect_lot.isin(new)], report.batch_id)
            _record_issues(db, report.batch_id, report.issues)
            return report
    except db.Error as e:
        raise IngestError(f"{type(e).__name__}: {e}") from e


# ---------------------------------------------------------------------------
# 명령줄 적재: 정기 작업(cron, 작업 스케줄러)으로 폴더의 파일을 누적 DB에 넣을 때 쓴다
# ---------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m factorylens.ingest",
                                     description="생산·불량 CSV를 검증해 누적 DB에 적재합니다.")
    parser.add_argument("production", type=Path, help="production.csv 경로")
    parser.add_argument("defects", type=Path, nargs="?", help="defects.csv 경로 (생략하면 불량 0)")
    parser.add_argument("--db", help=f"DB 주소. 생략하면 환경 변수 {DATABASE_URL_ENV}")
    parser.add_argument("--source", help="적재 이력에 남길 출처 이름 (기본: production 파일 이름)")
    args = parser.parse_args(argv)

    try:
        db = connect(args.db) if args.db else connect_from_env()
    except Exception as e:  # 연결 실패 사유(주소 형식, 인증, 네트워크)를 그대로 보여 준다
        print(f"DB에 연결할 수 없습니다: {e}", file=sys.stderr)
        return 2
    if not db.persistent:
        print(f"누적 저장할 DB가 필요합니다. --db 또는 환경 변수 {DATABASE_URL_ENV}를 지정하세요.", file=sys.stderr)
        return 2

    result = validate_upload(args.production.read_bytes(), args.defects.read_bytes() if args.defects else None)
    try:
        report = ingest(db, result, args.source or args.production.name)
    except IngestError as e:
        print(f"{e}\n원인: {e.detail}", file=sys.stderr)   # 명령줄은 운영자가 보므로 원인까지 출력
        return 1
    finally:
        db.close()

    print(f"배치 #{report.batch_id} [{report.status}] "
          f"LOT 신규 {report.lots_inserted:,} · 건너뜀 {report.lots_skipped:,} · 충돌 제외 {report.lots_conflicted:,} · "
          f"불량 행 신규 {report.defect_rows_inserted:,}")
    for issue in [i for i in report.issues if i["level"] != "info"][:20]:
        where = f"{issue['file']} {issue['row']}번 줄" if issue["row"] is not None else (issue["file"] or "-")
        print(f"  [{issue['level']}] {where}: {issue['reason']}")
    problems = sum(i["level"] != "info" for i in report.issues)
    if problems > 20:
        print(f"  ... 외 {problems - 20:,}건 (ingest_issue 테이블에 모두 저장됨)")
    return 0 if report.status == "loaded" else 1


if __name__ == "__main__":
    sys.exit(main())
