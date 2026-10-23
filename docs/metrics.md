# 지표 정의와 집계 기준

구현 위치: `factorylens/metrics.py`

## 지표

| 지표 | 정의 | 비고 |
|---|---|---|
| 생산량 | `SUM(produced_qty)` | 전수검사를 가정하므로 검사 수량과 같습니다. |
| 불량 수량 | `SUM(defect_qty)` | **불량품 개수**입니다. 불량이 발생한 LOT 수나 불량 행 수가 아닙니다. |
| 불량률(%) | 불량 수량 ÷ 생산량 × 100, 소수 둘째 자리 반올림 | 생산량이 0이면 계산하지 않고 NULL로 두며, 화면에는 `-`, CSV에는 빈 칸으로 표시합니다. |
| 불량 유형 비중(%) | 유형별 불량 수량 ÷ 선택 범위 전체 불량 수량 × 100 | 전체 불량이 0이면 계산하지 않습니다. |
| 누적 비중(%) | 불량 수량이 많은 유형부터 더한 수량 ÷ 전체 불량 수량 × 100 | 파레토 분석. 반올림한 비중을 더하지 않고 수량의 누적합으로 계산해 마지막 값이 정확히 100이 됩니다. |
| 핵심 소수 유형 수 | 누적 비중이 처음으로 80% 이상이 되는 순위 | 샘플 데이터: 11개 유형 중 7개(80.16%) |
| LOT 수 | `COUNT(*)` (production 행 수) | 불량이 없는 LOT도 포함합니다. |

## 집계 기준

1. **가중 평균:** 그룹 불량률은 그룹의 불량 합계 ÷ 생산량 합계입니다. LOT별 불량률의 단순 평균을 쓰지 않습니다.
   - 예: L1(불량 5 / 생산 100 = 5%)과 L2(3 / 300 = 1%)의 단순 평균은 3%입니다.
   - 실제 불량률은 8 / 400 = **2%**입니다.
2. **불량은 LOT의 생산일에 귀속:** 날짜 필터는 `production.work_date` 기준이고 시작일과 종료일을 모두 포함합니다.
3. **선집계 후 JOIN:**
   - 불량을 LOT 단위로 먼저 합산한 뒤 `production`에 LEFT JOIN합니다.
   - `defect`를 바로 JOIN하면 불량 유형이 여러 개인 LOT의 생산량이 그 수만큼 중복 합산됩니다(JOIN 팬아웃).
4. **불량 행이 없는 LOT는 불량 0:** `LEFT JOIN`과 `COALESCE(defect_qty, 0)`로 처리합니다.
5. **필터:** 공정·제품을 하나도 선택하지 않으면 결과는 0건이며, 화면에 안내 문구가 나옵니다.

## 핵심 SQL

```sql
WITH lot_defect AS (                 -- ① 불량을 LOT 단위로 먼저 합산
    SELECT lot_id, SUM(defect_qty) AS defect_qty
    FROM defect
    GROUP BY lot_id
),
lot AS (                             -- ② LOT별 생산량 + 불량 합계 (불량 없으면 0)
    SELECT p.*, COALESCE(ld.defect_qty, 0) AS defect_qty
    FROM production p
    LEFT JOIN lot_defect ld ON ld.lot_id = p.lot_id
    WHERE p.work_date BETWEEN ? AND ?            -- 필터 값은 ? 플레이스홀더로 전달
      AND p.process IN (?, ?, ...)
)
SELECT process,                      -- ③ 그룹별 집계
       COUNT(*)          AS lot_count,
       SUM(produced_qty) AS produced_qty,
       SUM(defect_qty)   AS defect_qty,
       CASE WHEN SUM(produced_qty) = 0 THEN NULL                       -- 0으로 나누기 방지
            ELSE ROUND(100.0 * SUM(defect_qty) / SUM(produced_qty), 2)  -- 100.0: 정수 나눗셈 방지
       END AS defect_rate_pct
FROM lot
GROUP BY process
ORDER BY defect_rate_pct DESC NULLS LAST, process;                      -- 불량률 높은 순, 계산 불가는 맨 뒤
```

- 이 SQL은 SQLite와 PostgreSQL에서 똑같이 실행됩니다. `?` 자리표시자는 PostgreSQL 드라이버에 넘길 때 `%s`로 바뀝니다(`factorylens/db.py`).
- `ORDER BY defect_rate_pct IS NULL`처럼 출력 별칭을 식 안에 쓰면 PostgreSQL에서 오류가 나므로, 두 DB가 모두 지원하는 `NULLS LAST`를 씁니다.
- PostgreSQL에서 `SUM(bigint)`는 `numeric`을 돌려줍니다. 소수 자릿수가 없는 값은 정수로, `ROUND(..., 2)` 결과는 실수로 바꿔 두 DB의 결과 타입을 맞춥니다.

## p 관리도 (불량률 관리도)

구현: `metrics.p_chart()`. 공정 하나, 선택한 기간·제품 기준입니다.

| 항목 | 정의 |
|---|---|
| 일자별 불량률 p<sub>i</sub> | 그날 불량 수량 ÷ 생산량 (n<sub>i</sub>) |
| 중심선 CL = p̄ | 기간 전체 불량 수량 합계 ÷ 생산량 합계 (가중 평균) |
| 관리 상한 UCL<sub>i</sub> | p̄ + 3 × √(p̄(1−p̄) / n<sub>i</sub>) |
| 관리 하한 LCL<sub>i</sub> | max(0, p̄ − 3 × √(p̄(1−p̄) / n<sub>i</sub>)) |
| 이탈 | p<sub>i</sub> > UCL<sub>i</sub> 또는 p<sub>i</sub> < LCL<sub>i</sub> (반올림 전 값으로 판정) |

- **날마다 한계가 다릅니다.** 생산량이 많은 날은 우연 변동이 작아 한계가 좁아집니다. 화면에서는 계단 모양 점선으로 그립니다.
- **공정별로 그립니다.** 샘플 데이터의 공정별 기준 불량률은 ASSY 1.05% ~ PAINT 3.68%로 3배 넘게 차이 납니다. 공정을 합쳐 그리면 그날 어느 공정의 LOT가 많았는지에 따라 불량률이 흔들려, 공정 자체에 문제가 없어도 이상처럼 보입니다.
- **생산량이 0인 날은 제외합니다.** 불량률을 정의할 수 없습니다.
- **해석용(Phase I) 관리도입니다.** 관리 한계를 같은 기간 데이터로 계산합니다. 운영용으로 쓰려면 안정 기간의 p̄를 고정해 두고 새 데이터를 판정해야 합니다.
- **손계산 예시** (`tests/test_metrics.py`): 하루 1,000개씩 10일, 9일은 불량 20개, 하루는 50개
  → p̄ = 230 / 10,000 = 2.3%, σ = √(0.023 × 0.977 / 1,000) = 0.474%, UCL = 3.722%, LCL = 0.878%
  → 불량 50개인 날(5.0%)만 이탈

## 데이터 가정

- 모든 샘플 데이터는 `scripts/generate_sample_data.py`로 만든 **가상 데이터**입니다.
- 불량품 1개에는 대표 불량 유형 1개만 기록합니다. 그래서 유형별 수량의 합이 LOT의 불량 수량이 됩니다.
- 공정·제품·불량 유형 코드는 앞뒤 공백만 제거하고 대소문자는 바꾸지 않습니다. `PRESS`와 `Press`는 서로 다른 값으로 집계됩니다.
