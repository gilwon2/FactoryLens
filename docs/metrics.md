# 지표 정의와 집계 기준

구현 위치: `factorylens/metrics.py`

## 지표

| 지표 | 정의 | 비고 |
|---|---|---|
| 생산량 | `SUM(produced_qty)` | 전수검사를 가정하므로 검사 수량과 같습니다. |
| 불량 수량 | `SUM(defect_qty)` | **불량품 개수**입니다. 불량이 발생한 LOT 수나 불량 행 수가 아닙니다. |
| 불량률(%) | 불량 수량 ÷ 생산량 × 100, 소수 둘째 자리 반올림 | 생산량이 0이면 계산하지 않고 NULL로 두며, 화면에는 `-`, CSV에는 빈 칸으로 표시합니다. |
| 불량 유형 비중(%) | 유형별 불량 수량 ÷ 선택 범위 전체 불량 수량 × 100 | 전체 불량이 0이면 계산하지 않습니다. |
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
ORDER BY defect_rate_pct IS NULL, defect_rate_pct DESC;                 -- 불량률 높은 순, 계산 불가는 맨 뒤
```

## 데이터 가정

- 모든 샘플 데이터는 `scripts/generate_sample_data.py`로 만든 **가상 데이터**입니다.
- 불량품 1개에는 대표 불량 유형 1개만 기록합니다. 그래서 유형별 수량의 합이 LOT의 불량 수량이 됩니다.
- 공정·제품·불량 유형 코드는 앞뒤 공백만 제거하고 대소문자는 바꾸지 않습니다. `PRESS`와 `Press`는 서로 다른 값으로 집계됩니다.
