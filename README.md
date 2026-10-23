# FactoryLens

[![CI](https://github.com/gilwon2/FactoryLens/actions/workflows/ci.yml/badge.svg)](https://github.com/gilwon2/FactoryLens/actions/workflows/ci.yml)

제조 현장의 생산·검사 CSV를 **검증해서 적재하고**, 공정별·제품별 불량률, **파레토 분석**, **p 관리도**로 품질 문제를 찾는 데이터 앱입니다.
공개 데모는 업로드한 데이터를 저장하지 않고, Docker로 실행하면 **PostgreSQL에 누적 저장하는 적재 파이프라인**으로 동작합니다.

> ⚠️ 이 저장소의 모든 데이터는 `scripts/generate_sample_data.py`로 생성한 **가상 데이터**이며 실제 공장 데이터가 아닙니다.

- **데모:** https://factorylens-1.streamlit.app · "샘플 데이터로 체험" 버튼으로 바로 써 볼 수 있습니다 (공개 데모는 업로드한 데이터를 저장하지 않습니다)
- **문서:** [ERD](docs/ERD.md) · [지표 정의](docs/metrics.md) · [테스트 결과](docs/test_report.md) · [배포 방법](docs/deploy.md) · [오류 예시 CSV](data/invalid_examples/README.md)

![FactoryLens 화면](docs/images/overview.png)

## 해결하려는 문제

중소 제조 현장에서는 MES 같은 시스템 없이 **생산 실적과 검사 결과를 엑셀로 모아 손으로 집계**하는 경우가 많습니다. 이때 흔히 생기는 문제는 세 가지입니다.

1. **데이터 오류가 집계에 그대로 섞입니다.** 빈칸, 날짜 오타, 같은 LOT의 중복 입력, 생산량보다 많은 불량 수가 결과를 조용히 왜곡합니다.
2. **집계 방식이 틀립니다.** LOT별 불량률을 단순 평균하거나, 생산·불량 표를 그냥 합치다가 생산량이 중복 합산됩니다.
3. **숫자는 있지만 "어디부터 고칠지"와 "이상이 생겼는지"를 판단하기 어렵습니다.**

FactoryLens는 이 세 가지를 **검증 → 정확한 SQL 집계 → 품질 분석(파레토·관리도)** 순서로 해결합니다. 제외한 데이터는 숨기지 않고 줄 번호와 사유로 보여 주고, 누적 모드에서는 업로드마다 적재 이력을 남깁니다.

## 주요 기능

| 기능 | 내용 |
|---|---|
| CSV 검증 | 필수 컬럼, 타입, 결측, 중복, 파일 간 정합성을 검사합니다. 정상 행만 적재하고, 제외한 행은 줄 번호와 사유를 표로 보여 줍니다. |
| 핵심 지표 | 생산량, 불량 수량, 불량률(가중 평균), LOT 수. 공정·제품·일자별 집계 |
| **파레토 분석** | 불량 유형을 많은 순으로 정렬하고 누적 비중을 계산합니다. 누적 80%를 차지하는 "핵심 소수" 유형을 자동으로 알려 줍니다. |
| **p 관리도** | 공정별 일자 불량률에 3σ 관리 한계를 그리고, 한계를 벗어난 날을 자동으로 표시합니다. |
| **누적 적재 (PostgreSQL)** | 업로드가 DB에 쌓입니다. 같은 파일을 다시 올려도 중복 적재되지 않고(멱등성), 기존 LOT와 내용이 다르면 그 LOT를 제외합니다. |
| **적재 이력** | 업로드 1회 = 배치 1건. 거부된 업로드까지 기록하고, 배치마다 제외된 행과 사유를 DB에 남깁니다. |
| **명령줄 적재** | `python -m factorylens.ingest`로 정기 작업(cron, 작업 스케줄러)에서 파일을 적재할 수 있습니다. |
| 결과 다운로드 | 탭별 CSV. 엑셀에서 한글이 깨지지 않게 저장하고(UTF-8 BOM), 수식 주입을 막습니다. |

| 파레토 분석 | p 관리도 |
|---|---|
| ![파레토 차트](docs/images/pareto.png) | ![p 관리도](docs/images/p_chart.png) |

![적재 이력](docs/images/batch_history.png)
<sub>누적 모드의 적재 이력: 같은 파일을 다시 올린 배치 #2는 534개 LOT를 모두 건너뛰었고, 빈 파일(#4)은 거부 기록과 사유가 남았습니다.</sub>

## 아키텍처

```mermaid
flowchart LR
    CSV["production.csv<br/>defects.csv"] --> V["검증<br/>validation.py<br/>F·D·R·X·Z 규칙"]
    V --> I["적재<br/>ingest.py<br/>K 규칙 · 배치 기록"]
    I --> DB[("DB<br/>데모: 인메모리 SQLite<br/>누적: PostgreSQL")]
    DB --> M["집계 SQL<br/>metrics.py<br/>불량률 · 파레토 · p 관리도"]
    M --> UI["Streamlit 화면<br/>app.py"]
    CLI["명령줄<br/>python -m factorylens.ingest"] --> V
    UI -- 업로드 --> V
```

- 화면(`app.py`)과 로직(`factorylens/`)을 분리했습니다. 로직은 Streamlit 없이 pytest로 테스트합니다.
- 화면 업로드와 명령줄 적재가 **같은 검증·적재 코드**를 씁니다.
- 집계 SQL은 SQLite와 PostgreSQL에서 똑같이 동작하는 문법만 씁니다. **같은 테스트를 두 DB에서 모두 실행**해 결과가 같은지 확인합니다.

## 실행 모드

| | 데모 모드 (기본) | 누적 모드 |
|---|---|---|
| 켜는 방법 | 아무 설정 없음 | 환경 변수 `FACTORYLENS_DATABASE_URL`에 DB 주소 |
| DB | 세션별 인메모리 SQLite | PostgreSQL (또는 `sqlite:///파일`) |
| 업로드 | 업로드할 때마다 새 DB로 교체 | 계속 쌓임. 재업로드는 건너뜀 |
| 화면 | 마지막 업로드 데이터 | 누적된 전체 데이터 + 적재 이력 탭 |
| 용도 | 공개 배포. 사용자 간 데이터가 섞이지 않고 서버에 남지 않음 | 사내 망에서 운영. 데이터 누적·추이 분석 |

## 빠른 시작

### 1. 데모 모드 (Python만 있으면 됨)

Python 3.10 이상이 필요합니다.

```bash
git clone <저장소 주소>
cd FactoryLens
python -m venv .venv
.venv\Scripts\activate            # macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt
streamlit run app.py              # http://localhost:8501
```

### 2. 누적 모드 (Docker + PostgreSQL)

```bash
docker compose up --build         # PostgreSQL과 앱을 함께 실행 -> http://localhost:8501
docker compose down               # 중지 (데이터는 볼륨에 남음)
docker compose down -v            # 중지 + 데이터 삭제
```

명령줄로 적재할 수도 있습니다. 자기 파일은 `inbox/` 폴더에 넣고 `inbox/production.csv`처럼 지정합니다.

```bash
docker compose run --rm app python -m factorylens.ingest data/sample/production_sample.csv data/sample/defects_sample.csv --source "2026-09 월간 실적"
# 배치 #1 [loaded] LOT 신규 534 · 건너뜀 0 · 충돌 제외 0 · 불량 행 신규 1,353

# 같은 명령을 한 번 더 실행하면 (멱등성)
# 배치 #2 [loaded] LOT 신규 0 · 건너뜀 534 · 충돌 제외 0 · 불량 행 신규 0
```

Docker 없이 누적 모드를 써 보려면 SQLite 파일을 지정합니다.

```bash
# PowerShell
$env:FACTORYLENS_DATABASE_URL="sqlite:///factorylens.db"; streamlit run app.py
# macOS/Linux
FACTORYLENS_DATABASE_URL=sqlite:///factorylens.db streamlit run app.py
```

### 3. 테스트

```bash
pip install -r requirements-dev.txt
python -m pytest -q                                    # SQLite로 실행 (PostgreSQL 테스트는 건너뜀)

# PostgreSQL에서도 실행하려면 테스트 전용 DB 주소를 지정한다 (테스트마다 테이블을 지우므로 운영 DB 금지)
FACTORYLENS_TEST_DATABASE_URL=postgresql://postgres:postgres@localhost:5432/factorylens_test python -m pytest -q
```

샘플 데이터와 오류 예시는 시드가 고정되어 있어 다시 만들어도 항상 같습니다.

```bash
python scripts/generate_sample_data.py
python scripts/make_invalid_examples.py
```

처리되지 않은 예외의 상세 정보는 브라우저에 표시하지 않고 터미널에만 남깁니다(`.streamlit/config.toml`의 `client.showErrorDetails = "none"`). 로컬에서 디버깅할 때만 `streamlit run app.py --client.showErrorDetails=full`로 켭니다. 설정 파일은 실행 위치 기준으로 읽으므로 **프로젝트 루트에서 실행**해야 합니다.

## 입력 데이터 형식

**production.csv** (LOT당 1행, 필수 업로드)

| 컬럼 | 규칙 |
|---|---|
| `lot_id` | 필수, 파일 내 고유 |
| `work_date` | `YYYY-MM-DD`, 실제 존재하는 날짜 |
| `process` | 필수 (예: PRESS, WELD, PAINT, ASSY) |
| `product` | 필수 |
| `produced_qty` | 0 이상의 정수 |

**defects.csv** (LOT×불량 유형당 1행, 선택 업로드)

| 컬럼 | 규칙 |
|---|---|
| `lot_id` | production에 있는 LOT |
| `defect_type` | 필수, `lot_id`와 함께 파일 내 고유 |
| `defect_qty` | 0 이상의 정수 |

- 컬럼명의 대소문자와 앞뒤 공백은 무시합니다. 추가 컬럼은 안내 후 무시합니다.
- 인코딩은 UTF-8, UTF-8 BOM(엑셀 "CSV UTF-8"), CP949를 지원합니다.
- defects.csv를 올리지 않거나 헤더만 있으면 모든 LOT의 불량을 0으로 계산합니다.

## 데이터 검증·적재 규칙

| 단계 | 규칙 | 처리 |
|---|---|---|
| F 파일 | 빈 파일, 읽을 수 없는 인코딩, 열 개수가 다른 줄, 필수 컬럼 누락, production에 데이터 행 없음 | **파일 거부** (분석 결과 CSV를 잘못 올리면 따로 안내) |
| D 중복 | 모든 값이 같은 완전중복 | 첫 행만 반영하고 경고 |
| D 중복 | 키(`lot_id` / `lot_id`+`defect_type`)는 같은데 값이 다름 | 그 키의 행 **전부 제외** |
| R 행 | 결측, 정수가 아닌 수량·음수, 잘못된 날짜 | 행 제외 |
| X 교차 | production에 없는 LOT의 불량 행 | 불량 행 제외 |
| X 교차 | LOT의 불량 합계가 생산량보다 많음 | 해당 LOT의 생산·불량 **모두 제외** |
| Z 0건 | 검증 후 유효한 생산 데이터가 0건 | 적재 중단 (오류 목록은 표시) |
| K 기존 데이터 | 이미 적재된 LOT와 생산·불량 내용이 같음 | **건너뜀** (재업로드해도 결과가 같음 = 멱등성) |
| K 기존 데이터 | 이미 적재된 LOT인데 내용이 다름 | 그 LOT의 새 행 **모두 제외**, 기존 데이터는 그대로 |

K 규칙은 누적 모드에서만 의미가 있습니다(데모 모드는 매번 빈 DB). 일부 행이 제외되면 화면에 "지표는 적재된 데이터만으로 계산한 값"이라는 경고가 항상 표시됩니다.

## 품질 분석

- **불량률(%)** = 불량 수량 ÷ 생산량 × 100. 그룹 불량률은 LOT별 불량률의 단순 평균이 아니라 **합계 ÷ 합계(가중 평균)**입니다. 생산량이 0이면 `-`로 표시합니다.
- **파레토 분석:** 불량 유형을 수량이 많은 순으로 놓고 누적 비중을 계산합니다. 샘플 데이터에서는 11개 유형 중 **상위 7개가 불량의 80.2%**입니다. 개선 우선순위를 정하는 데 씁니다.
- **p 관리도:** 일자별 불량률 p와 중심선 p̄(기간 가중 평균), 관리 한계 p̄ ± 3√(p̄(1−p̄)/n)을 그립니다. 날마다 생산량 n이 달라 한계도 날마다 다릅니다.
  - **공정별로 그립니다.** 공정마다 기준 불량률이 다르기 때문입니다(샘플: ASSY 1.05%, PAINT 3.68%). 여러 공정을 합쳐 그리면 그날의 공정 구성이 바뀐 것만으로 이상처럼 보입니다.
  - 샘플 데이터는 관리 한계를 벗어난 날이 없어 "관리 상태"로 표시됩니다. 한계 이탈 판정은 손계산한 데이터로 테스트합니다(`tests/test_metrics.py`).

자세한 정의와 SQL은 [docs/metrics.md](docs/metrics.md)에 있습니다.

## 설계 결정

| 문제 | 결정 | 이유 |
|---|---|---|
| 불량 유형이 여러 개인 LOT를 어떻게 저장할지 | 생산·불량 테이블을 1:N으로 분리 | 한 테이블에 두면 생산량이 행마다 반복되어 중복 합산될 위험이 있습니다. |
| 생산·불량 JOIN 시 생산량 중복 | 불량을 LOT별로 먼저 합산한 뒤 LEFT JOIN | JOIN 팬아웃을 막고, 불량이 없는 LOT도 집계에 포함합니다. |
| 같은 키의 값이 서로 다른 행 | 어느 쪽도 채택하지 않고 모두 제외 | 어느 값이 맞는지 시스템이 판단할 근거가 없습니다. 완전중복은 같은 데이터가 두 번 들어온 것으로 보고 1행만 반영합니다. |
| 중복과 타입 오류를 검사하는 순서 | 중복을 먼저 검사 | 타입 오류 행이 먼저 빠지면 같은 키의 다른 행이 충돌 없이 통과해 버립니다. |
| 오류 행이 있는 파일 | 정상 행만 적재하고 제외 내역을 표시 | 사용자가 데이터를 고치지 않아도 분석할 수 있게 하되, 제외 사실은 숨기지 않습니다. |
| 공개 배포와 데이터 누적의 충돌 | **실행 모드를 둘로 나눔** (환경 변수 하나로 전환) | 공개 데모는 개인정보·데이터 혼입 위험이 없어야 하고, 실제 운영은 누적이 필요합니다. 코드는 같고 저장 위치만 다릅니다. |
| 같은 파일을 다시 올렸을 때 | LOT 단위로 비교해 같으면 건너뜀 (멱등성) | 파이프라인은 재실행될 수 있습니다. 재실행해도 합계가 두 배가 되면 안 됩니다. |
| 이미 적재된 LOT와 내용이 다를 때 | 새 행을 제외하고 기존 데이터는 유지 | 덮어쓰면 누가 언제 무엇을 바꿨는지 사라집니다. 수정이 필요하면 사유와 함께 따로 처리하는 것이 안전합니다. |
| 무엇이 왜 빠졌는지 추적 | 업로드마다 배치를 기록하고 이슈를 DB에 저장 | 화면을 닫으면 사라지는 오류 목록으로는 나중에 "이 숫자가 왜 이런가"에 답할 수 없습니다. |
| 배치 기록과 데이터 적재 | 한 트랜잭션으로 묶음 | 중간에 실패하면 배치 기록만 남거나 데이터만 일부 들어가는 상태가 생기지 않습니다. |
| SQLite와 PostgreSQL을 함께 지원하는 방법 | ORM 대신 얇은 DB 래퍼 + 두 DB 공통 SQL | 집계 SQL을 그대로 보여 주는 것이 이 프로젝트의 핵심입니다. 차이(자리표시자, 반환 타입)는 `db.py` 한 곳에서 흡수합니다. |
| p 관리도 대상 | 공정 하나씩 | 공정마다 기준 불량률이 달라 합치면 공정 구성 변화가 이상으로 보입니다. |
| DB 오류 메시지 | 화면에는 일반 문구, 원문은 서버 로그 | DB 오류 원문에는 테이블·컬럼 이름이 들어 있어 공개 화면에 노출하지 않습니다. |

## 프로젝트 구조

```
FactoryLens/
├─ app.py                       # Streamlit 화면 (업로드 → 리포트 → 필터 → 지표·탭·다운로드)
├─ factorylens/
│  ├─ validation.py             # CSV 읽기와 검증 (F·D·R·X·Z 규칙)
│  ├─ ingest.py                 # 적재·배치 기록 (K 규칙), 명령줄 적재
│  ├─ db.py                     # DB 연결·스키마 (인메모리 SQLite / SQLite 파일 / PostgreSQL)
│  ├─ metrics.py                # 집계 SQL, 불량률, 파레토, p 관리도, 적재 이력
│  └─ export.py                 # 다운로드 CSV (BOM, 수식 주입 방지)
├─ tests/                       # pytest 168개 (집계·적재 40개는 PostgreSQL에서도 실행)
├─ Dockerfile, docker-compose.yml  # 누적 모드 실행 (PostgreSQL + 앱)
├─ .github/workflows/ci.yml     # 테스트(두 DB) + Docker 빌드·적재 확인
├─ scripts/                     # 샘플 데이터·오류 예시 생성
├─ data/sample/                 # 가상 샘플 데이터
├─ data/invalid_examples/       # 오류 처리 시연용 CSV
├─ inbox/                       # 명령줄 적재용 입력 폴더 (Docker에 연결)
└─ docs/                        # ERD, 지표 정의, 테스트 결과, 배포 방법, 화면 이미지
```

## 테스트

- **테스트 168개 + PostgreSQL 반복 실행 40개 = 208개 통과, 커버리지 98%** (로직 모듈 97~100%)
- 집계·적재 테스트 40개는 **SQLite와 PostgreSQL에서 각각 실행**됩니다. GitHub Actions가 PostgreSQL 서비스를 띄워 매번 두 DB로 확인하고, PostgreSQL 테스트가 하나라도 건너뛰어지면 실패로 처리합니다.
- CI는 Docker Compose로 앱과 PostgreSQL을 띄우고, 명령줄 적재를 두 번 실행해 두 번째가 전부 건너뛰어지는지(멱등성)까지 확인합니다.
- 화면 흐름은 Streamlit `AppTest`로 브라우저 없이 테스트합니다.
- 상세 내용: [docs/test_report.md](docs/test_report.md)

## 기술 스택

| 기술 | 선택 이유 |
|---|---|
| Python 3.10+ | 데이터 처리 생태계 |
| Streamlit · Altair | 별도 프런트엔드 없이 업로드, 차트(파레토 이중 축, 관리도 계단형 한계선), 다운로드 UI를 구현할 수 있습니다. |
| pandas | CSV 읽기, 행 단위 검증, 중복 판별 |
| SQLite (`sqlite3`) | 데모 모드. 서버 설치 없이 SQL 집계를 하고, 세션마다 독립된 DB를 만듭니다. |
| PostgreSQL · psycopg 3 | 누적 모드. 여러 사용자가 동시에 쓰는 운영 DB를 가정합니다. 날짜는 `DATE`, 배치 번호는 `IDENTITY`로 저장합니다. |
| Docker Compose | 앱과 DB를 명령 하나로 실행하고, DB가 준비된 뒤에 앱이 뜨도록(healthcheck) 순서를 보장합니다. |
| pytest · GitHub Actions | 손으로 계산한 기대값으로 핵심 로직을 검증하고, 푸시할 때마다 두 DB와 Docker 실행을 확인합니다. |

## 가정과 한계

- **전수검사 가정:** 검사 수량과 생산 수량을 같게 봅니다.
- **대표 불량 유형:** 불량품 1개에 대표 불량 유형 1개만 기록한다고 가정합니다.
- **누적 모드에는 로그인이 없습니다.** 주소를 아는 사람은 누구나 업로드할 수 있으므로 사내 망에서만 실행해야 합니다. 공개 배포는 데모 모드로 합니다.
- **데이터 수정 기능이 없습니다.** 이미 적재된 LOT의 값을 바꾸려면 DB에서 직접 처리해야 합니다(의도적으로 덮어쓰기를 막았습니다).
- **p 관리도는 해석용(Phase I)입니다.** 관리 한계를 같은 기간 데이터로 계산하고, 3σ 이탈만 판정합니다(연속 상승 같은 런 규칙은 없음).
- **코드값 대소문자:** 공정·제품 코드는 대소문자를 구분합니다(`PRESS` ≠ `Press`).
- **업로드 크기:** 최대 10MB입니다. 20만 행 기준 검증에 약 2초가 걸립니다.
