# FactoryLens 앱 이미지
#   docker compose up --build   (PostgreSQL과 함께 누적 모드로 실행. docker-compose.yml 참고)
FROM python:3.12-slim

# 파이썬 출력 버퍼링을 끄고(.pyc 생성도 끔) 로그가 바로 보이게 한다
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# 의존성 먼저 설치: 소스만 바뀌면 이 층은 캐시를 재사용해 빌드가 빠르다
COPY requirements.txt .
RUN pip install -r requirements.txt

COPY app.py .
COPY .streamlit/ .streamlit/
COPY factorylens/ factorylens/
COPY data/ data/

# root가 아닌 사용자로 실행
RUN useradd --create-home --uid 1000 app
USER app

EXPOSE 8501
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8501/_stcore/health')"

CMD ["streamlit", "run", "app.py", "--server.address=0.0.0.0", "--server.port=8501", "--server.headless=true"]
