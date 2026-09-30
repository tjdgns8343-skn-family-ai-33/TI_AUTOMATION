# 백엔드(Django + gunicorn)와 작업자(Celery)가 함께 쓰는 이미지. 빌드 컨텍스트는 저장소 루트.
FROM python:3.12-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONUTF8=1

# ngspice: 회로 시뮬레이션 (리눅스에서는 `ngspice` 실행 파일을 PATH에서 찾는다), curl: 헬스체크
RUN apt-get update \
    && apt-get install -y --no-install-recommends ngspice curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# 의존성을 먼저 설치해 코드만 바뀌면 이 층을 다시 쓴다
COPY backend/requirements.txt backend/requirements.txt
RUN pip install --no-cache-dir -r backend/requirements.txt

COPY fae_agent fae_agent
COPY circuits circuits
COPY backend backend
# 데이터: 부품 카드, 예시 문의, 사례 카드, 예시 화면용 평가 문의, 문서 검색 색인 조각 (원본 PDF는 넣지 않는다)
COPY data/parts data/parts
COPY data/samples data/samples
COPY data/cases data/cases
COPY data/eval data/eval
COPY data/processed/chunks.jsonl data/processed/chunks.jsonl

WORKDIR /app/backend
RUN python manage.py collectstatic --noinput \
    && chmod +x /app/backend/entrypoint.sh

EXPOSE 8000
CMD ["/app/backend/entrypoint.sh"]
