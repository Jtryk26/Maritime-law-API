# Produktionsvejledning — kurateret relevans-override i Docker

**Status: intet af nedenstående er kørt af mig.** Kommandoerne er udledt af
`docker-compose.yml` og `backend/Dockerfile` i dette repo, ikke afprøvet mod
et kørende miljø — der er ingen Docker-dæmon i det miljø, patchen blev
udviklet i. Testsuiten, migrationskæden og CLI'en ER kørt, men mod SQLite
uden for Docker.

Host-Python mangler blandt andet `pydantic`, så **alt skal køres i
backend-containeren**. Alle kommandoer herunder gør det.

Faste værdier aflæst i `docker-compose.yml` (ikke gættet):

| Hvad | Værdi |
|---|---|
| Backend-container | `maritim-backend` |
| Database-container | `maritim-db` |
| Postgres bruger/db | `maritim` / `maritim` (medmindre `POSTGRES_USER`/`POSTGRES_DB` er sat i `.env`) |
| Arbejdsmappe i container | `/app` |
| Manifest-mount | `./manifests` (host) → `/manifests` (container), **læse/skrive** |
| Migrationer ved opstart | `RUN_MIGRATIONS_ON_STARTUP: "true"` |

---

## 0. Rollback-beredskab — FØR migrationen køres

Migration `0003` opretter to nye tabeller og rører ikke eksisterende data.
Den er derfor lav risiko, men tag en dump alligevel, før du kører den.

```bash
# Fuld dump af databasen, med tidsstempel i filnavnet.
docker compose exec -T db pg_dump -U maritim -d maritim \
  > backup-foer-0003-$(date +%Y%m%d-%H%M%S).sql

ls -lh backup-foer-0003-*.sql   # kontrollér at filen ikke er tom
```

To måder at rulle tilbage på, i stigende omfang:

```bash
# A) Kun migrationen tilbage (beholder alle øvrige data).
#    Dropper de to nye tabeller — altså også al kurateringshistorik.
docker compose exec backend alembic downgrade 0002_backfill_manifest

# B) Hele databasen tilbage til dump'en (sidste udvej).
docker compose exec -T db psql -U maritim -d postgres \
  -c "DROP DATABASE maritim WITH (FORCE);" -c "CREATE DATABASE maritim;"
docker compose exec -T db psql -U maritim -d maritim < backup-foer-0003-TIDSSTEMPEL.sql
```

Kodemæssig rollback (hvis patchen skal af igen):

```bash
git revert <commit-sha>     # eller: git reset --hard <sha-før-patchen>
docker compose up -d --build backend
```

---

## 1. Anvend erstatningspatchen

Denne patch **erstatter** den tidligere leverede
`0001-feat-curated-relevance-override.patch`. Har du allerede anvendt den
gamle, skal den af først.

```bash
cd /sti/til/Maritime-law-API
git status                   # skal være rent, ellers stash først

# Har du IKKE anvendt den gamle patch:
git checkout main && git pull
git am 0001-feat-curated-relevance-override-v2.patch

# Har du allerede anvendt den gamle patch — fjern den først:
#   git log --oneline -3      # find den gamle commit
#   git revert --no-edit <gammel-sha>     (eller reset --hard til før den)
#   git am 0001-feat-curated-relevance-override-v2.patch

git log --oneline -3
```

---

## 2. Byg og genstart backend

Applikationskoden **kopieres** ind i imaget (`COPY backend/ /app/`) — den er
ikke bind-mountet. En kodeændring kræver derfor rebuild:

```bash
docker compose build backend
docker compose up -d backend

# Vent til den er sund, og se at migrationen kørte ved opstart.
docker compose ps
docker compose logs --tail=40 backend | grep -i "alembic\|migration\|0003"
```

`config/` og `data/fixtures/` er både kopieret ind i imaget **og**
bind-mountet read-only, så rene konfigurationsændringer kræver ikke rebuild.

---

