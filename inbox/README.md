# inbox

`docker compose run`으로 명령줄 적재를 할 때 CSV를 넣어 두는 폴더입니다. 컨테이너의 `/app/inbox`에 읽기 전용으로 연결됩니다.

```bash
docker compose run --rm app python -m factorylens.ingest inbox/production.csv inbox/defects.csv --source "2026-10-23 일일 실적"
```

이 폴더의 CSV는 Git에 올리지 않습니다(`.gitignore`).
