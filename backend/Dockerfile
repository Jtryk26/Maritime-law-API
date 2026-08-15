# Backend — FastAPI + importservice
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# Afhængigheder installeres først, så laget kan genbruges i cachen.
COPY backend/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

# --- Lokal embedding-model ------------------------------------------------
# Slå fra med: docker compose build --build-arg WITH_EMBEDDINGS=false
# Da kører systemet leksikalsk (sæt EMBEDDINGS_ENABLED=false), eller mod
# et eksternt endpoint (EMBEDDING_PROVIDER=api), som ikke kræver torch.
ARG WITH_EMBEDDINGS=true
ARG EMBEDDING_MODEL=intfloat/multilingual-e5-small

COPY backend/requirements-embedding.txt ./requirements-embedding.txt
# CPU-hjulet fra PyTorchs eget indeks. CUDA-udgaven er flere gigabyte
# større og ville ikke kunne bruges i en container uden GPU alligevel.
RUN if [ "$WITH_EMBEDDINGS" = "true" ]; then \
      pip install --no-cache-dir \
        --extra-index-url https://download.pytorch.org/whl/cpu \
        -r requirements-embedding.txt ; \
    fi

# Modellen bages ind i imaget. Ellers ville den blive hentet ved første
# søgning i produktion — en langsom første forespørgsel og en container,
# der ikke kan starte uden netværk.
ENV SENTENCE_TRANSFORMERS_HOME=/models
RUN if [ "$WITH_EMBEDDINGS" = "true" ]; then \
      python -c "from sentence_transformers import SentenceTransformer; \
SentenceTransformer('${EMBEDDING_MODEL}', cache_folder='/models')" ; \
    fi

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
