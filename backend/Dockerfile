# Backend — FastAPI + importservice
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# Afhængigheder installeres først, så laget kan genbruges i cachen.
COPY backend/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

# curl bruges af Docker-healthchecket.
RUN apt-get update \
 && apt-get install -y --no-install-recommends curl \
 && rm -rf /var/lib/apt/lists/*

# Applikationskode
COPY backend/ /app/
# Domænekonfiguration og fixturer ligger uden for backend/, men
# forventes på samme relative placering som i repositoriet.
COPY config/ /config/
COPY data/fixtures/ /data/fixtures/

# Settings udleder stier fra repo-roden; i containeren er den /.
ENV CONFIG_DIR=/config \
    FIXTURE_DIR=/data/fixtures

EXPOSE 8000

HEALTHCHECK --interval=15s --timeout=5s --start-period=40s --retries=5 \
  CMD curl -fsS http://localhost:8000/health || exit 1

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
