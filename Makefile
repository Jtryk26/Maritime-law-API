# =============================================================================
# Maritim Lovdatabase — udviklingskommandoer
# =============================================================================
.DEFAULT_GOAL := help
.PHONY: help up down logs build rebuild install migrate seed import import-rev2 \
        import-prod test verify api web stats clean psql shell

BACKEND := cd backend && PYTHONPATH=.

help:  ## Vis denne oversigt
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) \
	 | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-16s\033[0m %s\n", $$1, $$2}'

# --- Docker ------------------------------------------------------------------

up:  ## Start hele systemet (db + backend + frontend)
	docker compose up --build

down:  ## Stop systemet
	docker compose down

logs:  ## Følg logs
	docker compose logs -f

rebuild:  ## Byg om fra bunden og start
	docker compose down -v && docker compose up --build

psql:  ## Åbn psql i databasen
	docker compose exec db psql -U maritim -d maritim

# --- Lokal udvikling ---------------------------------------------------------

install:  ## Installér backend- og frontend-afhængigheder
	pip install -r backend/requirements.txt
	cd frontend && npm install

migrate:  ## Kør databasemigrationer
	$(BACKEND) python -m app.cli migrate

seed:  ## Seed den maritime taksonomi
	$(BACKEND) python -m app.cli seed

import:  ## Importér fixturdata (revision 1)
	$(BACKEND) python -m app.cli import --source fixture --fixture-revision 1

import-rev2:  ## Importér fixturdata revision 2 — demonstrerer versionering
	$(BACKEND) python -m app.cli import --source fixture --fixture-revision 2

import-prod:  ## Importér fra Retsinformations officielle høsteservice
	$(BACKEND) python -m app.cli import --source production

stats:  ## Vis nøgletal fra databasen
	$(BACKEND) python -m app.cli stats

api:  ## Start backend på http://localhost:8000
	$(BACKEND) python -m uvicorn app.main:app --reload --port 8000

web:  ## Start frontend på http://localhost:5173
	cd frontend && npm run dev

# --- Kvalitet ----------------------------------------------------------------

test:  ## Kør testsuiten
	$(BACKEND) python -m pytest

verify:  ## Kør integrationsverifikation mod et kørende API
	python3 scripts/verify_api.py

clean:  ## Fjern lokale databaser og byggeartefakter
	rm -f data/*.db
	rm -rf frontend/dist backend/.pytest_cache
	find . -name __pycache__ -type d -prune -exec rm -rf {} +
