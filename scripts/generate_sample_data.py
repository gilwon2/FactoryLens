"""가상 생산·검사 샘플 데이터를 생성한다.

※ 이 스크립트가 만드는 데이터는 모두 가상 데이터이며 실제 공장 데이터가 아니다.
   SEED가 고정되어 있어 여러 번 실행해도 항상 같은 CSV가 만들어진다.

실행: python scripts/generate_sample_data.py
결과: data/sample/production_sample.csv, data/sample/defects_sample.csv
"""
import csv
import random
from datetime import date, timedelta
from pathlib import Path

SEED = 42
START_DATE = date(2026, 9, 1)
DAYS = 30

# 공정별 기본 불량률과 발생할 수 있는 불량 유형(가중치)
PROCESSES = {
    "PRESS": {"rate": 0.015, "defects": {"SCRATCH": 5, "DENT": 3, "BURR": 2}},
    "WELD": {"rate": 0.025, "defects": {"SPATTER": 4, "CRACK": 2, "POROSITY": 3}},
    "PAINT": {"rate": 0.035, "defects": {"RUN": 3, "BUBBLE": 4, "COLOR_MISMATCH": 2}},
    "ASSY": {"rate": 0.010, "defects": {"MISSING_PART": 3, "LOOSE_BOLT": 5}},
}

# 제품별 불량률 보정 배수 (P-C300이 가장 까다로운 제품이라고 가정)
PRODUCTS = {"P-A100": 0.8, "P-B200": 1.0, "P-C300": 1.4}

OUT_DIR = Path(__file__).resolve().parent.parent / "data" / "sample"


def generate(rng: random.Random) -> tuple[list[dict], list[dict]]:
    production_rows = []
    defect_rows = []

    for day in range(DAYS):
        work_date = START_DATE + timedelta(days=day)
        for process, spec in PROCESSES.items():
            for product, factor in PRODUCTS.items():
                # 하루에 공정·제품 조합당 LOT 1~2개
                for seq in range(1, rng.randint(1, 2) + 1):
                    lot_id = f"L{work_date:%Y%m%d}-{process}-{product}-{seq}"

                    # 약 2% 확률로 설비 정지(생산 0) LOT
                    produced_qty = 0 if rng.random() < 0.02 else rng.randint(300, 800)

                    production_rows.append({
                        "lot_id": lot_id,
                        "work_date": work_date.isoformat(),
                        "process": process,
                        "product": product,
                        "produced_qty": produced_qty,
                    })

                    # 생산품 하나하나에 대해 불량 여부를 뽑는다
                    rate = spec["rate"] * factor
                    total_defects = sum(1 for _ in range(produced_qty) if rng.random() < rate)
                    if total_defects == 0:
                        continue

                    # 불량품 하나에 대표 불량 유형 하나를 배정한다 (가정 A3)
                    types = list(spec["defects"])
                    weights = list(spec["defects"].values())
                    counts = {}
                    for defect_type in rng.choices(types, weights=weights, k=total_defects):
                        counts[defect_type] = counts.get(defect_type, 0) + 1

                    for defect_type in types:  # 정해진 순서로 기록해 결과를 재현 가능하게 유지
                        if defect_type in counts:
                            defect_rows.append({
                                "lot_id": lot_id,
                                "defect_type": defect_type,
                                "defect_qty": counts[defect_type],
                            })

    return production_rows, defect_rows


def write_csv(path: Path, rows: list[dict]) -> None:
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def main() -> None:
    production_rows, defect_rows = generate(random.Random(SEED))
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    write_csv(OUT_DIR / "production_sample.csv", production_rows)
    write_csv(OUT_DIR / "defects_sample.csv", defect_rows)
    print(f"production: {len(production_rows)} rows, defects: {len(defect_rows)} rows -> {OUT_DIR}")


if __name__ == "__main__":
    main()