## 3. Kør testsuiten i Docker

Brug en engangs-container frem for `exec`, så den kørende API-service ikke
forstyrres:

```bash
docker compose run --rm backend python -m pytest -q
```

Forventet: **282 passed**.

Testene rører ikke Postgres. `tests/conftest.py` sætter `DATABASE_URL` til en
frisk SQLite-fil under `tmp_path` pr. test og kører de rigtige
Alembic-migrationer mod den. Produktionsdata er ikke i spil.

Kun de kuraterede tests:

```bash
docker compose run --rm backend python -m pytest tests/test_curated_relevance_override.py -v
```

Skema-/migrationsdriftcheck:

```bash
docker compose exec backend alembic check
```

> **Forventet output:** `New upgrade operations detected: [('remove_constraint',
> UniqueConstraint(Column('slug', ..., table=<categories>)))]`.
> Dette er en **eksisterende** afvigelse på `categories.slug`, som findes
> uændret uden denne patch — den er et refleksionsartefakt og har intet med
> kurateringen at gøre. Patchen tilføjer ingen ny drift. Ser du andre
> tabeller nævnt, så stop og undersøg.

---

## 4. Kør migration 0003

`RUN_MIGRATIONS_ON_STARTUP: "true"` betyder, at trin 2 allerede har kørt den.
Kontrollér og kør eventuelt eksplicit:

```bash
docker compose exec backend alembic current      # forvent: 0003_curated_relevance_overrides (head)
docker compose exec backend alembic history      # forvent kæden 0001 -> 0002 -> 0003

# Eksplicit (idempotent — gør intet hvis den allerede er kørt):
docker compose exec backend python -m app.cli migrate
```

Kontrollér at tabellerne findes:

```bash
docker compose exec db psql -U maritim -d maritim -c "\dt curated*"
```

---

## 5. Gør manifestfilen tilgængelig i containeren

`./manifests` er bind-mountet til `/manifests`. Filen fulgte med patchen, så
efter `git am` ligger den allerede på hosten og er **umiddelbart synlig i
containeren uden rebuild**:

```bash
ls -l manifests/curated-include-2026-08.txt                    # host
docker compose exec backend ls -l /manifests/curated-include-2026-08.txt   # container
docker compose exec backend head -3 /manifests/curated-include-2026-08.txt
```

Ligger den ikke der (fx hvis du fik filen separat), så kopiér den ind på
hosten — ikke med `docker cp`, mounten klarer resten:

```bash
cp /sti/til/curated-include-2026-08.txt manifests/
```

Kontrollér at der er præcis 16 numre, og at `C20190977160` **ikke** er iblandt:

```bash
grep -c '^[A-Z]' manifests/curated-include-2026-08.txt     # forvent 16
grep -c 'C20190977160' manifests/curated-include-2026-08.txt   # forvent 0
```

---

## 6. Statuskontrol FØR

```bash
docker compose exec backend python -m app.cli backfill status
docker compose exec backend python -m app.cli backfill curated-status
```

Noter tallene. `curated-status` bør være tom, hvis det er første gang.

Se hvad de 17 afviste faktisk står som lige nu:

```bash
docker compose exec db psql -U maritim -d maritim -c \
  "SELECT status, count(*) FROM backfill_manifest_items GROUP BY status ORDER BY status;"
```

---

## 7. Registrér og genindsæt præcis de 16

Kør først en **prøve** uden `--requeue-rejected`, så du ser hvilke poster der
overhovedet rammes:

```bash
docker compose exec backend python -m app.cli backfill enqueue \
  --file /manifests/curated-include-2026-08.txt \
  --tag curated-relevance-2026-08 \
  --curated-include \
  --curated-reason "Global discovery-triage, manuel kontrol aug. 2026: reelt maritimt indhold trods fuldtekstscore under lagringstærsklen" \
  --decided-by "jacob"
```

Dette registrerer de 16 overrides, men **flytter ingen REJECTED-post**.
Kontrollér, at der står `Nye: 16`.

