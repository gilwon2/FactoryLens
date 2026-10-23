"""업로드된 CSV를 읽고 검증한다.

검증 단계(docs 및 계획서의 규칙 번호와 동일):
  F1~F4: 파일 단위 검증. F1~F3에 걸리면 FileError를 발생시켜 파일 전체를 거부한다.
  D1·D2: 중복 검증. 완전중복은 1행만 남기고(경고), 키 충돌은 그 키의 행을 모두 제외한다.
  R1·R2: 행 단위 검증. 결측·타입·범위 오류가 있는 행은 제외한다.
  X1·X2: 두 파일 간 교차 검증. 고아 불량 행과, 불량이 생산량보다 많은 LOT를 제외한다.
  Z1   : 검증 후 유효한 생산 데이터가 0건이면 적재하지 않는다.

앱에서는 validate_upload() 하나만 호출하면 위 단계가 모두 실행된다.

이슈(오류·경고·안내)는 아래 형태의 dict 목록으로 모은다.
  {"file": 파일 구분, "row": CSV 줄 번호 또는 None, "column": 컬럼명 또는 None,
   "level": "error" | "warning" | "info", "reason": 사유}
"""
import io
import re
from dataclasses import dataclass, field
from datetime import datetime

import pandas as pd

PRODUCTION_COLUMNS = ["lot_id", "work_date", "process", "product", "produced_qty"]
DEFECT_COLUMNS = ["lot_id", "defect_type", "defect_qty"]

# 수량 상한. 오타(0을 여러 개 더 입력)를 잡고, SQLite 정수 범위를 넘는 값을 막는다.
MAX_QTY = 1_000_000_000

# FactoryLens가 내려 주는 분석 결과 CSV의 한글 헤더. 이 파일을 원본 대신 올렸을 때 알아보기 위해 쓴다.
RESULT_FILE_HEADERS = {"공정", "제품", "생산일", "불량 유형", "LOT 수", "생산량", "불량 수량", "불량률(%)"}

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


