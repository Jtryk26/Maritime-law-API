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
17. [REST-API](#17-rest-api)
18. [Frontend](#18-frontend)
19. [Test](#19-test)
20. [Kendte begrænsninger](#20-kendte-begrænsninger)
21. [Fremtidige udvidelsespunkter](#21-fremtidige-udvidelsespunkter)

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
Vektorisering             → lovteksten deles i stykker og indlejres
      ↓
Søge-API                  → ord, betydning eller begge + facetfiltre
      ↓
Webgrænseflade            → søgning, dokumentvisning, drift
```

Retsinformation klassificerer ikke lovgivning efter en maritim taksonomi.
Systemets kerneopgave er derfor at afgøre **hvad der er maritimt relevant**,
og at kunne **forklare hvorfor** — systemet arbejder med lovgivning, hvor en
sort boks er ubrugelig.

Søgningen kan både finde **ordene** og **betydningen**. En maskinmester der
søger efter *livbåde*, skal også have *Bekendtgørelse om redningsmidler i
handelsskibe*, selv om ordet ikke står der. Se
[Semantisk søgning](#16-semantisk-søgning-vektorer).

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
│   │   ├── api/            Ruter, serialisering, fejlhåndtering
│   │   ├── core/           Konfiguration, logging, tekstbehandling
│   │   ├── db/             Session, seeding, migrationskørsel
│   │   ├── models/         SQLAlchemy-modeller
│   │   ├── schemas/        Pydantic-svarskemaer
│   │   ├── services/       Forretningslogik (se ovenfor)
│   │   │   ├── discovery/  Opdagelse af accessionsnumre + CSV-manifest
│   │   │   ├── backfill/   Kø og arbejder til historisk efterindlæsning
│   │   │   └── embedding/  Chunking, embedding-udbydere, indeksering
│   │   ├── cli.py          Kommandolinjegrænseflade
│   │   └── main.py         FastAPI-applikationen
│   ├── migrations/         Alembic
│   ├── tests/              411 tests
│   ├── requirements.txt
│   ├── requirements-embedding.txt   Lokal model (ca. 1,5 GB, valgfri)
│   └── Dockerfile
├── frontend/
│   ├── src/
│   │   ├── components/     Genbrugte visningskomponenter
│   │   ├── lib/            API-klient, formatering, routing
│   │   └── pages/          Søgning, dokument, import/drift
│   ├── nginx.conf
│   └── Dockerfile
├── config/
│   ├── maritime_keywords.yaml   Termer og vægte for relevansmotoren
│   └── categories.yaml          Maritim taksonomi
├── data/fixtures/               Syntetiske testdokumenter og søgeresultater
├── manifests/                   CSV-manifester fra `backfill discover`
├── scripts/verify_api.py        Integrationsverifikation
├── docker-compose.yml
├── .env.example
└── Makefile
```

---

## 5. Kom i gang med Docker

```bash
cp .env.example .env
docker compose up --build
```

Dette starter PostgreSQL (med pgvector), backend og frontend. Migrationer
køres automatisk, og den maritime taksonomi seedes ved opstart.

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
# Via brugerfladen: gå til "Import og drift" → "Kør import nu"
# Eller fra kommandolinjen:
docker compose exec backend python -m app.cli import --source production

# Byg derefter det semantiske indeks. Bevidst adskilt fra importen —
# se afsnit 16.
docker compose exec backend python -m app.cli embed run
docker compose exec backend python -m app.cli embed status
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

To migrationer: `0001_initial` (Version 1-skemaet) og `0002_backfill_manifest`
(efterindlæsningskøen).

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
maritim score fra/til og maritim klassifikation. Alle håndhæves i API'et, ikke
kun visuelt i frontenden. `/api/facets` leverer de tilgængelige værdier, så
brugerfladen ikke hardcoder dem.

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

## 17. REST-API

Interaktiv dokumentation på `/docs`.

| Metode | Sti | Beskrivelse |
|---|---|---|
| `GET` | `/api/search` | Søgning med facetfiltre og `mode=lexical\|semantic\|hybrid` |
| `GET` | `/api/search/related` | Tidligere søgninger der ligner en given |
| `GET` | `/api/search/queries` | Søgelog: `kind=popular\|without_results` |
| `GET` | `/api/documents` | Dokumentliste |
| `GET` | `/api/documents/{id}` | Metadata, tekst, kategorier, forklaring, versioner |
| `GET` | `/api/documents/{id}/versions` | Versionshistorik |
| `GET` | `/api/documents/{id}/similar` | Dokumenter der ligner dette (vektorlighed) |
| `GET` | `/api/documents/{id}/versions/{n}` | Indholdet af en bestemt version |
| `GET` | `/api/categories` | Taksonomi med dokumenttællinger |
| `GET` | `/api/facets` | Tilgængelige filterværdier |
| `GET` | `/api/stats` | Nøgletal |
| `POST` | `/api/import/run` | Kør en import |
| `GET` | `/api/import/runs` | Importhistorik |
| `GET` | `/api/embeddings/status` | Dækning og tilstand for det semantiske indeks |
| `POST` | `/api/embeddings/run` | Vektorisér de dokumenter der mangler |
| `GET` | `/health` | Systemtilstand |

SQLAlchemy-modeller returneres aldrig direkte; alle svar går gennem
Pydantic-skemaer. Fejl har ensartet format med `detail` og `error_type`.
Kildefejl oversættes til meningsfulde statuskoder: 503 ved midlertidig
utilgængelighed, 502 ved ugyldigt svar, 404 ved ukendt dokument.

---

## 18. Frontend

Tre sider:

**Søgeside** — søgefelt, valg mellem *Ordret*, *Betydning* og *Kombineret*,
facetfiltre i sidepanel, resultater med titel, type, myndighed, dato, status,
maritim score, kategorier og tekstuddrag. Hvert resultat er mærket med hvordan
det blev fundet (`Ordmatch`, `Betydningsmatch`, `Ord + betydning`) og med lighed
i procent. Under søgefeltet vises beslægtede søgninger fra søgeloggen. Filtre,
tilstand og side ligger i URL'en, så en søgning kan deles og genindlæses.

**Dokumentside** — metadata, gældende lovtekst, kategorier med confidence,
fuld relevansforklaring med termtabel og regnestykke, **lignende dokumenter**
fundet på vektorlighed, versionshistorik med mulighed for at åbne historiske
versioner, ændringslog og link til originalen på Retsinformation.

**Import og drift** — nøgletal, manuel import med eksplicit kildevalg,
detaljer om seneste kørsel inklusive fejl, fuld importhistorik, tilstanden for
det semantiske indeks med knap til at vektorisere det manglende, og søgeloggen
med både de hyppigste søgninger og dem der aldrig har givet et svar.

Fladen prioriterer informationstæthed og læsbar lovtekst frem for pynt.
Lovtekst sættes med serif; status og score har hver sin farvekodning, så de
kan skimmes. Syntetiske data markeres altid tydeligt.

---

## 19. Test

```bash
cd backend && python -m pytest          # 411 tests
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

Tests kører mod et skema oprettet med de rigtige Alembic-migrationer, ikke
`create_all` — så det testede skema er det, der udrulles.

Testene henter **aldrig** en embedding-model. De kører med den deterministiske
hash-udbyder (`EMBEDDING_PROVIDER=hashing`, sat i `conftest.py`), så hele
rørføringen — chunking, lagring, sammensmeltning, søgelog — kan afprøves
reproducerbart uden netværk og uden torch. Adapteren omkring den rigtige model
er testet mod et stand-in, og HTTP-udbyderen mod en `MockTransport`. Hvad der
bevidst *ikke* testes, er modelkvalitet: en påstand om at "livbåd ligner
redningsflåde" ville med hash-udbyderen måle støj. Sprogkvalitet hører til i en
evaluering mod et sæt kendte søgninger — se [begrænsninger](#20-kendte-begrænsninger).

Opgavespecifikationens hovedkrav er dækket eksplicit:
`"Bekendtgørelse om sikkerhed på passagerskibe"` → maritimt;
`"Bekendtgørelse om folkeskolens undervisning"` → ikke maritimt.

### Integrationsverifikation

```bash
python3 scripts/verify_api.py http://localhost:8000
```

Gennemgår hele brugerrejsen mod et kørende API: import, genkørsel uden
dubletter, klassifikation, søgning, filtre, dokumentvisning, forklaring,
versionering og fejlhåndtering. Verificeret mod **både PostgreSQL 16 og
SQLite**.

---

## 20. Kendte begrænsninger

Disse forhold er reelle og bør kendes, før systemet sættes i drift.

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

### Betydningssøgningens kvalitet er ikke målt

Vektorlaget er implementeret, testet og kørende, men **søgekvaliteten er ikke
evalueret mod et sæt kendte søgninger**. Der findes ikke et facit for "hvilke
bekendtgørelser burde en maskinmester få, når han søger efter *lækagealarm i
maskinrum*", og uden et sådant sæt er enhver påstand om at systemet "finder de
rigtige dokumenter" et postulat.

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

## 21. Fremtidige udvidelsespunkter

Arkitekturen er lagt an på disse udvidelser:

| Udvidelse | Hvor det gribes an |
|---|---|
| AI-baseret relevansvurdering | Ny klasse der opfylder `RelevanceEngine`. Kan genbruge `EmbeddingProvider` direkte |
| ~~Semantisk søgning~~ | **Implementeret.** Se [afsnit 16](#16-semantisk-søgning-vektorer) |
| Måling af søgekvalitet | Evalueringssæt med facitlister + en `embed evaluate`-kommando. Forudsætningen for at justere vægte og grænser på andet end skøn |
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