Kør derefter med genindsættelse:

```bash
docker compose exec backend python -m app.cli backfill enqueue \
  --file /manifests/curated-include-2026-08.txt \
  --tag curated-relevance-2026-08 \
  --curated-include \
  --curated-reason "Global discovery-triage, manuel kontrol aug. 2026: reelt maritimt indhold trods fuldtekstscore under lagringstærsklen" \
  --decided-by "jacob" \
  --requeue-rejected
```

Anden kørsel viser `Uændret: 16` for overrides (identisk afgørelse → ingen ny
historikpost) og `Genindsat: 16` for køen. Det er den forventede,
idempotente opførsel.

Kontrollér før du kører køen:

```bash
docker compose exec backend python -m app.cli backfill curated-status   # forvent 16 include
docker compose exec backend python -m app.cli backfill status           # forvent PENDING = 16
```

`C20190977160` skal stadig stå som `REJECTED` og **uden** override. Den
kræver ingen handling overhovedet:

```bash
docker compose exec db psql -U maritim -d maritim -c \
  "SELECT accession_number, status FROM backfill_manifest_items WHERE accession_number = 'C20190977160';"
docker compose exec db psql -U maritim -d maritim -c \
  "SELECT count(*) FROM curated_relevance_overrides WHERE accession_number = 'C20190977160';"  -- forvent 0
```

---

## 8. Kør backfill

```bash
docker compose exec backend python -m app.cli backfill run \
  --source production --batch-size 25
```

Høsteservicen har åbningstid 03:00–23:45. Rammer du det lukkede vindue, går
posterne i RETRY og forsøges automatisk igen (rettet i en tidligere patch).

---

## 9. Statuskontrol EFTER

```bash
docker compose exec backend python -m app.cli backfill status
```

Forventet: de 16 er `COMPLETED`, ikke `REJECTED`.

```bash
# Alle 16 skal nu være gemt med is_maritime = true...
docker compose exec db psql -U maritim -d maritim -c \
  "SELECT source_id, is_maritime, maritime_score
     FROM documents
    WHERE source_id IN (
      'A20160155830','A20190050030','B20070023205','B20100172605',
      'B20160089205','B20170013705','B20221002905','B20221025905',
      'B20221026005','B20230913105','B20230947605','B20230956305',
      'B20230970105','B20230980105','B20230989605','B20240913905')
    ORDER BY source_id;"
```

**Vigtigt at bemærke i outputtet:** `maritime_score` skal stadig vise de
oprindelige, lave automatiske scorer (under 60). Det er korrekt og hele
pointen — overriden ændrer kun `is_maritime`, aldrig motorens egen udregning.
Ser du scorerne omskrevet til 60+, er noget galt.

Revisionssporet:

```bash
docker compose exec backend python -m app.cli backfill curated-history
docker compose exec backend python -m app.cli backfill curated-history --accession B20070023205
```

---

## Hvis et allerede importeret dokument skal ekskluderes

Det var det tilfælde, `--requeue-rejected` ikke dækkede: et dokument, hvis
købost allerede er `COMPLETED`, ville beholde sin gamle `is_maritime = true`
indtil en tilfældig senere genimport. Brug `--requeue-completed`:

```bash
docker compose exec backend python -m app.cli backfill enqueue \
  --id C20190977160 \
  --tag curated-relevance-2026-08 \
  --curated-exclude \
  --curated-reason "Generel regulering om køleanlæg og varmepumper — ikke maritim" \
  --requeue-completed

docker compose exec backend python -m app.cli backfill run --source production
```

Flaget afvises med exit-kode 2, hvis der ikke samtidig gives
`--curated-include`/`--curated-exclude`, og rammer kun de numre, der står i
`--id`/`--file`.

> Dette er **ikke** nødvendigt for de 16 i denne omgang — de står som
> `REJECTED`, ikke `COMPLETED`, og dækkes af `--requeue-rejected`.