def read_csv(data: bytes, required_columns: list[str], file_label: str,
             allow_empty: bool = False) -> tuple[pd.DataFrame, list[dict]]:
    """CSV 바이트를 읽어 (필수 컬럼만 담은 DataFrame, 이슈 목록)을 반환한다.

    - 모든 값은 문자열로 읽고 앞뒤 공백을 제거한다. 결측은 빈 문자열 ""로 통일한다.
    - 반환 DataFrame의 index는 원본 CSV의 줄 번호다(헤더가 1번 줄).
    - F1~F3에 해당하면 FileError를 발생시킨다.
    - allow_empty=True이면 헤더만 있는 파일(F3)을 거부하지 않고 빈 DataFrame을 반환한다.
      불량이 한 건도 없는 기간의 defects 파일은 헤더만 있는 것이 정상이기 때문이다.
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
        message = f"필수 컬럼이 없습니다: {', '.join(missing)} (필수 컬럼: {', '.join(required_columns)})"
        if RESULT_FILE_HEADERS & set(df.columns):
            message += " FactoryLens에서 내려받은 분석 결과 파일을 올린 것 같습니다. 원본 데이터 파일을 올려 주세요."
        raise FileError(message)

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
    if df.empty and not allow_empty:
        raise FileError("헤더만 있고 데이터 행이 없습니다.")

    return df, issues


# ---------------------------------------------------------------------------
# R2: 값 하나를 검사해 문제가 있으면 사유 문자열, 없으면 None을 반환한다.
#     빈 값은 R1(결측)에서 따로 처리하므로 여기서는 들어오지 않는다.
# ---------------------------------------------------------------------------

def check_quantity(value: str):
    if re.fullmatch(r"[0-9]+", value):
        if int(value) > MAX_QTY:
            return f"값이 너무 큽니다 (최대 {MAX_QTY:,})"
        return None
    if re.fullmatch(r"-[0-9]+(\.[0-9]+)?", value):
        return "음수는 허용되지 않습니다"
    if re.fullmatch(r"[0-9]*\.[0-9]+", value):
        return "정수가 아닙니다 (소수점 불가)"
    return "0 이상의 정수가 아닙니다"


def check_date(value: str):
    # strptime은 "2026-9-1"도 받아들이므로 자릿수를 정규식으로 먼저 고정한다
    if not re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", value):
        return "날짜 형식이 아닙니다 (YYYY-MM-DD)"
    try:
        datetime.strptime(value, "%Y-%m-%d")
    except ValueError:
        return "존재하지 않는 날짜입니다"
    return None


# ---------------------------------------------------------------------------
# D1·D2: 중복 처리
# ---------------------------------------------------------------------------

def remove_duplicates(df: pd.DataFrame, key_columns: list[str], file_label: str) -> tuple[pd.DataFrame, list[dict]]:
    """D1 완전중복은 첫 행만 남기고, D2 키 충돌은 그 키의 행을 모두 제외한다.

    키가 비어 있는 행은 중복 판단에서 빼고 그대로 둔다(뒤의 R1에서 결측으로 제외된다).
    """
    issues = []
    key_text = "+".join(key_columns)
    has_key = (df[key_columns] != "").all(axis=1)

    # D1: 모든 값이 같은 행은 같은 데이터가 두 번 들어온 것으로 보고 1행만 남긴다
    keyed = df[has_key]
    is_duplicate = keyed.duplicated(keep="first")  # 두 번째로 나온 행부터 True
    # 같은 값끼리 묶어서 각 묶음의 첫 줄 번호를 구한다 (경고 메시지에 "몇 번 줄과 같은지" 표시용)
    line_numbers = pd.Series(keyed.index, index=keyed.index)
    first_line = line_numbers.groupby([keyed[c] for c in keyed.columns]).transform("first")
    for line in keyed.index[is_duplicate]:
        issues.append(make_issue(file_label, line, None, "warning",
                                 f"{first_line[line]}번 줄과 완전히 같은 중복 행이라 1행만 반영했습니다"))
    df = df.drop(index=keyed.index[is_duplicate])

    # D2: 키는 같은데 값이 다른 행 -> 어느 값이 맞는지 알 수 없으므로 모두 제외
    keyed = df[(df[key_columns] != "").all(axis=1)]
    conflict = keyed.duplicated(subset=key_columns, keep=False)
    for _, group in keyed[conflict].groupby(key_columns):
        lines = list(group.index)
        key_value = "/".join(group.iloc[0][key_columns])
        line_text = ", ".join(str(n) for n in lines)
        for line in lines:
            issues.append(make_issue(file_label, line, key_text, "error",
                                     f"키 '{key_value}'가 여러 줄({line_text})에 서로 다른 값으로 있어 모두 제외했습니다"))
    df = df.drop(index=keyed.index[conflict])

    return df, issues


# ---------------------------------------------------------------------------
# 파일별 검증 (D1·D2 -> R1·R2 순서)
# ---------------------------------------------------------------------------

def validate_rows(df: pd.DataFrame, file_label: str, key_columns: list[str],
                  quantity_columns: list[str], date_columns: list[str]) -> tuple[pd.DataFrame, list[dict]]:
    # 중복을 타입 검사보다 먼저 본다. 같은 키의 한 줄이 타입 오류로 먼저 빠지면
    # 남은 줄이 충돌 없이 통과해 버리기 때문이다.
    df, issues = remove_duplicates(df, key_columns, file_label)

    bad_lines = set()
    for column in df.columns:
        if column in quantity_columns:
            checker = check_quantity
        elif column in date_columns:
            checker = check_date
        else:
            checker = None

        def find_problem(value, checker=checker):
            if value == "":
                return "값이 비어 있습니다"  # R1
            if checker is not None:
                return checker(value)         # R2
            return None

        reasons = df[column].map(find_problem)  # 값마다 사유 또는 None
        for line in reasons.index[reasons.notna()]:
            value, reason = df.at[line, column], reasons[line]
            bad_lines.add(line)
            issues.append(make_issue(file_label, line, column, "error", f"{reason} (입력값: '{value}')" if value else reason))

    clean = df.drop(index=sorted(bad_lines)).copy()
    for column in quantity_columns:
        clean[column] = clean[column].astype(int)
    return clean, issues


def validate_production(df: pd.DataFrame, file_label: str = "production") -> tuple[pd.DataFrame, list[dict]]:
    return validate_rows(df, file_label, key_columns=["lot_id"],
                         quantity_columns=["produced_qty"], date_columns=["work_date"])


def validate_defects(df: pd.DataFrame, file_label: str = "defects") -> tuple[pd.DataFrame, list[dict]]:
    return validate_rows(df, file_label, key_columns=["lot_id", "defect_type"],
                         quantity_columns=["defect_qty"], date_columns=[])


# ---------------------------------------------------------------------------
# X1·X2: 두 파일 간 교차 검증
# ---------------------------------------------------------------------------

def cross_validate(production: pd.DataFrame, defects: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame, list[dict]]:
    """검증을 통과한 production·defects를 서로 맞춰 본다."""
    issues = []

    # X1: production에 없는 LOT의 불량 행 (production 파일에 없거나, 오류로 제외된 LOT)
    orphan = ~defects["lot_id"].isin(set(production["lot_id"]))
    for line in defects.index[orphan]:
        issues.append(make_issue("defects", line, "lot_id", "error",
                                 f"LOT '{defects.at[line, 'lot_id']}'가 유효한 생산 데이터에 없습니다 "
                                 "(production 파일에 없거나 오류로 제외됨)"))
    defects = defects[~orphan]

    # X2: LOT별 불량 수량 합계가 생산량보다 많으면 데이터가 모순이므로 그 LOT를 통째로 제외
    defect_sum = defects.groupby("lot_id")["defect_qty"].sum()                       # LOT -> 불량 합계
    defect_total = production["lot_id"].map(defect_sum).fillna(0).astype(int)        # 불량 행 없는 LOT는 0
    over = defect_total > production["produced_qty"]
    for line in production.index[over]:
        issues.append(make_issue("production", line, "produced_qty", "error",
                                 f"LOT '{production.at[line, 'lot_id']}'의 불량 수량 합계({defect_total[line]:,})가 "
                                 f"생산량({production.at[line, 'produced_qty']:,})보다 많아 이 LOT의 생산·불량 데이터를 모두 제외했습니다"))

    over_lots = set(production.loc[over, "lot_id"])
    defect_over = defects["lot_id"].isin(over_lots)
    for line in defects.index[defect_over]:
        issues.append(make_issue("defects", line, "defect_qty", "error",
                                 f"LOT '{defects.at[line, 'lot_id']}'의 불량 수량 합계가 생산량보다 많아 제외했습니다"))

    return production[~over], defects[~defect_over], issues


# ---------------------------------------------------------------------------
# 진입 함수: 앱은 이 함수만 호출한다
# ---------------------------------------------------------------------------

@dataclass
class UploadResult:
    production: pd.DataFrame | None = None   # 적재할 생산 데이터 (fatal이면 None)
    defects: pd.DataFrame | None = None      # 적재할 불량 데이터 (fatal이면 None)
    issues: list = field(default_factory=list)
    fatal: str | None = None                 # 적재할 수 없는 사유. None이면 적재 가능
    production_rows_read: int = 0            # 빈 줄을 뺀 원본 데이터 행 수 (적재율 표시용)
    defects_rows_read: int = 0

    @property
    def ok(self) -> bool:
        return self.fatal is None


def validate_upload(production_data: bytes, defects_data: bytes | None) -> UploadResult:
    """두 CSV를 읽고 F -> D -> R -> X -> Z 순서로 검증한다.

    defects_data가 None이면(불량 파일을 올리지 않음) 모든 LOT의 불량을 0으로 보고 경고를 남긴다.
    """
    result = UploadResult()
    file_errors = []

    # F1~F4: 두 파일을 모두 읽어 본 뒤, 파일 단위 오류를 한꺼번에 알려 준다
    try:
        production, issues = read_csv(production_data, PRODUCTION_COLUMNS, "production")
        result.issues += issues
    except FileError as e:
        file_errors.append(f"[production] {e}")

    if defects_data is None:
        defects = pd.DataFrame(columns=DEFECT_COLUMNS)
        result.issues.append(make_issue("defects", None, None, "warning",
                                        "불량 파일이 없어 모든 LOT의 불량 수량을 0으로 계산합니다"))
    else:
        try:
            defects, issues = read_csv(defects_data, DEFECT_COLUMNS, "defects", allow_empty=True)
            result.issues += issues
        except FileError as e:
            file_errors.append(f"[defects] {e}")

    if file_errors:
        result.fatal = "\n".join(file_errors)
        return result

    result.production_rows_read = len(production)
    result.defects_rows_read = len(defects)

    # D1·D2, R1·R2: 파일별 검증
    production, issues = validate_production(production)
    result.issues += issues
    defects, issues = validate_defects(defects)
    result.issues += issues

    # X1·X2: 교차 검증
    production, defects, issues = cross_validate(production, defects)
    result.issues += issues

    # Z1: 남은 생산 데이터가 없으면 적재하지 않는다
    if production.empty:
        result.fatal = "검증을 통과한 생산 데이터가 없어 적재하지 않았습니다. 오류 목록을 확인하세요."
        return result

    result.production = production
    result.defects = defects
    return result
