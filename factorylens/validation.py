"""업로드된 CSV를 읽고 검증한다.

검증 단계(docs 및 계획서의 규칙 번호와 동일):
  F1~F4: 파일 단위 검증. F1~F3에 걸리면 FileError를 발생시켜 파일 전체를 거부한다.
  (R1·R2, D1·D2, X1·X2·Z1은 다음 단계에서 추가)

이슈(오류·경고·안내)는 아래 형태의 dict 목록으로 모은다.
  {"file": 파일 구분, "row": CSV 줄 번호 또는 None, "column": 컬럼명 또는 None,
   "level": "error" | "warning" | "info", "reason": 사유}
"""
import io
import re

import pandas as pd

PRODUCTION_COLUMNS = ["lot_id", "work_date", "process", "product", "produced_qty"]
DEFECT_COLUMNS = ["lot_id", "defect_type", "defect_qty"]

# 앞에서부터 순서대로 시도한다. utf-8-sig는 엑셀이 붙이는 BOM도 처리한다.
ENCODINGS = ["utf-8-sig", "cp949"]


class FileError(Exception):
    """파일 전체를 거부해야 하는 오류 (F1~F3)."""


def make_issue(file, row, column, level, reason):
    return {"file": file, "row": row, "column": column, "level": level, "reason": reason}


def _decode(data: bytes) -> str:
    for encoding in ENCODINGS:
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    raise FileError("파일 인코딩을 읽을 수 없습니다. UTF-8 또는 CP949(EUC-KR)로 저장된 CSV인지 확인하세요.")


def _explain_parser_error(error: Exception) -> str:
    # 예: "Error tokenizing data. C error: Expected 5 fields in line 3, saw 7"
    match = re.search(r"Expected (\d+) fields in line (\d+), saw (\d+)", str(error))
    if match:
        expected, line, saw = match.groups()
        return f"{line}번 줄의 열 개수가 {saw}개입니다 (헤더 기준 {expected}개). 값 안의 쉼표는 따옴표로 감싸야 합니다."
    return str(error)


def read_csv(data: bytes, required_columns: list[str], file_label: str) -> tuple[pd.DataFrame, list[dict]]:
    """CSV 바이트를 읽어 (필수 컬럼만 담은 DataFrame, 이슈 목록)을 반환한다.

    - 모든 값은 문자열로 읽고 앞뒤 공백을 제거한다. 결측은 빈 문자열 ""로 통일한다.
    - 반환 DataFrame의 index는 원본 CSV의 줄 번호다(헤더가 1번 줄).
    - F1~F3에 해당하면 FileError를 발생시킨다.
    """
    # F1: 빈 파일 / 인코딩 / CSV 구조
    if not data or not data.strip():
        raise FileError("빈 파일입니다.")

    text = _decode(data)
    try:
        raw = pd.read_csv(
            io.StringIO(text),
            header=None,             # 헤더도 데이터처럼 읽는다. 헤더보다 열이 많은 줄을 확실히 오류로 잡기 위해서다
                                     # (header를 쓰면 pandas가 남는 열을 조용히 index로 써 버린다)
            dtype=str,               # 타입 자동 추론을 막고 직접 변환한다 (예: "007"이 7로 바뀌는 것 방지)
            keep_default_na=False,   # "NA", "null" 같은 문자열을 결측으로 바꾸지 않는다
            skip_blank_lines=False,  # 빈 줄도 행으로 읽어야 줄 번호가 원본과 일치한다
        )
    except pd.errors.EmptyDataError:
        raise FileError("빈 파일입니다.")
    except pd.errors.ParserError as e:
        raise FileError(f"CSV 형식이 올바르지 않습니다. {_explain_parser_error(e)}")

    # 첫 줄을 헤더로 사용하고, 컬럼명은 앞뒤 공백 제거 + 소문자로 정규화
    df = raw.iloc[1:]
    df.columns = [str(c).strip().lower() for c in raw.iloc[0]]
    duplicated_cols = sorted({c for c in df.columns if list(df.columns).count(c) > 1})
    if duplicated_cols:
        raise FileError(f"컬럼명이 중복되었습니다: {', '.join(duplicated_cols)}")

    # F2: 필수 컬럼 누락
    missing = [c for c in required_columns if c not in df.columns]
    if missing:
        raise FileError(f"필수 컬럼이 없습니다: {', '.join(missing)} (필수 컬럼: {', '.join(required_columns)})")

    issues = []

    # F4: 필수 외 컬럼은 무시하고 안내
    extra = [c for c in df.columns if c not in required_columns]
    if extra:
        issues.append(make_issue(file_label, None, None, "info", f"분석에 사용하지 않는 컬럼을 무시했습니다: {', '.join(extra)}"))

    df = df[required_columns].fillna("")  # 결측을 ""로 통일 (안전장치)
    df = df.apply(lambda col: col.str.strip())
    df.index = df.index + 1  # raw의 0번 행이 헤더(1번 줄)이므로 1번 행 = CSV 2번 줄

    # 빈 줄(모든 값이 빈 행)은 데이터가 아니므로 조용히 제거
    df = df[~(df == "").all(axis=1)]

    # F3: 데이터 행 0개
    if df.empty:
        raise FileError("헤더만 있고 데이터 행이 없습니다.")

    return df, issues
