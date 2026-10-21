"""오류 처리 시연용 예시 CSV를 만든다 (모두 가상 데이터).

실행: python scripts/make_invalid_examples.py
결과: data/invalid_examples/<시나리오>/production.csv, defects.csv

각 시나리오의 기대 결과는 data/invalid_examples/README.md에 있고,
tests/test_invalid_examples.py가 기대 결과대로 동작하는지 자동으로 확인한다.
"""
from pathlib import Path

OUT_DIR = Path(__file__).resolve().parent.parent / "data" / "invalid_examples"

P_HEADER = "lot_id,work_date,process,product,produced_qty\n"
D_HEADER = "lot_id,defect_type,defect_qty\n"

# 정상 기준 데이터 (시나리오마다 일부만 망가뜨린다)
P_OK = (
    "L1,2026-09-01,PRESS,P-A100,500\n"
    "L2,2026-09-01,WELD,P-A100,400\n"
    "L3,2026-09-02,PAINT,P-B200,300\n"
)
D_OK = (
    "L1,SCRATCH,5\n"
    "L2,CRACK,4\n"
    "L3,BUBBLE,9\n"
)

SCENARIOS = {
    # 파일 단위 오류 -> 적재하지 않음
    "01_empty_file": ("", D_HEADER + D_OK),
    "02_missing_column": ("lot_id,work_date,process,product\nL1,2026-09-01,PRESS,P-A100\n", D_HEADER + D_OK),
    "03_header_only": (P_HEADER, D_HEADER + D_OK),
    "04_wrong_column_count": (P_HEADER + P_OK + "L4,2026-09-02,PRESS,P-A100,1,000\n", D_HEADER + D_OK),
    # 행 단위 오류 -> 해당 행만 제외
    "05_bad_values": (
        P_HEADER + P_OK
        + "L4,,PRESS,P-A100,200\n"          # 결측
        + "L5,2026-09-31,PRESS,P-A100,200\n"  # 없는 날짜
        + "L6,2026/09/02,PRESS,P-A100,200\n"  # 날짜 형식
        + "L7,2026-09-02,PRESS,P-A100,12.5\n"  # 소수
        + "L8,2026-09-02,PRESS,P-A100,-10\n",  # 음수
        D_HEADER + D_OK,
    ),
    "06_duplicates": (
        P_HEADER + P_OK
        + "L1,2026-09-01,PRESS,P-A100,500\n"   # L1과 완전중복 -> 1행만 반영
        + "L2,2026-09-01,WELD,P-A100,450\n",   # L2와 키 충돌 -> L2 전부 제외
        D_HEADER + D_OK + "L3,BUBBLE,9\n",      # 완전중복
    ),
    "07_cross_check": (
        P_HEADER + P_OK + "L4,2026-09-02,ASSY,P-C300,3\n",
        D_HEADER + D_OK
        + "L9,DENT,1\n"                          # production에 없는 LOT
        + "L4,LOOSE_BOLT,2\nL4,MISSING_PART,2\n",  # 불량 4 > 생산 3
    ),
    # 정상 처리되어야 하는 경계 사례
    "08_cp949_korean": (P_HEADER + "L1,2026-09-01,프레스,제품A,500\n", D_HEADER + "L1,긁힘,5\n"),
    "09_zero_production": (
        P_HEADER + "L1,2026-09-01,PRESS,P-A100,0\nL2,2026-09-01,WELD,P-A100,0\n",
        D_HEADER,                                # 불량 없음 (헤더만)
    ),
}


def main() -> None:
    for name, (production, defects) in SCENARIOS.items():
        folder = OUT_DIR / name
        folder.mkdir(parents=True, exist_ok=True)
        encoding = "cp949" if "cp949" in name else "utf-8"
        (folder / "production.csv").write_bytes(production.encode(encoding))
        (folder / "defects.csv").write_bytes(defects.encode(encoding))
    print(f"{len(SCENARIOS)} scenarios -> {OUT_DIR}")


if __name__ == "__main__":
    main()
