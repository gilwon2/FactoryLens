# 배포와 실행 방법

| 목적 | 방법 | 저장 모드 |
|---|---|---|
| 포트폴리오 공개 데모 | Streamlit Community Cloud (무료) | 데모 모드 (저장 안 함) |
| 누적 저장 시연·운영 | Docker Compose (PostgreSQL + 앱) | 누적 모드 |

## 1. GitHub에 올리기

이 폴더는 이미 Git 저장소(`main` 브랜치)입니다. GitHub에서 빈 저장소 `FactoryLens`를 만든 뒤(README·.gitignore 추가 없이) 프로젝트 루트에서 실행합니다.

```powershell
git add .
git commit -m "파레토·p 관리도, PostgreSQL 누적 적재, Docker·CI 추가"
git remote add origin https://github.com/gilwon2/FactoryLens.git
git push -u origin main
```

- 푸시하면 GitHub Actions(`.github/workflows/ci.yml`)가 자동으로 실행됩니다. 저장소의 **Actions** 탭에서 두 작업(테스트, Docker)이 모두 초록색인지 확인합니다.
- `README.md` 맨 위 CI 배지 주소의 `gilwon2`를 실제 아이디로 바꿉니다.

## 2. Streamlit Community Cloud에 공개 배포 (데모 모드)

1. <https://share.streamlit.io>에 GitHub 계정으로 로그인합니다.
2. **Create app** → **Deploy a public app from GitHub**를 고릅니다.
3. 저장소 `gilwon2/FactoryLens`, 브랜치 `main`, 메인 파일 `app.py`를 지정합니다.
4. **Advanced settings**에서 Python 버전을 3.12로 고릅니다. **Secrets는 비워 둡니다.** `FACTORYLENS_DATABASE_URL`이 없어야 데모 모드(업로드를 저장하지 않음)로 실행됩니다.
5. 원하는 앱 주소(예: `factorylens`)를 정하고 **Deploy**를 누릅니다.
6. 배포된 주소를 `README.md`의 "데모" 항목에 넣고 커밋합니다.

### 배포 후 확인 (테스트 보고서 M5)

| 확인 | 방법 | 기대 결과 |
|---|---|---|
| 기본 동작 | "샘플 데이터로 체험" | 생산량 288,347, 불량률 2.23%, 탭 5개 |
| 업로드 크기 제한 | 10MB가 넘는 CSV 업로드 | "File must be 10.0MB or smaller." |
| 오류 상세 숨김 | 위 행으로 간접 확인 | 업로드 제한이 10MB로 동작하면 같은 `.streamlit/config.toml`이 읽힌 것이므로 `showErrorDetails = "none"`도 적용된 것입니다. 정상 사용 중에는 처리되지 않은 예외가 나지 않아 직접 재현하기는 어렵습니다. |

확인 결과는 `docs/test_report.md`의 M5 행에 기록합니다.

## 3. Docker Compose로 누적 모드 실행

[Docker Desktop](https://www.docker.com/products/docker-desktop/)을 설치한 뒤 프로젝트 루트에서 실행합니다.

```powershell
docker compose up --build          # http://localhost:8501
```

- 처음에는 DB가 비어 있습니다. "샘플 데이터로 체험"을 두 번 눌러 보면 두 번째는 "재업로드로 건너뜀 534 LOT"가 되고, **적재 이력** 탭에 배치 2건이 남습니다.
- 명령줄 적재:
  ```powershell
  docker compose run --rm app python -m factorylens.ingest data/sample/production_sample.csv data/sample/defects_sample.csv --source "2026-09 월간 실적"
  ```
- DB 비밀번호를 바꾸려면 `.env.example`을 `.env`로 복사해 `POSTGRES_PASSWORD`를 수정합니다. `.env`는 Git에 올라가지 않습니다.
- 데이터는 Docker 볼륨 `pgdata`에 남습니다. 모두 지우려면 `docker compose down -v`를 실행합니다.

### 정기 적재 예시 (Windows 작업 스케줄러)

매일 아침 `inbox` 폴더의 파일을 적재하는 배치 파일입니다. 작업 스케줄러에 등록하면 됩니다.

```bat
@echo off
cd /d "C:\경로\FactoryLens"
docker compose run --rm app python -m factorylens.ingest inbox/production.csv inbox/defects.csv --source "%date% 정기 적재"
```

종료 코드는 0(적재됨), 1(파일 거부 또는 DB 오류), 2(DB 연결·설정 오류)입니다. 스케줄러에서 실패 알림 조건으로 쓸 수 있습니다.

## 주의

- **누적 모드는 인터넷에 공개하지 않습니다.** 로그인 기능이 없어 주소를 아는 사람은 누구나 데이터를 올릴 수 있습니다. 사내 망에서만 실행합니다.
- 테스트용 PostgreSQL 주소(`FACTORYLENS_TEST_DATABASE_URL`)에는 운영 DB를 쓰면 안 됩니다. 테스트가 테이블을 지웁니다.
