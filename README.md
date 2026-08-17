# Maritim Lovdatabase

Lokal, søgbar database over **dansk maritim lovgivning**, bygget på data fra
[Retsinformation](https://www.retsinformation.dk).

Systemet høster lovdokumenter fra Retsinformation, normaliserer dem, vurderer
om de er maritimt relevante, kategoriserer dem i en maritim taksonomi,
versionerer dem lokalt og gør dem søgbare.

> **Retsinformation er den officielle retskilde.** Denne tjeneste er et lokalt
> søge- og analyseværktøj. Kontrollér altid den gældende officielle tekst på
> Retsinformation ved juridisk anvendelse.

---

## Indhold

1. [Hvad systemet gør](#1-hvad-systemet-gør)
2. [Arkitektur](#2-arkitektur)
3. [Teknologivalg](#3-teknologivalg)
4. [Mappestruktur](#4-mappestruktur)
5. [Kom i gang med Docker](#5-kom-i-gang-med-docker)
6. [Kom i gang uden Docker](#6-kom-i-gang-uden-docker)
7. [Miljøvariabler](#7-miljøvariabler)
8. [Database og migrationer](#8-database-og-migrationer)
9. [Fixturdata](#9-fixturdata)
10. [Retsinformation-connectoren](#10-retsinformation-connectoren)
11. [Importeren](#11-importeren)
12. [Maritim relevansvurdering](#12-maritim-relevansvurdering)
13. [Kategorisering](#13-kategorisering)
14. [Versionering](#14-versionering)
15. [Søgning](#15-søgning)
16. [Semantisk søgning (vektorer)](#16-semantisk-søgning-vektorer)
17. [Strukturel parsing, titler og domænejusteret rangering](#17-strukturel-parsing-titler-og-domænejusteret-rangering)
18. [REST-API](#18-rest-api)
19. [Frontend](#19-frontend)
20. [Test](#20-test)
21. [Måling af søgekvalitet](#21-måling-af-søgekvalitet)
22. [Sikkerhed og adgangskontrol](#22-sikkerhed-og-adgangskontrol)
23. [Offentlig udgivelse](#23-offentlig-udgivelse)
24. [Kendte begrænsninger](#24-kendte-begrænsninger)
25. [Fremtidige udvidelsespunkter](#25-fremtidige-udvidelsespunkter)

---

## 1. Hvad systemet gør

```
Retsinformation
      ↓  hent dokumentliste og fuldtekst
Normalisering            → ensartede felter, uafhængigt af kildens format
      ↓
Maritim relevansvurdering → score 0–100 med fuld begrundelse
      ↓
Kategorisering            → maritim taksonomi, flere kategorier pr. dokument
      ↓
Versioneret database      → historik bevares, aldrig overskrivning
      ↓
Strukturel parsing        → kapitel, afsnit, paragraf, stk. — paragraffen er enheden
      ↓
Vektorisering             → én paragraf = én vektorpost, med kapitelkontekst
      ↓
Søge-API                  → ord, betydning eller begge + facetfiltre,
                            domænejusteret rangering, paragrafhit
      ↓
Webgrænseflade            → søgning, læsevisning af lovtekst, drift
```

Retsinformation klassificerer ikke lovgivning efter en maritim taksonomi.
Systemets kerneopgave er derfor at afgøre **hvad der er maritimt relevant**,
og at kunne **forklare hvorfor** — systemet arbejder med lovgivning, hvor en
sort boks er ubrugelig.

Søgningen kan både finde **ordene** og **betydningen**. En maskinmester der
søger efter *livbåde*, skal også have *Bekendtgørelse om redningsmidler i
handelsskibe*, selv om ordet ikke står der. Se
[Semantisk søgning](#16-semantisk-søgning-vektorer).

Systemet er ikke en generisk søgemaskine over maritime dokumenter, men en
**faglig maritim lovdatabase**. Det har to konsekvenser, som gennemsyrer
resten:

* **Enheden er paragraffen.** Et søgeresultat peger på *Kapitel 2 — Hviletid
  · § 3*, ikke på "et sted i dokumentet". Det er den henvisning, en praktiker
  skal bruge videre.
* **Rangeringen er domænestyret.** Ved en bred søgning som *hviletid* skal
  hovedreglen for søfarende stå før en særregel om hviletid for lodser i
  grønlandske farvande — også selv om særreglen nævner ordet lige så direkte.
  Søger brugeren derimod på *grønlandske lodser hviletid*, skal særreglen stå
  øverst. Se
  [afsnit 17](#17-strukturel-parsing-titler-og-domænejusteret-rangering).

---

## 2. Arkitektur

Ansvaret er delt i tjenester med hver sin grænseflade:

```
backend/app/services/
├── retsinformation/   Kildeadgang og normalisering
│   ├── base.py          SourceClient (Protocol) + NormalizedDocument
│   ├── production.py    ProductionRetsinformationClient (officielt API)
│   ├── fixture.py       FixtureRetsinformationClient (syntetiske testdata)
│   ├── xml_parser.py    Tolerant ELI-XML-parser
│   ├── normalization.py Dokumenttyper, status, datoer, myndighed
│   └── factory.py       Eksplicit kildevalg — aldrig stiltiende fallback
├── relevance/         Maritim relevansvurdering
│   ├── base.py          RelevanceEngine (Protocol) + RelevanceResult
│   └── keyword_engine.py KeywordRelevanceEngine
├── categorization/    Maritim taksonomi
│   ├── base.py          CategorizationEngine (Protocol)
│   └── keyword_categorizer.py
├── importer/          Orkestrering og persistering
│   ├── service.py       ImportService — kalder de øvrige tjenester
│   └── repository.py    Persistering, versionering, søgeindeks
├── discovery/         Opdagelse af kandidat-accessionsnumre
│   ├── base.py          DiscoveryClient (Protocol) + DiscoveryHit
│   ├── extract.py       Tolerant udtræk af et udokumenteret søgesvar
│   ├── search_client.py RetsinformationSearchClient (konfigureret endpoint)
│   ├── fixture.py       FixtureDiscoveryClient (syntetiske søgeresultater)
│   ├── manifest_csv.py  CSV mellem opdagelse og kø — menneskelig gennemgang
│   └── service.py       Delsøgninger, dubletfjernelse og tælleprøve
├── backfill/          Historisk efterindlæsning via accessionsnumre
│   ├── manifest.py      Kø, reservation (lease) og fencing token
│   └── worker.py        Portionsvis kørsel gennem ImportService
├── embedding/         Vektorisering af lovtekst og søgninger
│   ├── base.py          EmbeddingProvider (Protocol) + ProviderInfo
│   ├── chunking.py      Opdeling ved kapitel-, §- og stykkegrænser
│   ├── local.py         sentence-transformers på CPU (standard)
│   ├── remote.py        OpenAI-kompatibelt endpoint
│   ├── hashing.py       Deterministisk hash — IKKE semantisk, kun test
│   ├── factory.py       Eksplicit udbydervalg — aldrig stiltiende fallback
│   └── service.py       EmbeddingIndexer — bygger og vedligeholder indekset
├── search/            Søgning
│   ├── base.py          SearchBackend (Protocol) + SearchQuery
│   ├── backends.py      PostgresSearchBackend + FallbackSearchBackend
│   ├── vector.py        VectorSearchBackend (pgvector eller portabel)
│   ├── hybrid.py        HybridSearchBackend — Reciprocal Rank Fusion
│   └── query_log.py     Logning og vektorisering af søgninger
└── matching.py        Fælles termmatchning for relevans og kategorisering
```

To principper bærer designet:

**Kilden er isoleret.** Resten af applikationen kender kun
`NormalizedDocument` og `SourceClient`. Ingen anden kode ser Retsinformations
JSON- eller XML-strukturer.

**Motorerne kan udskiftes.** `RelevanceEngine`, `CategorizationEngine` og
`EmbeddingProvider` er protokoller. En senere `HybridAIRelevanceEngine` eller en
anden embedding-model kan indsættes uden ændringer i importer, persistering
eller API.

**Vektorisering er adskilt fra import.** Importeren henter og gemmer
lovteksten; `EmbeddingIndexer` bygger indekset over den bagefter. En import må
ikke tage timer længere eller kunne fejle, fordi en model ikke kunne indlæses.

---

## 3. Teknologivalg

| Lag | Valg | Begrundelse |
|---|---|---|
| Backend | Python 3.12 + FastAPI | Typede skemaer, automatisk OpenAPI-dokumentation |
| ORM | SQLAlchemy 2.0 | Understøtter både PostgreSQL og SQLite fra samme model |
| Migrationer | Alembic | Reproducerbart skema; ingen `create_all` i drift |
| Database | PostgreSQL 16 | Indbygget dansk fuldtekstsøgning (`to_tsvector('danish', …)`) |
| Søgning | PostgreSQL FTS | Ingen grund til Elasticsearch ved dette datavolumen |
| Vektorsøgning | pgvector (HNSW) | Holder vektorerne i samme database — ingen separat søgeklynge |
| Embeddings | `intfloat/multilingual-e5-small` på CPU | Flersproget med dansk, 384 dim., kører lokalt uden nøgle |
| Sammensmeltning | Reciprocal Rank Fusion | De to scorer er ikke sammenlignelige størrelser; kun rækkefølgen er |
| Frontend | React 18 + Vite | Lille, hurtig, uden unødige afhængigheder |
| Konfiguration | YAML + pydantic-settings | Domæneviden i konfiguration, ikke i kode |

Frontenden bruger **ingen router-pakke**. Applikationen har tre sider, og en
hash-baseret router på 30 linjer koster mindre end afhængigheden.

---

## 4. Mappestruktur

```
maritime-law/
├── backend/
│   ├── app/
│   │   ├── api/            Ruter, serialisering, fejlhåndtering, middleware
│   │   ├── core/           Konfiguration, logging, adgangskontrol, tekstbehandling
│   │   ├── db/             Session, seeding, migrationskørsel
│   │   ├── models/         SQLAlchemy-modeller
│   │   ├── schemas/        Pydantic-svarskemaer
│   │   ├── services/       Forretningslogik (se ovenfor)
│   │   │   ├── discovery/  Opdagelse af accessionsnumre + CSV-manifest
│   │   │   ├── backfill/   Kø og arbejder til historisk efterindlæsning
│   │   │   ├── legal/      Strukturel parsing (kapitel/§/stk.) og visningstitler
│   │   │   ├── ranking/    law_class, query intent og den vægtede scoremodel
│   │   │   └── embedding/  Chunking, embedding-udbydere, indeksering
│   │   ├── cli.py          Kommandolinjegrænseflade
│   │   └── main.py         FastAPI-applikationen
│   ├── migrations/         Alembic
│   ├── tests/              584 tests
│   ├── requirements.txt
│   ├── requirements-embedding.txt   Lokal model (ca. 1,5 GB, valgfri)
│   └── Dockerfile
├── frontend/
│   ├── src/
│   │   ├── components/     Genbrugte visningskomponenter
│   │   ├── lib/            API-klient, formatering, routing
│   │   └── pages/          Søgning, dokument, import/drift
│   ├── nginx.conf              Proxy + rate limiting
│   ├── security-headers.conf   CSP og øvrige sikkerhedsheadere
│   └── Dockerfile
├── config/
│   ├── maritime_keywords.yaml   Termer og vægte for relevansmotoren
│   ├── categories.yaml          Maritim taksonomi
│   └── ranking.yaml             Nichegrupper, kernelovsmønstre, vægte og domæneregler
├── data/fixtures/               Syntetiske testdokumenter og søgeresultater
├── manifests/                   CSV-manifester fra `backfill discover`
├── docs/                        Udrulnings- og designnoter
├── scripts/
│   ├── verify_api.py            Integrationsverifikation
│   └── deploy_check.sh          Kontrol af .env før offentlig udgivelse
├── docker-compose.yml
├── docker-compose.tunnel.yml    Offentlig udgivelse via Cloudflare Tunnel
├── .env.example
└── Makefile
```

---

## 5. Kom i gang med Docker

```bash
cp .env.example .env
cd backend && python -m app.cli admin-token   # token til import og drift
# skriv resultatet i .env som ADMIN_API_TOKEN=...
cd .. && docker compose up --build
```

Dette starter PostgreSQL (med pgvector), backend og frontend. Migrationer
køres automatisk, og den maritime taksonomi seedes ved opstart.

`ADMIN_API_TOKEN` er ikke valgfri i praksis: uden den svarer import,
vektorisering og driftstal `503`. Systemet er lukket som udgangspunkt —
se [afsnit 22](#22-sikkerhed-og-adgangskontrol). Alle porte bindes til
`127.0.0.1`; sæt `BIND_ADDRESS=0.0.0.0` i `.env`, hvis du bevidst vil nå
systemet fra en anden maskine på dit eget net.

Første bygning tager længere tid end man forventer: embedding-modellen bages
ind i backend-imaget, så containeren kan vektorisere uden netværk. Det koster
omkring 1,5 GB. Skal det undgås:
`docker compose build --build-arg WITH_EMBEDDINGS=false` og
`EMBEDDINGS_ENABLED=false` i `.env` — da kører systemet med ordsøgning alene.

| Tjeneste | Adresse |
|---|---|
| Frontend | http://localhost:8080 |
| API | http://localhost:8000 |
| API-dokumentation | http://localhost:8000/docs |
| Systemtilstand | http://localhost:8000/health |

Databasen er tom efter første opstart. Hent data ind:

```bash
# Via brugerfladen: åbn http://localhost:8080/#/drift, indsæt
# ADMIN_API_TOKEN, og klik "Kør import nu".
# Eller fra kommandolinjen:
docker compose exec backend python -m app.cli import --source production

# Byg derefter det semantiske indeks. Bevidst adskilt fra importen —
# se afsnit 16.
docker compose exec backend python -m app.cli embed run
docker compose exec backend python -m app.cli embed status
```

Opgraderes en eksisterende installation, skal visningstitler og
rangeringssignaler beregnes for de dokumenter, der allerede ligger i basen —
migration `0005` opretter kolonnerne, men kan ikke fylde dem:

```bash
docker compose exec backend python -m app.cli ranking reclassify
docker compose exec backend python -m app.cli embed run --reset   # ændrede stykkegrænser
```

Søg derefter efter `brand passagerskib` i frontenden. Prøv også `livbåde` med
tilstanden **Betydning** — den finder bekendtgørelser om redningsmidler, selv
om ordet ikke står i dem.

---

## 6. Kom i gang uden Docker

Systemet kører på SQLite uden PostgreSQL — praktisk til udvikling.

```bash
# Backend
pip install -r backend/requirements.txt
export DATABASE_URL="sqlite:///./data/maritime.db"

# Valgfrit: den lokale embedding-model (ca. 1,5 GB). Uden den kører
# systemet leksikalsk — sæt da EMBEDDINGS_ENABLED=false.
pip install --extra-index-url https://download.pytorch.org/whl/cpu \
    -r backend/requirements-embedding.txt

cd backend
python -m app.cli migrate                 # opret skema
python -m app.cli import --source production # hent officielle ændringer
python -m app.cli embed run               # byg det semantiske indeks
python -m uvicorn app.main:app --reload   # http://localhost:8000

# Frontend (nyt terminalvindue)
cd frontend
npm install
npm run dev                               # http://localhost:5173
```

Vite sender `/api`-kald videre til `http://localhost:8000`.

`make help` viser alle tilgængelige kommandoer.

---

## 7. Miljøvariabler

Alle findes i `.env.example`. De vigtigste:

| Variabel | Standard | Betydning |
|---|---|---|
| `DATABASE_URL` | SQLite-fil | PostgreSQL i produktion, SQLite til udvikling |
| `SOURCE_CLIENT` | `production` | Normal drift bruger Retsinformations officielle høsteservice |
| `RETSINFORMATION_BASE_URL` | `https://api.retsinformation.dk` | Officiel høsteservice |
| `RETSINFORMATION_MIN_REQUEST_INTERVAL_SECONDS` | `10` | Kildens dokumenterede grænse. Sænk ikke uden aftale |
| `IMPORT_STORE_MIN_SCORE` | `30` | Dokumenter under denne score gemmes ikke |
| `RUN_MIGRATIONS_ON_STARTUP` | `true` | Praktisk i Docker |
| `LOG_LEVEL` | `INFO` | |
| `ADMIN_API_TOKEN` | *(tom)* | Kræves af import, vektorisering, driftstal og søgelog. Tom = de svarer 503 |
| `ENVIRONMENT` | `development` | `production` kræver et token på mindst 24 tegn for at starte |
| `RATE_LIMIT_REQUESTS_PER_MINUTE` | `120` | Kvote pr. klient-IP for almindelige API-kald |
| `RATE_LIMIT_SEARCH_PER_MINUTE` | `30` | Strammere kvote for søgning |
| `TRUST_PROXY_HEADERS` | `false` | Brug `CF-Connecting-IP`/`X-Forwarded-For`. Kun bag en proxy du selv kontrollerer |
| `EXPOSE_API_DOCS` | `true` | `/docs`, `/redoc`, `/openapi.json`. Sæt `false` ved offentlig adgang |
| `BIND_ADDRESS` | `127.0.0.1` | Hvilken adresse Docker binder porte til |
| `CORS_ORIGINS` | localhost | Tom ved offentlig udgivelse. `*` afvises i produktion |
| `TUNNEL_TOKEN` | *(tom)* | Kun til `docker-compose.tunnel.yml` |

**Retsinformations høsteservice kræver ingen API-nøgle.** Der er derfor
bevidst ingen `RETSINFORMATION_API_KEY`.

### Skift fra testdata til officielle data

Hvis den lokale database allerede indeholder fixtures, bliver de ikke fjernet
ved blot at ændre `SOURCE_CLIENT`. Når databasen kun indeholder testdata, kan
du nulstille den én gang og derefter importere fra Retsinformation:

```bash
# Docker: sletter den lokale PostgreSQL-volume og alle importerede dokumenter
docker compose down -v
docker compose up --build -d
docker compose exec backend python -m app.cli import --source production
```

Kør kun nulstillingen, når de eksisterende lokale data må slettes. Ved lokal
SQLite-kørsel slettes i stedet `data/maritime.db`, før migration og
produktionsimport køres igen. Fixtures er fortsat tilgængelige eksplicit til
automatiske tests, men vælges ikke i normal drift eller i brugerfladen.

---

## 8. Database og migrationer

Ni tabeller:

| Tabel | Indhold |
|---|---|
| `documents` | Det logiske dokument: identitet, metadata, maritim klassifikation |
| `document_versions` | Uforanderlige indholdsversioner med SHA-256-hash |
| `categories` | Maritim taksonomi, seedet fra `config/categories.yaml` |
| `document_categories` | Mange-til-mange med `confidence` og matchede termer |
| `import_runs` | Én række pr. importkørsel med tællinger og fejl |
| `change_log` | `CREATED`, `CONTENT_UPDATED`, `METADATA_UPDATED`, `STATUS_CHANGED` |
| `backfill_manifest_items` | Kø til [historisk efterindlæsning](#historisk-efterindlæsning-backfill) med reservation og fencing token |
| `document_chunks` | Lovteksten delt i stykker, hvert med sin vektor — grundlaget for [betydningssøgning](#16-semantisk-søgning-vektorer) |
| `search_queries` | De søgninger der faktisk stilles, vektoriseret. Ingen bruger-, IP- eller sessionsoplysninger |

Fem migrationer:

| Migration | Indhold |
|---|---|
| `0001_initial` | Version 1-skemaet |
| `0002_backfill_manifest` | Efterindlæsningskøen |
| `0003_curated_relevance_overrides` | Kuraterede relevansafgørelser og deres historik |
| `0004_semantic_search` | `document_chunks`, `search_queries` og pgvector-kolonnerne |
| `0005_structural_ranking` | `display_title`, `law_class`, `scope_score`, `authority_score` og paragrafadressen på hvert stykke |

Efter `0005` skal eksisterende dokumenter genberegnes — se
[afsnit 17.6](#176-genberegning).

```bash
cd backend
python -m app.cli migrate      # eller: alembic upgrade head
alembic downgrade -1           # rul én migration tilbage
alembic revision --autogenerate -m "beskrivelse"
```

Migrationen tilføjer `search_vector` (tsvector) og GIN-indeks **kun på
PostgreSQL**, da SQLite ikke har typen. Begge databaser deler den portable
`search_text`-kolonne.

Skemaet oprettes aldrig med `create_all` i drift — kun via migrationer, så
det udrullede skema altid svarer til det testede.

---

## 9. Fixturdata

`data/fixtures/` indeholder **18 syntetiske dokumenter**: 15 maritime og 3
klart ikke-maritime (folkeskole, dagtilbud, luftfartøjsvedligeholdelse).
Luftfartsdokumentet er med med vilje — det nævner "besætning", "fartøj" og
"certifikat" og afprøver derfor modstanden mod falske positiver.

> **Fixturdokumenterne er konstruerede.** De er ikke hentet fra
> Retsinformation og er ikke gældende ret. Hvert dokument bærer
> `is_synthetic=True` hele vejen fra kilde til brugerflade, hvor det vises som
> en advarsel og et "Testdata"-mærke.

To revisioner demonstrerer versionering:

| Revision | Ændring | Forventet resultat |
|---|---|---|
| 1 | Grundsæt, 18 dokumenter | 15 oprettet, 3 afvist |
| 2 | Ny § 5 a i brandbekendtgørelsen | `CONTENT_UPDATED`, version 2 |
| 2 | Lodspligt ændret til "Ophævet" | `STATUS_CHANGED`, ingen ny version |
| 2 | Nyt dokument om havnemodtageanlæg | `CREATED`, version 1 |

---

## 10. Retsinformation-connectoren

### Verificeret grundlag

Produktionsklienten er bygget udelukkende på officiel dokumentation:
[Retsinformation høsteservice — REST API vejledning, v1.3 (13.09.2023)](https://www.retsinformation.dk/offentlig/vejledning/Retsinformation%20REST%20API%20vejledning.pdf),
Civilstyrelsen/Schultz.

| Forhold | Verificeret værdi |
|---|---|
| Base-URL | `https://api.retsinformation.dk` |
| Endpoint | `GET /v1/Documents` |
| Parameter | `date=ÅÅÅÅ-MM-DD`, højst 10 kalenderdage tilbage |
| Uden parameter | Seneste døgns ændringer, skæring kl. 03:00 |
| Autentifikation | Ingen |
| Åbningstid | 03:00–23:45. Udenfor → HTTP 400 |
| Rate limit | 1 kald pr. 10 sekunder. Overskridelse → HTTP 429 |
| Svarfelter | `documentId`, `accessionsnummer`, `reasonForChange`, `changeDate`, `documentType{shortName,id}`, `href`, `images[]` |
| Fuldtekst | ELI-XML: `https://www.retsinformation.dk/eli/accn/{accn}/xml` |

Klienten håndhæver rate limit trådsikkert, forsøger igen ved timeout, 429 og
5xx med eksponentiel backoff, og forsøger **ikke** igen ved permanente 4xx.
Ved HTTP 400 uden for åbningstiden tilføjes en forklarende besked.

### Aldrig stiltiende fallback

`build_source_client()` afviser ukendte og tomme værdier frem for at gætte.
Kun `None` betyder "brug konfigurationens standard".

```python
build_source_client("production")   # ProductionRetsinformationClient
build_source_client("fixture")      # FixtureRetsinformationClient + advarsel i loggen
build_source_client("produktion")   # UnknownSourceClientError
```

Fejler produktionskilden, fejler importen. Der leveres aldrig syntetiske
dokumenter som erstatning.

---

## 11. Importeren

For hvert kildedokument:

```
hent dokument → normalisér → vurdér relevans → afvis under tærskel
              → kategorisér → beregn hash → sammenlign med lokal version
              → opret eller opdatér → gem version → opdatér søgeindeks
              → skriv ændringslog
```

Hvert dokument behandles i sin **egen transaktion**. Fejler ét dokument,
rulles netop dét tilbage, fejlen registreres på kørslen, og behandlingen
fortsætter. Fejler mange i træk (standard 25), afbrydes kørslen — så er kilden
nede, ikke dokumentet.

Dokumenter under `IMPORT_STORE_MIN_SCORE` gemmes ikke. Databasen forbliver en
maritim samling frem for en kopi af hele lovsamlingen. Antallet af afviste
dokumenter registreres på kørslen.

```bash
cd backend
python -m app.cli import --source fixture
python -m app.cli import --source fixture --fixture-revision 2
python -m app.cli import --source production --since 2026-08-01
python -m app.cli import --source production --limit 50
```

Importen kan også startes via API'et (`POST /api/import/run`) eller fra
brugerfladens driftsside.

**Planlægning:** Version 1 kører importen manuelt. Servicen er statuløs mellem
kørsler og kan lægges bag cron, en worker eller en planlagt container uden
ændringer.

### Opdagelse af accessionsnumre (discover)

Efterindlæsningskøen løser genoptagelse og samtidighed — ikke *opdagelse*.
Numrene skal komme et sted fra. Retsinformations egen søgeside kan filtrere
på `administrerendeMyndighed = Søfartsstyrelsen`, og det er den autoritative
første afgrænsning. Manuelt verificeret på Retsinformation 13.08.2026:

| Filter | Antal |
| --- | ---: |
| Kun gældende | 606 |
| Kun historisk | 2.281 |
| Begge | **2.887** |

Til sammenligning gav en global sitemap-scanning 134.044 dokumenter — en
reduktion på over 97 %. Sitemappen bruges derfor kun til verificering og
fallback, ikke som kø.

**Søgegrænsefladen er ikke en dokumenteret API.** Søgesiden er en
JavaScript-applikation, og det endpoint dens frontend kalder, indgår ikke i
høsteservicens dokumentation. Der står derfor **ingen søge-URL i koden**.
Den skal aflæses én gang og sættes i `.env`:

1. Åbn <https://www.retsinformation.dk/documents> og udviklerværktøjernes
   netværksfane.
2. Søg med `administrerendeMyndighed = Søfartsstyrelsen`.
3. Aflæs anmodningens URL, metode og parametre.
4. Sæt `RETSINFORMATION_SEARCH_URL`, `_METHOD` og `_PARAMS` i `.env`.

Kontrollér så svaret med ét enkelt kald:

```bash
cd backend
python -m app.cli backfill probe-search --out probe.json
```

`probe-search` henter præcis én side og udskriver, om svaret bærer
accessionsnumre, hvilke pagineringsnøgler det har, og hvad udtrækket får ud
af den første post. Det er forundersøgelsen — den skal se rigtig ud, før
`discover` bruges.

Derefter bygges manifestet:

```bash
python -m app.cli backfill discover \
    --authority Søfartsstyrelsen \
    --out manifests/soefartsstyrelsen.csv
```

`discover` kører gældende og historiske som to adskilte søgninger, fjerner
dubletter på accessionsnummer og **kontrollerer tallene**: 606 + 2.281 =
2.887. Afviger noget — for få resultater, en delsøgning der ramte sideloftet,
eller et resultattal fra kilden der er højere end det hentede — standser
kommandoen **før CSV'en skrives**. Et manifest der stille mangler 400
dokumenter, er farligere end intet manifest; fejlen ville først vise sig som
huller i databasen måneder senere. Forventningerne kan justeres med
`--expect-current` / `--expect-historical` (`0` slår kontrollen fra), og
`--allow-count-mismatch` skriver filen alligevel med en advarsel øverst.

CSV'en har faste kolonner i fast rækkefølge og er sorteret på
accessionsnummer, så to opdagelser kan sammenlignes linje for linje:

```
accession_number,title,authority,status,document_type,published_date,
eli_url,source_query,discovered_at,decision
```

**`discover` lægger aldrig noget i køen.** Filen gennemgås manuelt —
kolonnen `decision` sættes til noget andet end `include` ud for det, der
ikke skal med — og først derefter:

```bash
python -m app.cli backfill enqueue-manifest \
    --file manifests/soefartsstyrelsen.csv \
    --tag soefartsstyrelsen-historical-2026
```

`--dry-run` viser hvad der ville blive lagt i kø. Hele kæden kan afprøves
uden netværk med `--source fixture`, som bruger de syntetiske
søgeresultater i `data/fixtures/discovery_soefartsstyrelsen.json`.

### Historisk efterindlæsning (backfill)

Ændringsfeeden rækker kun ti dage tilbage (se
[kendte begrænsninger](#19-kendte-begrænsninger)). Ældre lovgivning kan
udelukkende hentes ved at slå bestemte accessionsnumre op. Det kræver en
arbejdsliste, og listen skal kunne genoptages: en efterindlæsning af nogle
tusinde dokumenter kører i timer, bliver afbrudt, og skal fortsætte hvor den
slap.

Arbejdslisten er tabellen `backfill_manifest_items` — én række pr.
accessionsnummer med en tilstand:

```
PENDING ──reserveres──> PROCESSING ──┬──> COMPLETED   (hentet og gemt)
   ▲                                 ├──> REJECTED    (under maritim tærskel)
   │                                 ├──> RETRY       (midlertidig fejl)
   └───────── frigivet ──────────────┴──> FAILED      (permanent fejl / forsøg brugt)
```

```bash
cd backend

# 1. Læg accessionsnumre i køen (idempotent — dubletter springes over)
python -m app.cli backfill enqueue --file accessions.txt --tag sofart-2024
python -m app.cli backfill enqueue --id B20220122005 --id B20190094605

# 2. Kør køen igennem
python -m app.cli backfill run --source production --batch-size 25

# 3. Se hvor langt vi er nået
python -m app.cli backfill status --tag sofart-2024
```

`--file` tager ét accessionsnummer pr. linje; `#` starter en kommentar.
Kørslen kan afbrydes og genoptages: reserverede poster falder tilbage i køen,
når deres reservation udløber.

**Fejl skelnes efter om det nytter at prøve igen.** En timeout eller HTTP 503
giver `RETRY` med eksponentiel ventetid (5 min → 15 min → 45 min, loft 6 timer).
Et dokument, kilden ikke har, giver `FAILED` med det samme — flere forsøg ændrer
ikke, at det ikke findes. Efter `--max-attempts` forsøg (standard 3) opgives
posten. Opgivne poster kan sættes tilbage i køen:

```bash
python -m app.cli backfill enqueue --id B20220122005 --requeue-failed
```

**Flere arbejdere kan dele køen.** En arbejder *reserverer* poster: status
sættes til `PROCESSING` med et unikt `claim_token` og en udløbstid
(`--lease-minutes`, standard 20). Udløber reservationen — arbejderen er død
eller hængt — må en anden tage posten.

Hele portionen reserveres på én gang, så **levetiden skal overstige
behandlingstiden for en hel portion**, ikke for ét dokument: `--batch-size`
dokumenter, plus kildens rate limiting, plus de interne genforsøg i
`ProductionRetsinformationClient`. Sættes den for lavt, stjæler arbejdere
poster fra hinanden under helt normal drift, og de langsomme arbejderes
resultater bliver kasseret af fencing token.

Den første arbejder kan imidlertid stadig være i gang. Derfor har **enhver
efterfølgende statusskrivning `claim_token` i WHERE-klausulen** (et *fencing
token*). Rammer skrivningen nul rækker, har arbejderen mistet posten, og
resultatet droppes med en `backfill.fence.breach`-advarsel i loggen frem for at
overskrive den nye ejers tilstand.

To databasemekanismer bærer reservationen:

| Mekanisme | PostgreSQL | SQLite |
|---|---|---|
| `SELECT ... FOR UPDATE SKIP LOCKED` | ja | findes ikke |
| `UPDATE ... WHERE status = :forventet AND claim_token = :forrige` | ja | ja |

Den betingede `UPDATE` er den portable garanti: ændres rækken mellem `SELECT` og
`UPDATE`, rammer opdateringen nul rækker, og kandidaten springes over. Det er
dækket af en test, der stjæler rækken i netop det vindue
(`test_claim_is_skipped_when_row_changes_between_select_and_update`).

**Dokumenttabellerne beskyttes ikke af tokenet — de behøver det ikke.**
`DocumentRepository` sammenligner indholdshash, før en version skrives.
Behandler to arbejdere ved et uheld samme accessionsnummer, giver den anden
`UNCHANGED` og skriver ingen ekstra række i `document_versions`. Fencing token
beskytter *køens* tilstand; dokumentlaget er idempotent i forvejen.

**Portioner frem for enkeltdokumenter.** Arbejderen reserverer `--batch-size`
poster og kører dem gennem *én* `ImportService.run(explicit_ids=[...])`. Ét
importkald pr. dokument ville give én `import_runs`-række pr. dokument og gøre
importhistorikken ubrugelig. `ImportSummary.outcomes` fortæller derefter hvad
der skete med hvert enkelt kilde-id, og hver kø-post får `import_run_id` sat, så
den kan spores tilbage til den kørsel, der behandlede den.

**En fejlet importkørsel stopper arbejderen.** En kørsel ender som `FAILED`,
hvis kildelisten ikke kunne bygges, eller hvis for mange dokumenter fejlede i
træk. Begge dele betyder, at kilden er nede.

`ImportService.run()` *returnerer* i det tilfælde en `FAILED`-opsummering uden
udfald frem for at kaste. Behandles det som "posterne blev bare ikke nået", og
frigives de til `PENDING`, reserverer arbejderen dem straks igen og kører i ring
— uden at forsøgstælleren nogensinde løber op. Derfor gælder to regler:

1. Portionens ubehandlede poster sættes i `RETRY` med ventetid, så de bruger et
   forsøg og til sidst opgives.
2. Arbejderen stopper. `backfill run` skriver hvorfor og returnerer exitkode 1,
   så en cron-kørsel kan opdage det.

Køen er uændret gyldig. Næste kørsel tager posterne op igen, når ventetiden er
udløbet.

---

## 12. Maritim relevansvurdering

Al domæneviden ligger i `config/maritime_keywords.yaml` — ingen termer er
hardcodet.

### Modellen

Dokumentet deles i fire felter med hver sin vægt:

| Felt | Vægt | Begrundelse |
|---|---|---|
| Titel | ×3,0 | Lovgivers egen emneangivelse — stærkeste enkeltsignal |
| Myndighed | ×2,5 | Søfartsstyrelsen som udsteder er stærkt indicium |
| Metadata | ×1,5 | Korttitel, type, nøgleord |
| Brødtekst | ×1,0 | Svagest, men bredest |

```
bidrag = min(forekomster, loft) × termvægt × feltvægt
```

**Loftet er afgørende.** Hver term tæller højst 3 gange pr. felt. Uden det
ville et dokument, der gentager "skib" 500 gange, ramme maksimum. Med loftet
scorer det 38, mens et dokument med syv forskellige maritime begreber nævnt én
gang hver scorer 64 — bredde er et bedre signal end gentagelse.

**Breddebonus:** termer hører til begreber (fartøj, besætning, miljø,
navigation …). Hvert begreb ud over det første giver +4, med loft på 20.

**Negative termer** (luftfart, jernbane, folkeskole) trækkes fra og dæmper
falske positiver.

**Normalisering** er mættende, så skalaen forbliver meningsfuld i hele
intervallet:

```
score = 100 × rå / (rå + 45)
```

**Titelautoritet:** står en utvetydig maritim term i titlen — "passagerskib",
"SOLAS", "søfarende" — sættes et gulv på 70. Uden denne regel ville et
dokument med kort eller manglende brødtekst kunne falde under tærsklen alene
på grund af tekstlængde. Gulvet anvendes ikke, hvis titlen samtidig indeholder
en negativ term, så "skibsfart og luftfart" ikke løftes kunstigt.

### Tærskler

| Score | Klassifikation |
|---|---|
| 0–29 | Ikke maritimt |
| 30–59 | Mulig maritim relevans |
| 60–100 | Maritimt |

### Forklarbarhed

Hvert dokument bærer hele regnestykket, tilgængeligt på
`GET /api/documents/{id}` og vist i brugerfladen:

- score, klassifikation og anvendte tærskler
- bidrag pr. felt
- hver matchet term med felt, antal forekomster, om loftet blev nået,
  termvægt, feltvægt og bidrag
- negative signaler separat
- breddebonus og dækkede begreber
- om titelautoritetsreglen blev anvendt
- **hvilken dokumentversion vurderingen blev beregnet på**

Sidste punkt er væsentligt: ændres lovteksten, markeres vurderingen som
forældet (`is_stale`), indtil en ny import er kørt. Uden den binding kan en
klassifikation af en tekst, der siden er ændret, ikke efterprøves.

### Afprøv en titel uden at gemme noget

```bash
cd backend
python -m app.cli classify "Bekendtgørelse om sikkerhed på passagerskibe"
```

```
  Score          : 70/100  (maritime)
  Begrundelse    : Maritim terminologi i titel. Stærkeste termer: passagerskib.
                   Titlen indeholder utvetydig maritim terminologi …
  Regnestykke    : positiv 30.0 + breddebonus 0.0 - negativ 0.0 = 30.0 → 70
```

### Målte resultater på fixtursættet

| Dokumenttype | Score |
|---|---|
| 15 maritime dokumenter | 81–90 |
| Folkeskole, dagtilbud, luftfart | 0 |
| `"skib"` × 500 | 38 (loftet virker) |
| 7 forskellige begreber, 1 gang hver | 64 (bredde slår gentagelse) |
| "lodsejer" (grundejer) | 0 (matcher ikke "lods") |

---

## 13. Kategorisering

23 kategorier defineret i `config/categories.yaml`, blandt andet
Skibssikkerhed, Brandsikkerhed, Redningsmidler/LSA, Maskineri, Elektriske
installationer, Miljø/MARPOL, Besætning, Uddannelse/STCW, Navigation,
Radio/kommunikation, ISM, ISPS, Passagerskibe, Lastskibe, Fiskeskibe, Havne,
Certifikater og syn, Søulykker og Rederiets ansvar.

Et dokument kan tilhøre flere kategorier. Samme feltvægtede, loftbegrænsede
model som relevansmotoren; rå score omregnes til en `confidence` i 0,0–1,0.
Kategorier over 0,55 tildeles, højst 4 pr. dokument.

```
Bekendtgørelse om brandsikkerhed i passagerskibe
  → Brandsikkerhed (0,94) · Passagerskibe (0,94) · Maskineri (0,73) · Besætning (0,71)
```

Nåede ingen kategori tærsklen, tildeles `Andet maritimt`, så et maritimt
dokument aldrig ender ukategoriseret.

**Kontrakt:** kategoriseringen forudsætter, at dokumentet allerede er vurderet
maritimt relevant. Taksonomien beskriver emner *inden for* søfartsområdet.
Importeren kalder derfor kun motoren for dokumenter, der har bestået
relevanstærsklen.

---

## 14. Versionering

Historiske versioner overskrives aldrig.

| Situation | Resultat |
|---|---|
| Nyt dokument | Version 1, `CREATED` |
| Uændret indholdshash | **Ingen ny version** |
| Ændret indholdshash | Ny version, gammel bevaret, `CONTENT_UPDATED` |
| Kun ændret metadata | Ingen ny version, `METADATA_UPDATED` |
| Ændret status | Ingen ny version, `STATUS_CHANGED` |

Hashen er SHA-256 over normaliseret indhold: whitespace kollapses og Unicode
normaliseres, men store og små bogstaver bevares. Ren omformatering hos kilden
fremstår derfor ikke som en indholdsændring, mens enhver reel tekstændring gør.

```
Dokument
├── Version 1  (bevaret uændret)
└── Version 2  ← documents.current_version_id
                ← documents.relevance_version_id
```

Brugerfladen kan åbne enhver historisk version og viser tydeligt, at det ikke
er den aktuelle tekst.

---

## 15. Søgning

**PostgreSQL** (produktion) bruger fuldtekstsøgning med vægtet tsvector:

| Vægt | Indhold |
|---|---|
| A | Titel, korttitel, dokumentnummer |
| B | Myndighed, dokumenttype |
| C | Kategorinavne |
| D | Brødtekst |

Forespørgsler bruger `websearch_to_tsquery('danish', …)`, der forstår citater,
`OR` og `-udeluk` og ikke fejler på skæve input. Rangering med `ts_rank_cd`.
Indekset opdateres i samme metode som skriver dokumentet, så det aldrig kommer
ud af trit ved ændring af titel, indhold, myndighed eller kategorier.

**SQLite** (udvikling og test) bruger en portabel token-søgning mod
`search_text`, med samme titelvægtning. Backend vælges automatisk ud fra
databasen.

Begge søgefelter gemmes **foldet** (æ→ae, ø→oe, å→aa, små bogstaver), og
søgetermer foldes tilsvarende. Uden det ville "Søfartsstyrelsen" og
"søulykke" ikke kunne findes på SQLite.

### Filtre

Kategori, dokumenttype, myndighed, status, dokumentnummer, dato fra/til,
maritim score fra/til, maritim klassifikation og **dokumentklasse**
(`law_class=kernelaw|speciallaw|support`, se [afsnit 17](#17-strukturel-parsing-titler-og-domænejusteret-rangering)).
Alle håndhæves i API'et, ikke kun visuelt i frontenden. `/api/facets` leverer
de tilgængelige værdier, så brugerfladen ikke hardcoder dem.

Uden en søgestreng er relevanssortering en **gennemsynsliste**, og da sorteres
der domænejusteret direkte i SQL: kernelove før speciallove før
støttedokumenter, gældende før historisk, derefter maritim score og
anvendelsesbredde. Det sker i SQL og ikke i Python, så `total` forbliver
rigtigt og sideinddelingen dækker hele databasen.

---

## 16. Semantisk søgning (vektorer)

Leksikalsk søgning finder kun det, der er skrevet med de ord brugeren valgte.
Søger en maskinmester efter **livbåde**, finder ordsøgningen ikke
*Bekendtgørelse om redningsmidler i handelsskibe*, fordi ordet "livbåd" ikke
står der. Det semantiske lag løser netop det: lovteksten og søgningen
oversættes begge til talrækker — vektorer — hvor nærhed betyder "handler om
det samme".

### Hvad der bliver vektoriseret

**Lovteksten**, delt i stykker. En bekendtgørelse på 80.000 tegn ville som én
vektor blive et gennemsnit af alle sine emner og ligne enhver søgning en lille
smule. Teksten deles derfor ved kapitel-, paragraf- og stykkegrænser, så et
stykke så vidt muligt svarer til én bestemmelse — den enhed man henviser til.
Nabostykker deler de sidste tegn, så en bestemmelse hen over en grænse ikke går
tabt begge steder. Hvert stykke vektoriseres med dokumentets titel og nærmeste
overskrift foran; et stykke der blot siger *"Reglerne i stk. 1 gælder ikke for
fartøjer under 15 meter"* er meningsløst uden at vide hvilken bekendtgørelse
det står i.

**Søgningerne**. Hver søgning der stilles, gemmes én gang med sin egen vektor
(se `search_queries`). Det giver "andre har også søgt efter …" på tværs af
ordvalg, og — vigtigere — listen over søgninger der aldrig har givet et svar.
Der gemmes hverken bruger, IP-adresse eller session: tabellen kan besvare *hvad*
der søges efter, ikke *hvem* der søgte.

### De tre søgetilstande

`GET /api/search?mode=…`, og som knapper under søgefeltet:

| Tilstand | Hvad den gør | Hvornår den er rigtig |
|---|---|---|
| `lexical` | Ordene skal stå i teksten | Paragraf- og nummerhenvisninger, "MARPOL bilag VI" |
| `semantic` | Betydningen skal ligne | Man kender emnet, men ikke lovtekstens ord |
| `hybrid` | Begge dele (standard) | Alt andet |

Hybrid smelter de to rangeringer sammen med **Reciprocal Rank Fusion**:

```text
score(d) = w_leks / (k + rang_leks(d)) + w_sem / (k + rang_sem(d))
```

Scorerne lægges bevidst *ikke* sammen. `ts_rank_cd` er et ubegrænset tal, der
afhænger af dokumentets længde; cosinus-lighed ligger mellem 0 og 1 og er
sammentrykt i den øvre ende. At normalisere dem til samme skala kræver
antagelser om fordelingen, der ikke holder. RRF ser bort fra scorerne og bruger
kun rækkefølgen — det eneste de to metoder er enige om at måle. `k = 60` er
værdien fra litteraturen; vægtene er 1,0 leksikalsk og 0,8 semantisk, en
tilsigtet overvægt til de eksakte ord.

Hvert søgeresultat er mærket med hvordan det blev fundet — `Ordmatch`,
`Betydningsmatch` eller `Ord + betydning` — plus lighed i procent og
overskriften på det stykke der matchede. Uden den markering kan en bruger ikke
se forskel på "ordene står i dokumentet" og "dokumentet handler om noget
beslægtet", og netop den forskel afgør, om dokumentet kan bruges som henvisning.

### Nedgradering siges højt

Findes der ingen vektorer endnu — eller kan modellen ikke indlæses — falder
søgningen tilbage til leksikalsk. Det står i svaret (`mode`, `notice`) og vises
i brugerfladen. En bruger, der tror der blev søgt på betydning, kan ellers
konkludere at et emne er ureguleret, alene fordi bekendtgørelsen bruger et
andet ord.

### Model og udbydere

`EmbeddingProvider` er en protokol med tre implementeringer, valgt med
`EMBEDDING_PROVIDER`:

| Værdi | Implementering | Bemærkning |
|---|---|---|
| `local` | `sentence-transformers` i backend-containeren | **Standard.** Ingen nøgle, intet netværk, teksten forlader ikke maskinen |
| `api` | OpenAI-kompatibelt HTTP-endpoint | Kræver `EMBEDDING_API_URL`. Sender dokumentteksten ud af huset |
| `hashing` | Deterministisk hash af ord | **Ikke semantisk.** Kun til test — markerer sig selv som sådan overalt |

Standardmodellen er `intfloat/multilingual-e5-small`: flersproget med dansk i
træningsdata, 384 dimensioner, kører på CPU. E5 er trænet asymmetrisk, så tekst
der indekseres får `passage: ` foran og en søgning `query: `; præfikserne ligger
i konfigurationen, så en anden model kan sætte dem tomme.

Der falder **aldrig** automatisk tilbage fra én udbyder til en anden. Et indeks
bygget halvt med én model og halvt med en anden ville give resultater, ingen
kunne forklare, og fejlen ville først vise sig som dårlig søgekvalitet. Samme
princip som `retsinformation/factory.py`.

### Lagring

Vektorerne gemmes to steder:

* `document_chunks.embedding` — float32 little-endian BLOB. Portabel, virker
  på både SQLite og PostgreSQL, og er **sandheden**.
* `document_chunks.embedding_vec` — pgvector-kolonne med HNSW-indeks. Findes
  kun på PostgreSQL og kun hvis udvidelsen er installeret. Et *indeks* over
  BLOB'en, ikke en selvstændig kilde. Begge skrives i samme transaktion.

Alle vektorer L2-normaliseres før de gemmes, så cosinus-lighed er identisk med
prikproduktet — hurtigere i den portable sti, og samme rangering som pgvectors
`<=>`.

Migration 0004 opretter kun pgvector-kolonnen, hvis udvidelsen faktisk er til
stede; den spørger `pg_available_extensions` først, fordi et fejlet
`CREATE EXTENSION` ville rulle hele migrationen tilbage. `docker-compose.yml`
bruger derfor `pgvector/pgvector:pg16`. Med et almindeligt postgres-image virker
alt stadig — vektorsøgningen falder blot til den portable sammenligning, og
`embed status` siger det.

### Byg og vedligehold indekset

Vektorisering sker **adskilt fra importen**, og det er et bevidst valg: en
import af tusindvis af dokumenter må ikke tage timer længere, og den må ikke
kunne fejle, fordi en model ikke kunne indlæses. Lovteksten er det vigtige;
vektorerne er et indeks over den.

```bash
# Kontrollér model og vektorlængde, FØR indekset bygges
python -m app.cli embed model-info

# Vektorisér det der mangler
python -m app.cli embed run
python -m app.cli embed run --limit 200          # i bidder
python -m app.cli embed run --reset              # byg forfra (modelskifte)

# Dækning, model, pgvector-tilstand og søgelog
python -m app.cli embed status

# Hvad brugerne søger efter
python -m app.cli search-log
python -m app.cli search-log --without-results   # den interessante liste
```

Med Docker:

```bash
docker compose exec backend python -m app.cli embed run
docker compose exec backend python -m app.cli embed status
```

Eller fra brugerfladen: **Import og drift → Betydningssøgning → Vektorisér
manglende** (højst 200 ad gangen, da kaldet er synkront).

Hvad der "mangler" afgøres af `Document.needs_embedding`: aldrig vektoriseret,
ny version siden sidst, eller vektorer fra en anden model. Ingen tilstandsmaskine
kan komme ud af trit med virkeligheden.

### Skift af model

```bash
# 1. Ret EMBEDDING_MODEL og EMBEDDING_DIMENSIONS i .env
# 2. Bekræft at de to passer sammen
python -m app.cli embed model-info
# 3. Kun PostgreSQL: pgvector-kolonnen har den gamle dimension
python -m app.cli embed vector-column --recreate
# 4. Byg indekset forfra
python -m app.cli embed run --reset
```

`embed status` advarer, hvis kolonnens og modellens dimensioner er kommet ud af
trit — ellers ville fejlen først vise sig som en databasefejl midt i en søgning.

---

## 17. Strukturel parsing, titler og domænejusteret rangering

Tre ting, der hænger sammen: hvad systemet indekserer, hvad det kalder
dokumenterne, og i hvilken rækkefølge det viser dem.

### 17.1 Paragraffen er enheden

Tidligere blev lovteksten skåret i vinduer på omtrent 1.200 tegn, hvor
snittet blev flyttet hen til nærmeste paragrafgrænse, hvis der tilfældigvis
lå én i nærheden. Indeksets enheder svarede derfor ikke til noget, en
jurist kan henvise til: et stykke kunne indeholde halvanden paragraf, og en
kort paragraf kunne blive slugt af sin nabo.

`app/services/legal/structure.py` læser nu lovens **form** først:

```text
dokument
├── præambel            "I medfør af § 1 i lov om ... fastsættes:"
├── Afsnit I            (valgfrit)
│   └── Kapitel 1  Anvendelsesområde
│       ├── § 1
│       │   ├── Stk. 2
│       │   └── Stk. 3
│       └── § 2
└── Kapitel 2 ...
```

Derefter gælder:

* **én paragraf = ét stykke** i indekset, med kapitel, eventuelt afsnit,
  paragraf-id, sorteringsnøgle og fuld henvisning på;
* stykkerne **flisebelægger** teksten — hvert stykke går fra slutningen af
  det forrige til begyndelsen af det næste, så kapiteloverskrifter og
  bilagslinjer altid hører til et bestemt stykke;
* er en enkelt paragraf for lang, deles den ved `Stk. N`-grænser, aldrig
  midt i en bestemmelse;
* **præamblen gemmes som sin egen enhed** (`unit_type="preamble"`). Den er
  dokumentets hjemmel, ikke en regel, og den skal ikke konkurrere med
  paragrafferne om at være det bedste hit;
* findes der ingen paragraffer (bilag, tabeller, vejledninger), falder
  opdelingen tilbage til afsnit og sætninger, og stykkerne mærkes
  `unit_type="fragment"` — så det kan ses i indekset frem for at blive
  forvekslet med rigtige paragraffer.

Overlap mellem nabostykker er væk. Det fandtes, fordi en bestemmelse kunne
ligge hen over et vilkårligt snit; med lovens egne grænser er der ikke
noget at redde, og overlap ville kun være dubleret tekst i indekset.
`CHUNK_OVERLAP_CHARS` bruges derfor kun i den ustrukturerede nødsti.

**Paragraffen følger med i alle tre søgetilstande.** Vektorindekset kender
det bedst matchende stykke, men kun for vektoriserede dokumenter og kun ved
semantisk søgning. `app/services/search/paragraphs.py` finder derfor
paragraffen for de resultater, der står på den viste side — højst 20 ad
gangen — ved at parse den gældende version og score paragrafferne mod
søgeordene. Parsingen caches pr. version, og en version er uforanderlig, så
cachen kan ikke blive forældet.

### 17.1b Når kilden leverer teksten fladt

Retsinformations XML bærer selv strukturen: kapitler, paragraffer og
stykker er hver sit element. Den skal **bevares** ved indlæsning, ikke
genskabes bagefter. `xml_parser._element_lines` lader derfor hver
elementgrænse blive et linjeskift, og føjer kun inline-markup (`<i>`,
`<Ref>`) sammen igen, fordi en linje der begynder med lille bogstav er
resten af den forrige sætning — aldrig en ny bestemmelse.

Det er ikke altid nok. Nogle dokumenter har ingen markup pr. bestemmelse,
og materiale importeret før denne rettelse ligger allerede gemt på én
linje. Parseren kan derfor også genkende en åbner **midt i en linje** — men
kun når fire uafhængige krav er opfyldt samtidig:

| Krav | Fanger |
|---|---|
| Kanonisk form `§ 12.` med punktum | `§ 12`, `§ 12, stk. 2`, `§§ 3-5` |
| Efterfulgt af stort begyndelsesbogstav | `… gælder dog ikke § 8.` (intet indhold efter) |
| Ingen forkortelse foran (`jf.`, `nr.`, `stk.`) | `… ansvaret, jf. § 4.` |
| Stigende nummerering | `§ 3` nævnt inde i § 12 |

At tage fejl her er værre end ingen struktur: blev `jf. § 4` til en ny
paragraf, ville lovtekst flytte over i en bestemmelse, den ikke hører til,
og et søgeresultat ville pege på et sted, hvor reglen ikke står. Derfor
værnene, og derfor `tests/test_flat_text_parsing.py`, hvor hvert værn har
sine egne modeksempler.

Positionerne flyttes ikke: segmenteringen indsætter ingen tegn, så et
stykkes `content` bliver ved med at være præcis `text[char_start:char_end]`.

**Mål før du bygger om.** To kommandoer, begge read-only:

```bash
# Fordelingen i hele samlingen: gemt indeks vs. hvad parseren ville give nu
python -m app.cli ranking parse-report

# Ét konkret dokument, med de stykker det ville give
python -m app.cli ranking parse-doc B20220122005
```

`embed status` viser den samme fordeling og advarer, hvis under halvdelen
af stykkerne er paragraffer. Dækning og kvalitet er to forskellige
spørgsmål: et indeks kan være 100 % vektoriseret og alligevel bestå af
vilkårlige tekstvinduer.

### 17.2 To titler

Officielle titler er skrevet for at være juridisk entydige, ikke for at
kunne skimmes. Hvert dokument bærer derfor to:

| Felt | Bruges til |
|---|---|
| `original_title` | Metadata, citater, fold-ud. Uændret fra kilden. |
| `display_title` | Søgeresultater, forsidekort, relaterede dokumenter, dokumentheader. |

```text
original_title:  Bekendtgørelse af lov om sikkerhed til søs (søsikkerhedsloven),
                 jf. lovbekendtgørelse nr. 1629 af 17. december 2018 med
                 senere ændringer
display_title:   Lov om sikkerhed til søs
```

Reglerne står i `app/services/legal/titles.py` og er bevidst få:
kundgørelsesformen fjernes ("Bekendtgørelse af lov om X" → "Lov om X"),
haler som `jf. …` og `med senere ændringer` klippes, et afsluttende
populærnavn i parentes fjernes, og en stadig for lang titel klippes ved en
**sproglig** grænse — komma, "samt", "jf." — aldrig midt i et ord uden
først at have prøvet alt andet. Kildens egen korttitel bruges kun, når
titlen ellers måtte afkortes; ellers ville resultatlisten vise et andet
navn end det, brugeren søgte på.

Prøv en titel:

```bash
python -m app.cli ranking explain --title "Bekendtgørelse af lov om sikkerhed til søs"
```

### 17.3 Kernelov, speciallov, støttedokument

Maritim relevans (0–100) siger *om* et dokument hører til i databasen. Den
siger intet om, hvor centralt det er. *Bekendtgørelse om sikkerhed ved
arbejdets udførelse på fiskeskibe* og *lov om sikkerhed til søs* kan sagtens
få samme relevansscore — men den ene gælder fiskeskibe, og den anden gælder
alle danske skibe.

`documents.law_class` er derfor et selvstændigt felt:

| Klasse | Betyder |
|---|---|
| `kernelaw` | Bredt anvendeligt, centralt regelsæt. Standard for et maritimt dokument uden indsnævrende markør. |
| `speciallaw` | Titlen bærer mindst én nichemarkør — fiskeskibe, Grønland, Færøerne, lodseri, fritidsfartøjer, offshore ... |
| `support` | Vejledning, cirkulære eller ændringsbekendtgørelse. |

Sammen med klassen gemmes `scope_score` (hvor bredt reglen gælder, 0–1),
`authority_score` (vægt som retskilde, 0–1) og `niche_groups` (hvilke
nichegrupper titlen peger på).

**Retlig status indgår ikke i klassifikationen.** En ophævet særregel om
fiskeskibe er stadig en særregel om fiskeskibe; blev den omklassificeret til
støttedokument, ville den miste sin nichemarkering og ikke kunne findes ved
en nichesøgning — og nedjusteringen ville blive talt to gange. Status
håndteres ét sted: `status_scores` og `historic_penalty`.

Nichegrupper, kernelovsmønstre og støttemønstre står i
`config/ranking.yaml` og kan udvides uden kodeændring.

### 17.4 Rangeringsmodellen

```text
base = 0.40 * lexical + 0.25 * semantic + 0.15 * authority
     + 0.10 * scope   + 0.05 * maritime + 0.05 * status

final = base * produktet af domænereglerne
```

`lexical` og `semantic` er **placeringsbaserede**, ikke rå scorer:
`k / (k + placering - 1)`, så nr. 1 får 1,0. Det er den samme indsigt, der
oprindeligt førte til Reciprocal Rank Fusion — `ts_rank_cd` er ubegrænset og
længdeafhængig, cosinus-lighed ligger sammenpresset mellem 0,7 og 0,9, og de
to kan ikke lægges sammen som tal. Til gengæld er begge nu 0–1, og de fire
domænesignaler kan lægges til.

Domænereglerne er **multiplikatorer med en begrundelse i klartekst**, ikke
flere led i summen. Det er et bevidst valg: brugerfladen skal kunne sige
"nedjusteret 30 % — speciallov ved bred søgning", og det tal skal svare til
noget. Ingen regel kan nulstille et resultat (`min_multiplier`); et dokument
der matcher, skal kunne findes, det skal blot stå længere nede.

### 17.5 Query intent

Systemet klassificerer søgningen, før det rangerer:

| Søgning | Type | Følge |
|---|---|---|
| `hviletid` | bred | Kernelove op, speciallove ned |
| `brand passagerskib` | bred | Kernelove op |
| `hviletid for søfarende om bord på danske skibe` | semispecifik | Samme, men mildere |
| `fiskeskib hviletid` | niche (fiskeskibe) | Fiskeskibsregler kraftigt op |
| `grønlandske lodser hviletid` | niche (Grønland, lodseri) | Grønlandske lodsregler kraftigt op |

Ordvalget alene er dog ikke nok. `trawlspil` er ét ord uden nichemarkør og
læses først som bred — men termen findes i ét eneste dokument. Uden en
justering ville de brede domæneregler nedjustere netop det dokument,
brugeren ledte efter, under dokumenter der slet ikke indeholder ordet.
Antallet af leksikalske træf er det direkte mål for, hvor almindeligt det
skrevne er i materialet; er det under `specific_max_results`, behandles
søgningen som specifik. Justeringen går kun én vej — mod mere specifik — og
den siges højt i svaret (`intent.refinement_reason`).

Prøv en søgning:

```bash
python -m app.cli ranking explain --query "grønlandske lodser hviletid"
```

### 17.6 Genberegning

Titler og rangeringssignaler sættes ved import. Efter migration `0005` og
efter enhver ændring af `config/ranking.yaml` skal de eksisterende
dokumenter genberegnes. Det kræver hverken model eller netværk:

```bash
# Se hvad der ville ændre sig
make reclassify-dry

# Skriv ændringerne
make reclassify

# Er stykkegrænserne ændret, skal det semantiske indeks bygges om
python -m app.cli embed run --reset
```

---

## 18. REST-API

Interaktiv dokumentation på `/docs` (slås fra med `EXPOSE_API_DOCS=false`).

Kolonnen **Adgang** er en sikkerhedsgrænse, ikke en bemærkning: alt
markeret 🔒 kræver `Authorization: Bearer <ADMIN_API_TOKEN>`. Se
[afsnit 22](#22-sikkerhed-og-adgangskontrol).

| Metode | Sti | Adgang | Beskrivelse |
|---|---|---|---|
| `GET` | `/api/search` | offentlig | Søgning med facetfiltre, `law_class` og `mode=lexical\|semantic\|hybrid` |
| `GET` | `/api/documents` | offentlig | Dokumentliste |
| `GET` | `/api/documents/{id}` | offentlig | Metadata, tekst, kategorier, forklaring, versioner |
| `GET` | `/api/documents/{id}/versions` | offentlig | Versionshistorik |
| `GET` | `/api/documents/{id}/versions/{n}` | offentlig | Indholdet af en bestemt version |
| `GET` | `/api/documents/{id}/structure` | offentlig | Kapitler og paragraffer i den gældende tekst |
| `GET` | `/api/documents/{id}/similar` | offentlig | Dokumenter der ligner dette (vektorlighed) |
| `GET` | `/api/core-laws` | offentlig | Centrale maritime love — forsidens udgangspunkt |
| `GET` | `/api/categories` | offentlig | Taksonomi med dokumenttællinger |
| `GET` | `/api/facets` | offentlig | Tilgængelige filterværdier |
| `GET` | `/health` | offentlig | Systemtilstand |
| `GET` | `/api/admin/session` | 🔒 | Kontrollér administratortoken |
| `GET` | `/api/stats` | 🔒 | Nøgletal |
| `POST` | `/api/import/run` | 🔒 | Kør en import |
| `GET` | `/api/import/runs` | 🔒 | Importhistorik |
| `GET` | `/api/embeddings/status` | 🔒 | Dækning og tilstand for det semantiske indeks |
| `POST` | `/api/embeddings/run` | 🔒 | Vektorisér de dokumenter der mangler |
| `GET` | `/api/search/queries` | 🔒 | Søgelog: `kind=popular\|without_results` |
| `GET` | `/api/search/related` | 🔒 | Tidligere søgninger der ligner en given |

SQLAlchemy-modeller returneres aldrig direkte; alle svar går gennem
Pydantic-skemaer. Fejl har ensartet format med `detail` og `error_type`.
Kildefejl oversættes til meningsfulde statuskoder: 503 ved midlertidig
utilgængelighed, 502 ved ugyldigt svar, 404 ved ukendt dokument. Manglende
eller forkert administratortoken giver 401 med `WWW-Authenticate`; er
tokenet slet ikke opsat på serveren, giver de beskyttede endepunkter 503.
For mange forespørgsler giver 429 med `Retry-After`.

---

## 19. Frontend

Tre sider:

**Søgeside** — søgefelt, valg mellem *Ordret*, *Betydning* og *Kombineret*,
aktive filtre som chips under søgningen, resultattælling og resultatliste.
Hvert resultat viser den korte visningstitel, status, dokumentklasse, type og
maritim relevans som badges — og derefter det **bedst matchende paragrafhit**
med kapitelhenvisning (`Kapitel 2 — Hviletid · § 3`) frem for en tekststump
fra et vilkårligt sted i dokumentet. Er der flere matchende paragraffer, kan
de foldes ud uden at forlade listen. En linje under søgefeltet siger, hvordan
søgningen blev læst (*bred*, *semispecifik*, *niche*), så en uventet
rækkefølge kan forklares frem for at ligne en fejl.

På en ufiltreret forside vises **"Start her — centrale maritime regler"**: et
udvalg af kernelove, hentet fra `/api/core-laws`. Udvælgelsen er den samme
`law_class`, som rangeringen bruger, så forsiden ikke kan komme ud af trit
med søgemaskinen.

*Desktop:* klæbende filterpanel i venstre spalte. Panelet klæber fra toppen af
sit eget spor (`align-items: start` på gitteret) og følger derfor med fra
første scroll i stedet for at "komme med" senere. Det har egen scroll og en
maxhøjde, så et langt filterpanel ikke selv bliver årsag til, at man ikke kan
nå bunden.

*Mobil:* ingen permanent sidebjælke. En filterknap ved siden af
resultattællingen åbner en **skuffe** med de samme filtre — samme komponent,
ikke en fattigere udgave — foldet i accordion-sektioner, med en klæbende
bundlinje med *Ryd* og *Vis N resultater*. Skuffen fanger fokus, lukkes på
Escape og låser baggrundens scroll. Ingen vandret scroll ved 390 px bredde.

Filtre, tilstand og side ligger i URL'en, så en søgning kan deles og
genindlæses.

**Dokumentside** — en læsevisning. Første skærmbillede indeholder kort
visningstitel, status- og klassebadges, maritim relevans, første
kapiteloverskrift og første paragraf. Lovteksten sættes fra dokumentets
**struktur**: kapitler som overskrifter, paragraffer som afsnit med ankre, så
der kan linkes direkte til `§ 12`.

Fuld juridisk titel, præambel, metadata, kategorier, relevansforklaring,
versionshistorik og ændringslog er **foldet sammen som standard** (`<details>`,
så de virker uden JavaScript, kan findes med browserens egen sidesøgning og
udskrives åbne). Beslægtede regler vises som kompakte kort med korte titler,
højst to linjer med ellipsis og hele kortet klikbart. Link til originalen på
Retsinformation ligger i metadata-fold-ud'et.

**Import og drift** (`#/drift`, kræver administratortoken) — nøgletal,
manuel import med eksplicit kildevalg,
detaljer om seneste kørsel inklusive fejl, fuld importhistorik, tilstanden for
det semantiske indeks med knap til at vektorisere det manglende, og søgeloggen
med både de hyppigste søgninger og dem der aldrig har givet et svar.

Fladen prioriterer informationstæthed og læsbar lovtekst frem for pynt.
Lovtekst sættes med serif; status og score har hver sin farvekodning, så de
kan skimmes. Syntetiske data markeres altid tydeligt.

---

## 20. Test

```bash
cd backend && python -m pytest          # 584 tests
```

| Fil | Dækker |
|---|---|
| `test_relevance_engine.py` | Scoring, anti-spam, falske positiver, forklarbarhed |
| `test_categorization.py` | Taksonomi, konfidens, fallback |
| `test_document_versioning.py` | Hashing, versionsforløb, statusændring |
| `test_importer.py` | Idempotens, afvisning, fejlisolering, sporbarhed |
| `test_backfill.py` | Reservationer, udløbne leases, fencing token, forsøgsgrænser, stopkriterier |
| `test_search.py` | Fritekst, filtre, sortering, sideinddeling |
| `test_source_clients.py` | Normalisering, XML-parser, HTTP-adfærd, kildevalg |
| `test_api.py` | Alle endpoints, validering, fejlkoder |
| `test_embedding.py` | Vektorprimitiver, chunking ved §-grænser, udbydervalg, HTTP-adfærd |
| `test_vector_search.py` | Indeksering, forældede vektorer, filtre, RRF-sammensmeltning, nedgradering |
| `test_query_log.py` | Aggregering pr. søgning, beslægtede søgninger, søgninger uden svar |
| `test_api_semantic.py` | Søgetilstande, lignende dokumenter, søgelog, driftsvisning |
| `test_security.py` | Adgangskontrol pr. endepunkt, fail-closed opstart, rate limiting, klientadresse bag proxy |
| `test_legal_structure.py` | Kapitel-, §- og stk.-parsing, præambel, robusthed mod ustruktureret tekst, visningstitler |
| `test_ranking.py` | `law_class`, scope, autoritet, query intent, scoremodel og de tre scenarier fra specifikationen |
| `test_api_structure.py` | Titler, dokumentklasse, paragrafhit, rangeringsforklaring, `/api/core-laws`, `/api/documents/{id}/structure` |

Tests kører mod et skema oprettet med de rigtige Alembic-migrationer, ikke
`create_all` — så det testede skema er det, der udrulles.

Testene henter **aldrig** en embedding-model. De kører med den deterministiske
hash-udbyder (`EMBEDDING_PROVIDER=hashing`, sat i `conftest.py`), så hele
rørføringen — chunking, lagring, sammensmeltning, søgelog — kan afprøves
reproducerbart uden netværk og uden torch. Adapteren omkring den rigtige model
er testet mod et stand-in, og HTTP-udbyderen mod en `MockTransport`. Hvad der
bevidst *ikke* testes, er modelkvalitet: en påstand om at "livbåd ligner
redningsflåde" ville med hash-udbyderen måle støj. Sprogkvalitet hører til i en
evaluering mod et sæt kendte søgninger — se [begrænsninger](#23-kendte-begrænsninger).

Opgavespecifikationens hovedkrav er dækket eksplicit:
`"Bekendtgørelse om sikkerhed på passagerskibe"` → maritimt;
`"Bekendtgørelse om folkeskolens undervisning"` → ikke maritimt.

### Integrationsverifikation

```bash
ADMIN_API_TOKEN=$(grep '^ADMIN_API_TOKEN=' .env | cut -d= -f2-) \
    python3 scripts/verify_api.py http://localhost:8000
```

Gennemgår hele brugerrejsen mod et kørende API: import, genkørsel uden
dubletter, klassifikation, søgning, filtre, dokumentvisning, forklaring,
versionering og fejlhåndtering. Verificeret mod **både PostgreSQL 16 og
SQLite**.

Scriptet kører import og læser driftstal og kræver derfor tokenet. Sidste
afsnit kontrollerer det modsatte: at de beskyttede endepunkter svarer 401
*uden* token, mens søgningen er åben.

---

## 21. Måling af søgekvalitet

Uden en facitliste er "systemet finder de rigtige dokumenter" et postulat.
Dette lag gør det til et tal, og gør det muligt at se om en ændring af model,
vægte eller tærskel faktisk hjalp.

```bash
python -m app.cli evaluate run                    # fixtursættet
python -m app.cli evaluate run --verbose --k 10   # hver søgning, og hvad der blev overset
python -m app.cli evaluate run --out rapport.json
```

### Hvad der måles

| Måletal | Hvad det siger |
|---|---|
| **Recall@k** | Hvor stor en del af de rigtige dokumenter kom med i top-k. Det vigtigste tal: et overset dokument er en regel, brugeren ikke ved findes |
| **Præcision@k** | Hvor stor en del af top-k var rigtige. Har et loft — med ét rigtigt svar kan P@10 aldrig overstige 0,1 |
| **MRR** | Hvor hurtigt brugeren fik fat i noget brugbart. Træf på plads 1 = 1,0, plads 2 = 0,5 |
| **nDCG@k** | Som recall, men belønner at det rigtige ligger øverst |
| **Negative kontroller** | Søgninger der IKKE må give svar. Et system der svarer på alt er lige så ubrugeligt som et der ikke svarer |

Definitionerne står i `services/evaluation/metrics.py` uden afhængigheder, så de
kan regnes efter i hånden — et måletal ingen kan efterregne, er værre end intet,
fordi det bliver troet.

### Målt på fixtursamlingen

De 18 syntetiske fixturdokumenter, 21 søgninger med facit og 3 negative
kontroller, kørt med hash-udbyderen:

| Tilstand | Recall@10 | MRR | nDCG@10 | Fuldt dækket | Negative kontroller |
|---|---|---|---|---|---|
| lexical | 0,810 | 0,833 | 0,803 | 16/21 | 3/3 |
| semantic | 0,857 | 0,612 | 0,648 | 16/21 | 0/3 |
| hybrid | **0,929** | **0,904** | **0,880** | **18/21** | 0/3 |

**Læs tallene med to forbehold.** De gælder 18 konstruerede dokumenter og siger
intet om 2.900 rigtige. Og de er målt med hash-udbyderen, ikke med E5 — det er
derfor de negative kontroller fejler i de to semantiske tilstande: hash-udbyderen
har ingen brugbar lighedstærskel (se `hashing.py`), så den svarer på alt. Med en
rigtig model og en tærskel på 0,75 skal den kolonne læses igen.

Det tallene faktisk viser, er formen: hybrid taber intet i forhold til ordsøgning
og henter de fire ordforrådssøgninger hjem, som ordsøgningen slet ikke kunne
besvare (`livbåde`, `EPIRB`, `hvor længe skal en sømand hvile`, `hvem bestemmer
om bord på skibet`). Samtidig ligger alle eksakte termer — `MARPOL bilag VI`,
`trawlspil`, dokumentnummer `1290` — fortsat på plads 1.

### Byg et evalueringssæt til den rigtige samling

Fixtursættet er en regressionsprøve, ikke en vurdering. Til den rigtige samling
laves et sæt af de søgninger, brugerne faktisk stiller:

```bash
# 1. Kandidater fra søgeloggen, samlet på tværs af alle tre tilstande
python -m app.cli evaluate scaffold --from-search-log --limit 50 \
    --out manifests/eval-review.csv

# 2. En fagperson udfylder kolonnen 'relevant' med ja/nej

# 3. CSV -> evalueringssæt
python -m app.cli evaluate import-csv --file manifests/eval-review.csv \
    --corpus production --out data/eval/production-queries.yaml

# 4. Mål
python -m app.cli evaluate run --file data/eval/production-queries.yaml --verbose
```

Samme mønster som `discover` → CSV → gennemgang → `enqueue-manifest`, og af samme
grund: afgørelsen er menneskelig, og den skal kunne ses i en git-diff.

**Pooling-skævheden skal med i enhver rapport.** Kandidaterne samles fra alle tre
tilstande — bygges facitlisten kun af det ordsøgningen fandt, kan
betydningssøgningen aldrig vise sin værdi. Men et dokument som *ingen* tilstand
fandt, kommer ikke i CSV'en og kan ikke markeres relevant. Recall måles derfor
mod "det de tre tilstande tilsammen fandt", ikke mod sandheden. Det er samme
begrænsning som TREC's pooling. Modvægten er at hæve `--candidates` og at tilføje
dokumenter i hånden.

### Som regressionsværn

```bash
python -m app.cli evaluate run --min-recall 0.85
```

Returnerer 1, hvis en tilstand ligger under grænsen. Egnet til CI: ændrer nogen
vægte, chunk-størrelse eller model, og recall falder, opdages det med det samme
i stedet for et halvt år senere.

---

## 22. Sikkerhed og adgangskontrol

Systemet har to slags brugere, og kun to: **den søgende**, der læser
lovtekst, og **den driftsansvarlige**, der importerer og vedligeholder.
Grænsen mellem dem håndhæves i API'et — ikke i brugerfladen. At skjule en
knap er ikke sikkerhed; at afvise kaldet er.

### Administratortoken

Alt, der skriver til databasen eller afslører drift, kræver et delt token:

```http
Authorization: Bearer <ADMIN_API_TOKEN>
```

```bash
make admin-token          # generér et token
# skriv det i .env som ADMIN_API_TOKEN=... og genstart backenden

curl -H "Authorization: Bearer $ADMIN_API_TOKEN" \
     -X POST http://localhost:8000/api/import/run
```

Der er bevidst **ingen brugerdatabase**. Installationen har én
driftsansvarlig, og et rollesystem med brugere, kodeord og sessioner ville
koste vedligehold uden at give mere sikkerhed. Skal flere personer have
hver sin adgang, er `require_admin` i `backend/app/core/security.py` det
eneste sted, der skal ændres — ruterne kender kun dependencyen.

To egenskaber er værd at kende:

* **Lukket som udgangspunkt.** Er `ADMIN_API_TOKEN` tom, svarer de
  beskyttede endepunkter `503`. En glemt konfiguration lader dem ikke stå
  åbne.
* **Nægter at starte uden token i produktion.** Med
  `ENVIRONMENT=production` afviser backenden at starte, hvis tokenet
  mangler eller er kortere end 24 tegn. Alternativet — at starte alligevel
  — ville give en tjeneste, der ser rask ud, men ikke kan drives.

Tokenet sammenlignes med `secrets.compare_digest` og skrives aldrig i
loggen. Et afvist forsøg logges som `admin.token.rejected` uden værdien.

### Hvad der er offentligt

Søgning, dokumenter, versioner, kategorier, facetter og `/health`.
Lovtekst er offentlig, og det er hele formålet med tjenesten.

### Hvad der er beskyttet

Import, vektorisering, importhistorik, nøgletal, indeksets tilstand og
søgeloggen. Søgeloggen indeholder hverken bruger, IP-adresse eller
session — men den viser hvad et navngivet sted interesserer sig for, og
hvad materialet mangler.

### Rate limiting

Grænser pr. klient-IP pr. minut, håndhævet **to steder**:

| Lag | Hvor | Hvorfor |
|---|---|---|
| nginx | `limit_req_zone` i `frontend/nginx.conf` | Afviser et angreb, før det koster en Python-forespørgsel |
| FastAPI | `RateLimitMiddleware` | Grænsen gælder også, hvis API'et nås direkte |

Søgning har egen, strammere kvote, fordi den rammer både fuldtekstindekset
og — i hybridtilstand — embedding-modellen. `/health` begrænses ikke, så
Docker og overvågning kan spørge frit.

```dotenv
RATE_LIMIT_REQUESTS_PER_MINUTE=120
RATE_LIMIT_SEARCH_PER_MINUTE=30
TRUST_PROXY_HEADERS=true
```

Bag en proxy er klientens adresse ikke socket-adressen. `CF-Connecting-IP`
og `X-Forwarded-For` bruges derfor — men **kun** når
`TRUST_PROXY_HEADERS=true`. Uden det forbehold kunne enhver klient skrive
en ny afsenderadresse for hver forespørgsel og dermed have uendelig kvote.
Sæt den kun, når applikationen faktisk står bag en proxy, du kontrollerer.

Ændrer du tallene, skal `limit_req_zone` i `frontend/nginx.conf` følge med.

### Øvrige foranstaltninger

* **Portbinding.** Docker binder alle porte til `127.0.0.1` som standard
  (`BIND_ADDRESS`). PostgreSQL er aldrig nåelig fra netværket.
* **Sikkerhedsheadere** sættes af nginx —
  `frontend/security-headers.conf`: CSP, `nosniff`, `X-Frame-Options:
  DENY`, `Referrer-Policy`, `Permissions-Policy`.
* **CORS** er tomt i produktion. nginx serverer frontend og `/api` fra
  samme domæne, så browseren har ikke brug for det. `CORS_ORIGINS=*`
  afvises i produktion.
* **API-dokumentationen** (`/docs`, `/redoc`, `/openapi.json`) slås fra med
  `EXPOSE_API_DOCS=false` og proxies aldrig gennem nginx.

### Kontrollér selv

```bash
make deploy-check                    # .env klar til offentlig udgivelse?
cd backend && python -m pytest tests/test_security.py
```

`tests/test_security.py` afprøver hvert beskyttet endepunkt uden token,
med forkert token og med gyldigt token; at de offentlige endepunkter
stadig er åbne; at en manglende serverkonfiguration lukker frem for at
åbne; og at rate limiting hverken kan omgås med en forfalsket
`X-Forwarded-For` eller lukker en hel skole ude bag én udgående adresse.

---

## 23. Offentlig udgivelse

Systemet gøres tilgængeligt på internettet med **Cloudflare Tunnel** —
uden at åbne porte i routeren og uden at offentliggøre maskinens
IP-adresse. `cloudflared` opretter en udgående forbindelse, og trafikken
kommer ind gennem den.

```bash
make admin-token        # 1. generér administratortoken -> .env
# 2. sæt ENVIRONMENT=production, POSTGRES_PASSWORD og TUNNEL_TOKEN i .env
make deploy-check       # 3. kontrollér opsætningen
make tunnel-up          # 4. start systemet bag tunnelen
make tunnel-logs        # 5. følg forbindelsen
```

`docker-compose.tunnel.yml` lægges oven på den almindelige compose-fil og
**fjerner portbindingerne** på database, backend og frontend. Efter det er
intet af systemet nåeligt uden om tunnelen. I Cloudflare peges den
offentlige adresse på `frontend:80` — aldrig på `backend:8000` og aldrig
på `db:5432`.

Hele fremgangsmåden, inklusive domæne, kontroller udefra, daglig drift og
fejlsøgning: **[docs/deployment-cloudflare-tunnel.md](docs/deployment-cloudflare-tunnel.md)**.

---

## 24. Kendte begrænsninger

Disse forhold er reelle og bør kendes, før systemet sættes i drift.

### Rangeringens vægte er begrundede, ikke målte

`config/ranking.yaml` bygger på brief'ets model og på afprøvning mod
fixtursættet. De tre scenarier — `hviletid`, `fiskeskib hviletid`,
`grønlandske lodser hviletid` — giver det rigtige svar, og der er tests, der
fastholder det. Men **et fixtursæt på 23 dokumenter kan ikke afgøre, om 0,35 i
kernelovsbonus er for meget eller for lidt** på en samling med tusinder.
`evaluate run` måler recall og præcision pr. søgetilstand; den måler endnu
ikke, om det rigtige dokument står øverst i rangeringen. Det er den oplagte
næste opgave.

### Klassifikationen er titelbaseret

`law_class` og nichegrupperne bestemmes af titel og korttitel — ikke af
lovteksten. Det er robust for dansk lovgivning, hvor titlen er lovgivers egen
emneangivelse, og det er hurtigt nok til at køre ved hver import. Men en
bekendtgørelse, hvis anvendelsesområde først indsnævres i § 1, stk. 2, bliver
klassificeret som bred. `LawClassifier` er isoleret bag en enkel grænseflade,
så en senere indholdsbaseret eller AI-assisteret klassifikation kan træde i
stedet uden at røre søgning eller API.

### Kernelovsmønstrene er en startliste

`law_class.core.title_patterns` indeholder de centrale danske søfartslove.
Listen er skrevet ud fra domænet, ikke udledt af data, og den vil mangle
noget. `core.source_ids` findes netop derfor: en konkret bekendtgørelse kan
udpeges som kernelov uden at ændre mønstrene.

### Paragrafgenkendelse i flad tekst bygger på sprogmønstre

Er kildens tekst leveret uden markup pr. bestemmelse, genkendes åbnere ud
fra fire samtidige krav (se [afsnit 17.1b](#171b-når-kilden-leverer-teksten-fladt)).
Værnene er skrevet mod dansk lovsprog og afprøvet mod modeksempler, men de
er mønstre, ikke en grammatik. Et dokument med usædvanlig sætningsbygning
kan miste en paragraf — den havner da i nabobestemmelsen frem for at blive
tabt.

Mål altid fordelingen med `ranking parse-report`, før et indeks bygges om,
og efterse de dokumenter, kommandoen viser uden en eneste paragraf. Bilag,
tabeller og noteapparater falder fortsat i `fragment`-stykker; det er
korrekt — de *er* ikke paragraffer.

### Genberegning er manuel

Ændres `config/ranking.yaml`, slår det først igennem, når `make reclassify`
køres. Det er bevidst: en automatisk genberegning ved opstart ville skrive
til hele dokumenttabellen, hver gang en container genstartes. Men det
betyder også, at en glemt genberegning giver en database, der rangerer efter
den gamle konfiguration uden at sige det.

### Høsteservicen er en ændringsfeed, ikke et katalog

Det officielle API leverer kun dokumenter ændret inden for de seneste 10
kalenderdage. Der findes **ikke** i den officielle dokumentation et endpoint,
der lister hele lovsamlingen eller tillader fritekstsøgning i kilden.

Konsekvens: **en fuld historisk backfill af al maritim lovgivning er ikke
mulig via det dokumenterede API alene.** Databasen bygges op over tid ved at
køre importen dagligt. Ældre dokumenter kan hentes ved at angive deres
accessionsnummer eksplicit — det er formålet med
[efterindlæsningskøen](#historisk-efterindlæsning-backfill).

Køen løser *genoptagelse og samtidighed*, ikke opdagelse. Numrene skal komme
udefra: fra Lovtidende, fra en myndighedsoversigt, fra en eksisterende liste.

Ønskes en fuld grunddatabase, kræver det en aftale med Civilstyrelsen om et
datadump eller en anden adgangsform.

### Søgegrænsefladen bag `discover` er ikke verificeret

`backfill discover` bygger på søgesiden på www.retsinformation.dk. Det
endpoint, dens JavaScript-frontend kalder, er **ikke** en dokumenteret del af
høsteservicen, og det kunne ikke kontrolleres i det miljø, funktionen blev
udviklet i (ingen udgående netværksadgang til retsinformation.dk).

Konsekvenserne er bevidste:

* Der er **ingen søge-URL i koden**. `RETSINFORMATION_SEARCH_URL` er tom som
  standard, og `discover` fejler med en forklarende besked, indtil den sættes.
* Udtrækket i `app/services/discovery/extract.py` er skrevet tolerant: det
  leder efter *strukturen* — en liste af poster med noget der ligner et
  accessionsnummer — frem for at antage bestemte feltnavne.
* `backfill probe-search` henter én side og beskriver den, så
  parametrene kan kontrolleres, før en opdagelse af ~2.900 dokumenter køres.
* Tælleprøven (606 + 2.281 = 2.887) standser kørslen, hvis resultatet ikke
  stemmer, frem for at skrive et ufuldstændigt manifest.

Status: **konfigurationsafhængig.** Abstraktion, paginering, tælleprøve,
CSV-manifest og kø-indlæsning er implementeret og testet mod kontrollerede
HTTP-svar og fixturdata. Selve endpointet skal aflæses én gang i en browser
med netværksadgang.

### Live-feed er verificeret; fuld produktionsimport skal overvåges

`GET https://api.retsinformation.dk/v1/Documents` blev verificeret mod den
aktive tjeneste den 13. august 2026 og returnerede det dokumenterede JSON-format.
Fejlhåndtering, rate limit og 10-dages begrænsningen er desuden dækket af
automatiske tests med kontrollerede HTTP-svar.

Den første fulde produktionsimport bør fortsat overvåges, især XML-parsningen,
fordi dokumenternes ELI-XML kan variere mellem dokumenttyper.

### ELI-XML-skemaet er ikke formelt verificeret

Det præcise XML-skema for dokumentteksten kunne ikke verificeres. Parseren er
derfor bevidst tolerant: den leder efter kendte elementnavne uafhængigt af
namespace, falder tilbage til at udtrække al tekst, og behandler ugyldig XML
som ren tekst. En skemaændring hos kilden giver dermed dårligere metadata frem
for et nedbrud i importen. Når skemaet er verificeret, bør
`FIELD_CANDIDATES` i `xml_parser.py` strammes op.

### Kvaliteten er målt på fixturer, ikke på den rigtige samling

Målekæden findes nu (se [afsnit 20](#20-måling-af-søgekvalitet)), og der er en
facitliste for de 18 fixturdokumenter. Men **der findes endnu ikke et
evalueringssæt for den rigtige samling**, og uden det er enhver påstand om at
systemet finder de rigtige bekendtgørelser stadig et postulat — nu blot et
postulat med et værktøj ved siden af.

Værktøjet er der; arbejdet er en fagpersons gennemgang af 30-50 rigtige
søgninger. `evaluate scaffold --from-search-log` leverer kandidaterne.

Konkret betyder det tre ting:

* **Modelvalget er begrundet, ikke bevist.** `multilingual-e5-small` er valgt
  fordi den er flersproget med dansk i træningsdata, lille og CPU-venlig — ikke
  fordi den er målt bedst på dansk juridisk sprog. En større E5-model, eller en
  dansk-specifik model, kan meget vel være bedre.
* **Vægtene i sammensmeltningen er et udgangspunkt.** 1,0 leksikalsk mod 0,8
  semantisk afspejler en vurdering af, at juridisk søgning oftere gælder en
  bestemt term end et tema. Det kan afkræftes med data.
* **Grænsen for hvad der tælles som et hit er modelafhængig.** E5 lægger de
  fleste par mellem 0,70 og 0,90; standardgrænsen 0,75 er sat derefter, men bør
  måles. `VECTOR_MIN_SIMILARITY` findes netop til det.

Den oplagte næste opgave er et lille evalueringssæt — 30-50 søgninger med
gennemgåede facitlister — og en kommando der måler recall og præcision for hver
tilstand. Først da kan vægte, grænser og modelvalg justeres på andet end skøn.

### Chunkeren er afprøvet på fixturmateriale

Opdelingen ved kapitel-, §- og stykkegrænser er testet mod syntetiske
lovtekster og bilagslignende tekst uden struktur. Rigtige ELI-dokumenter kan
have tabeller, bilag og opstillinger, hvor snittene falder mindre pænt. Fejlen
er ikke alvorlig — et skævt snit giver et mindre præcist uddrag, ikke tabt
tekst, da nabostykker overlapper — men chunkeren bør efterses, når et større
produktionsmateriale er importeret.

### Vektorsøgning uden pgvector skalerer ikke

Kører systemet på SQLite eller på et PostgreSQL uden pgvector, sammenlignes
vektorerne i Python. Det er korrekt, men arbejdet vokser lineært, og loftet
`VECTOR_FALLBACK_MAX_CHUNKS` (20.000 stykker) beskærer resultatet med en
advarsel i loggen frem for at lade en søgning tage tyve sekunder. Til drift af
hele Søfartsstyrelsens materiale skal `pgvector/pgvector:pg16` bruges — hvilket
`docker-compose.yml` allerede gør.

### Søgeloggen aggregerer og gemmer ikke forløb

`search_queries` har én række pr. normaliseret søgestreng med første og seneste
forekomst — ikke én række pr. hændelse. Tabellen kan derfor sige "søgt 40 gange,
aldrig med resultat", men ikke "søgt 30 gange i marts og 10 i august". Valget
holder tabellen lille og fri for persondata; skal udviklingen over tid følges,
kræver det en selvstændig hændelsestabel.

### Adgangskontrollen er ét delt token

Der er ingen brugerdatabase, og derfor heller ikke noget svar på *hvem* der
startede en import. `import_runs` registrerer kørslen, ikke personen. Skal
flere have hver sin adgang — og skal handlingerne kunne spores til en
person — skal `require_admin` i `backend/app/core/security.py` udskiftes.
Det er med vilje det eneste sted, ruterne kender til godkendelse.

### Rate limiting tælles pr. proces

`SlidingWindowLimiter` lever i backend-containerens hukommelse. Med én
container er det korrekt; med flere replikaer ville hver have sin egen
kvote, og den samlede grænse blive ganget op. En Redis-baseret udgave er
det naturlige næste skridt — grænsefladen (`check`) er holdt lille netop
derfor. Grænserne står desuden to steder, `.env` og `frontend/nginx.conf`,
og skal holdes i overensstemmelse manuelt.

### Øvrige begrænsninger

- **Fixturdata er syntetiske.** De 18 dokumenter er skrevet til udvikling og
  test. De er ikke gældende ret. De er markeret som sådan overalt.
- **Relevansmotoren er regelbaseret.** Den forstår ikke semantik. Et dokument,
  der behandler maritime forhold uden at bruge maritim terminologi, vil ikke
  blive fanget. Vektorlaget hjælper på *søgningen*, men ikke på *udvælgelsen*:
  et dokument der aldrig blev importeret, kan heller ikke findes semantisk. En
  `EmbeddingRelevanceEngine` er det oplagte næste skridt — abstraktionen findes
  allerede.
- **Vektorer bygges ikke under import.** Efter en import mangler de nye
  dokumenter vektorer, indtil `embed run` har kørt. Indtil da findes de kun
  leksikalsk. `embed status` og driftsvisningen viser hvor mange der mangler.
- **Ændringsloggen fortolker ikke juridisk.** Version 1 registrerer at noget
  ændrede sig, ikke hvad ændringen betyder retligt.
- **Ingen autentifikation.** Systemet er tiltænkt lokal kørsel. Skal det
  eksponeres, kræves adgangsstyring — særligt på `POST /api/import/run`.
- **Importen kører synkront.** Tilstrækkeligt til ændringsfeedens volumen, men
  et større genindlæsningsjob bør flyttes til en baggrundsworker.
- **Ingen planlægning.** Version 1 kører importen manuelt. Servicen er
  forberedt til cron eller worker, men planlæggeren er ikke bygget.

---

## 25. Fremtidige udvidelsespunkter

Arkitekturen er lagt an på disse udvidelser:

| Udvidelse | Hvor det gribes an |
|---|---|
| AI-baseret relevansvurdering | Ny klasse der opfylder `RelevanceEngine`. Kan genbruge `EmbeddingProvider` direkte |
| ~~Semantisk søgning~~ | **Implementeret.** Se [afsnit 16](#16-semantisk-søgning-vektorer) |
| ~~Måling af søgekvalitet~~ | **Harnessen er implementeret** — se [afsnit 20](#20-måling-af-søgekvalitet). Mangler: en facitliste for den rigtige samling |
| Cross-encoder-omrangering | Ny `RerankEngine`-protokol mellem sammensmeltning og visning. Bør først indføres, når evalueringssættet kan vise at den hjælper |
| BM25 frem for `ts_rank_cd` | PostgreSQLs rangering mangler IDF. Mærkbart på flerordssøgninger med almindelige ord |
| RAG og juridisk spørgsmål-svar | Grundlaget er lagt: `document_chunks` er allerede den passage-inddeling en RAG-kæde har brug for, og `search_queries` er en samling rigtige spørgsmål at evaluere imod |
| Bedre eller dansk-specifik model | Skift `EMBEDDING_MODEL` + `embed run --reset`. Ingen kodeændring |
| Ændringsanalyse | `document_versions` indeholder allerede fuld historik til diff |
| Relationer mellem regler | Ny tabel; `retsinformation_id` giver stabil nøgle |
| Planlagt import | `ImportService.run(since=…)` er statuløs og klar til cron eller worker |
| Adgangsstyring | Ét sted: FastAPI-dependencies på ruterne |

Nøglen er, at motorerne ligger bag protokoller. En `HybridAIRelevanceEngine`
eller en anden embedding-model kan indsættes uden ændringer i importer,
persistering eller API.

---

## Licens og kilde

Lovdata tilhører Retsinformation (Civilstyrelsen). Dette projekt er et lokalt
indekseringsværktøj og hverken erstatter eller videredistribuerer den
officielle kundgørelse.

**Kontrollér altid den gældende officielle tekst på
[retsinformation.dk](https://www.retsinformation.dk) ved juridisk anvendelse.**
