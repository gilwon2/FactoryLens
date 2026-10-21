"""data/invalid_examples/의 시나리오가 README에 적힌 기대 결과대로 처리되는지 확인한다.

문서와 실제 동작이 어긋나지 않게 하는 테스트다. 예시 파일을 바꾸면 이 표도 함께 바꿔야 한다.
"""
from pathlib import Path

import pytest

from factorylens.validation import validate_upload

EXAMPLES = Path(__file__).resolve().parent.parent / "data" / "invalid_examples"


def run(name):
    folder = EXAMPLES / name
    return validate_upload((folder / "production.csv").read_bytes(), (folder / "defects.csv").read_bytes())


@pytest.mark.parametrize("name, message", [
    ("01_empty_file", "빈 파일"),
    ("02_missing_column", "필수 컬럼이 없습니다: produced_qty"),
    ("03_header_only", "데이터 행이 없습니다"),
    ("04_wrong_column_count", "5번 줄의 열 개수가 6개"),
])
def test_file_level_errors_are_rejected(name, message):
    result = run(name)
    assert not result.ok
    assert message in result.fatal


@pytest.mark.parametrize("name, production, defects, levels", [
    # 이름, (원본 -> 적재) 생산 행, (원본 -> 적재) 불량 행, 이슈 구분별 건수
    ("05_bad_values", (8, 3), (3, 3), {"error": 5}),
    ("06_duplicates", (5, 2), (4, 2), {"warning": 2, "error": 3}),
    ("07_cross_check", (4, 3), (6, 3), {"error": 4}),
    ("08_cp949_korean", (1, 1), (1, 1), {}),
    ("09_zero_production", (2, 2), (0, 0), {}),
])
def test_row_level_scenarios(name, production, defects, levels):
    result = run(name)
    assert result.ok
    assert (result.production_rows_read, len(result.production)) == production
    assert (result.defects_rows_read, len(result.defects)) == defects
    counts = {}
    for issue in result.issues:
        counts[issue["level"]] = counts.get(issue["level"], 0) + 1
    assert counts == levels


def test_cp949_korean_values_are_kept():
    result = run("08_cp949_korean")
    assert result.production.iloc[0]["process"] == "프레스"
    assert result.defects.iloc[0]["defect_type"] == "긁힘"
