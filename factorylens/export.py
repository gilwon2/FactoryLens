"""분석 결과를 엑셀에서 바로 열 수 있는 CSV로 만든다.

- utf-8-sig: 파일 앞에 BOM을 붙여 엑셀이 한글을 UTF-8로 인식하게 한다(없으면 한글이 깨진다).
- 수식 주입 방지: '=', '+', '-', '@' 등으로 시작하는 문자열은 엑셀이 수식으로 실행할 수 있으므로
  앞에 작은따옴표(')를 붙여 글자로 취급되게 한다. 업로드된 공정·제품명이 그대로 결과에 들어가기 때문이다.
"""
import pandas as pd

FORMULA_PREFIXES = ("=", "+", "-", "@", "\t", "\r")


def sanitize_cell(value):
    if isinstance(value, str) and value.startswith(FORMULA_PREFIXES):
        return "'" + value
    return value


def to_csv_bytes(df: pd.DataFrame) -> bytes:
    safe = df.copy()
    for column in safe.columns:
        if safe[column].dtype == object:  # 문자열 컬럼만. 숫자 컬럼의 음수는 건드리지 않는다
            safe[column] = safe[column].map(sanitize_cell)
    return safe.to_csv(index=False).encode("utf-8-sig")
