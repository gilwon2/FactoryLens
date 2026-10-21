import pandas as pd
import pytest

from factorylens.export import sanitize_cell, to_csv_bytes


@pytest.mark.parametrize("value, expected", [
    ("=1+1", "'=1+1"),
    ("+A1", "'+A1"),
    ("-cmd", "'-cmd"),
    ("@SUM(A1)", "'@SUM(A1)"),
    ("PRESS", "PRESS"),
    ("P-A100", "P-A100"),  # 중간의 '-'는 문제 없음
    (-3, -3),              # 숫자는 그대로
])
def test_sanitize_cell(value, expected):
    assert sanitize_cell(value) == expected


def test_csv_has_bom_and_korean_header():
    data = to_csv_bytes(pd.DataFrame({"공정": ["PRESS"], "불량률(%)": [2.5]}))
    assert data.startswith(b"\xef\xbb\xbf")  # UTF-8 BOM
    assert data.decode("utf-8-sig").splitlines() == ["공정,불량률(%)", "PRESS,2.5"]


def test_csv_sanitizes_text_but_keeps_numbers_and_blank_rate():
    df = pd.DataFrame({"공정": ["=HYPERLINK()", "WELD"], "증감": [-1, 2], "불량률(%)": [None, 5.0]})
    assert to_csv_bytes(df).decode("utf-8-sig").splitlines() == [
        "공정,증감,불량률(%)",
        "'=HYPERLINK(),-1,",   # 문자열만 처리, 숫자 -1은 그대로, 계산할 수 없는 불량률은 빈 칸
        "WELD,2,5.0",
    ]


def test_original_dataframe_is_not_modified():
    df = pd.DataFrame({"공정": ["=X"]})
    to_csv_bytes(df)
    assert df["공정"].tolist() == ["=X"]
