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
16. [REST-API](#16-rest-api)
17. [Frontend](#17-frontend)
18. [Test](#18-test)
19. [Kendte begrænsninger](#19-kendte-begrænsninger)
20. [Fremtidige udvidelsespunkter](#20-fremtidige-udvidelsespunkter)

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
Søge-API                  → fritekst + facetfiltre
      ↓
Webgrænseflade            → søgning, dokumentvisning, drift
```

Retsinformation klassificerer ikke lovgivning efter en maritim taksonomi.
Systemets kerneopgave er derfor at afgøre **hvad der er maritimt relevant**,
og at kunne **forklare hvorfor** — systemet arbejder med lovgivning, hvor en
sort boks er ubrugelig.

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
├── search/            Søgning
│   ├── base.py          SearchBackend (Protocol) + SearchQuery
│   └── backends.py      PostgresSearchBackend + FallbackSearchBackend
└── matching.py        Fælles termmatchning for relevans og kategorisering
```

To principper bærer designet:

**Kilden er isoleret.** Resten af applikationen kender kun
`NormalizedDocument` og `SourceClient`. Ingen anden kode ser Retsinformations
JSON- eller XML-strukturer.

**Motorerne kan udskiftes.** `RelevanceEngine` og `CategorizationEngine` er
protokoller. En senere `HybridAIRelevanceEngine` kan indsættes uden ændringer i
importer, persistering eller API.

---

## 3. Teknologivalg

| Lag | Valg | Begrundelse |
|---|---|---|
| Backend | Python 3.12 + FastAPI | Typede skemaer, automatisk OpenAPI-dokumentation |
| ORM | SQLAlchemy 2.0 | Understøtter både PostgreSQL og SQLite fra samme model |
| Migrationer | Alembic | Reproducerbart skema; ingen `create_all` i drift |
| Database | PostgreSQL 16 | Indbygget dansk fuldtekstsøgning (`to_tsvector('danish', …)`) |
| Søgning | PostgreSQL FTS | Ingen grund til Elasticsearch ved dette datavolumen |
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
│   │   ├── cli.py          Kommandolinjegrænseflade
│   │   └── main.py         FastAPI-applikationen
│   ├── migrations/         Alembic
│   ├── tests/              131 tests
│   ├── requirements.txt
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
├── data/fixtures/               Syntetiske testdokumenter
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

Dette starter PostgreSQL, backend og frontend. Migrationer køres automatisk,
og den maritime taksonomi seedes ved opstart.

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
docker compose exec backend python -m app.cli import --source fixture

# Kør derefter revision 2 for at se versionering i praksis:
docker compose exec backend python -m app.cli import --source fixture --fixture-revision 2
```

Søg derefter efter `brand passagerskib` i frontenden.

---

## 6. Kom i gang uden Docker

Systemet kører på SQLite uden PostgreSQL — praktisk til udvikling.

```bash
# Backend
pip install -r backend/requirements.txt
export DATABASE_URL="sqlite:///./data/maritime.db"

cd backend
python -m app.cli migrate                 # opret skema
python -m app.cli import --source fixture # hent testdata
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
| `SOURCE_CLIENT` | `fixture` | `fixture` eller `production`. Se advarsel nedenfor |
| `RETSINFORMATION_BASE_URL` | `https://api.retsinformation.dk` | Officiel høsteservice |
| `RETSINFORMATION_MIN_REQUEST_INTERVAL_SECONDS` | `10` | Kildens dokumenterede grænse. Sænk ikke uden aftale |
| `IMPORT_STORE_MIN_SCORE` | `30` | Dokumenter under denne score gemmes ikke |
| `RUN_MIGRATIONS_ON_STARTUP` | `true` | Praktisk i Docker |
| `LOG_LEVEL` | `INFO` | |

**Retsinformations høsteservice kræver ingen API-nøgle.** Der er derfor
bevidst ingen `RETSINFORMATION_API_KEY`.

---

## 8. Database og migrationer

Seks tabeller:

| Tabel | Indhold |
|---|---|
| `documents` | Det logiske dokument: identitet, metadata, maritim klassifikation |
| `document_versions` | Uforanderlige indholdsversioner med SHA-256-hash |
| `categories` | Maritim taksonomi, seedet fra `config/categories.yaml` |
| `document_categories` | Mange-til-mange med `confidence` og matchede termer |
| `import_runs` | Én række pr. importkørsel med tællinger og fejl |
| `change_log` | `CREATED`, `CONTENT_UPDATED`, `METADATA_UPDATED`, `STATUS_CHANGED` |

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

## 16. REST-API

Interaktiv dokumentation på `/docs`.

| Metode | Sti | Beskrivelse |
|---|---|---|
| `GET` | `/api/search` | Fritekstsøgning med facetfiltre |
| `GET` | `/api/documents` | Dokumentliste |
| `GET` | `/api/documents/{id}` | Metadata, tekst, kategorier, forklaring, versioner |
| `GET` | `/api/documents/{id}/versions` | Versionshistorik |
| `GET` | `/api/documents/{id}/versions/{n}` | Indholdet af en bestemt version |
| `GET` | `/api/categories` | Taksonomi med dokumenttællinger |
| `GET` | `/api/facets` | Tilgængelige filterværdier |
| `GET` | `/api/stats` | Nøgletal |
| `POST` | `/api/import/run` | Kør en import |
| `GET` | `/api/import/runs` | Importhistorik |
| `GET` | `/health` | Systemtilstand |

SQLAlchemy-modeller returneres aldrig direkte; alle svar går gennem
Pydantic-skemaer. Fejl har ensartet format med `detail` og `error_type`.
Kildefejl oversættes til meningsfulde statuskoder: 503 ved midlertidig
utilgængelighed, 502 ved ugyldigt svar, 404 ved ukendt dokument.

---

## 17. Frontend

Tre sider:

**Søgeside** — søgefelt, facetfiltre i sidepanel, resultater med titel, type,
myndighed, dato, status, maritim score, kategorier og tekstuddrag. Filtre og
side ligger i URL'en, så en søgning kan deles og genindlæses.

**Dokumentside** — metadata, gældende lovtekst, kategorier med confidence,
fuld relevansforklaring med termtabel og regnestykke, versionshistorik med
mulighed for at åbne historiske versioner, ændringslog og link til originalen
på Retsinformation.

**Import og drift** — nøgletal, manuel import med eksplicit kildevalg,
detaljer om seneste kørsel inklusive fejl, og fuld importhistorik.

Fladen prioriterer informationstæthed og læsbar lovtekst frem for pynt.
Lovtekst sættes med serif; status og score har hver sin farvekodning, så de
kan skimmes. Syntetiske data markeres altid tydeligt.

---

## 18. Test

```bash
cd backend && python -m pytest          # 131 tests
```

| Fil | Dækker |
|---|---|
| `test_relevance_engine.py` | Scoring, anti-spam, falske positiver, forklarbarhed |
| `test_categorization.py` | Taksonomi, konfidens, fallback |
| `test_document_versioning.py` | Hashing, versionsforløb, statusændring |
| `test_importer.py` | Idempotens, afvisning, fejlisolering, sporbarhed |
| `test_search.py` | Fritekst, filtre, sortering, sideinddeling |
| `test_source_clients.py` | Normalisering, XML-parser, HTTP-adfærd, kildevalg |
| `test_api.py` | Alle endpoints, validering, fejlkoder |

Tests kører mod et skema oprettet med de rigtige Alembic-migrationer, ikke
`create_all` — så det testede skema er det, der udrulles.

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

## 19. Kendte begrænsninger

Disse forhold er reelle og bør kendes, før systemet sættes i drift.

### Høsteservicen er en ændringsfeed, ikke et katalog

Det officielle API leverer kun dokumenter ændret inden for de seneste 10
kalenderdage. Der findes **ikke** i den officielle dokumentation et endpoint,
der lister hele lovsamlingen eller tillader fritekstsøgning i kilden.

Konsekvens: **en fuld historisk backfill af al maritim lovgivning er ikke
mulig via det dokumenterede API alene.** Databasen bygges op over tid ved at
køre importen dagligt. Enkelte ældre dokumenter kan hentes ved at angive deres
accessionsnummer eksplicit (understøttet i klientens `explicit_ids`).

Ønskes en fuld grunddatabase, kræver det en aftale med Civilstyrelsen om et
datadump eller en anden adgangsform.

### Produktionsconnectoren er ikke kørt mod det live API

Udviklingsmiljøet havde ikke netværksadgang til `retsinformation.dk`.
Kontrakten følger den officielle vejledning og er testet mod mocket HTTP:
ændringsfeedens svarformat, 404, 400 (åbningstid), 429 (rate limit), 5xx med
retry og backoff, samt 10-dages begrænsningen. **Den bør verificeres mod
produktion, før den tages i drift.**

Alt andet — importer, versionering, klassifikation, kategorisering, søgning,
API og frontend — er kørt og verificeret.

### ELI-XML-skemaet er ikke formelt verificeret

Det præcise XML-skema for dokumentteksten kunne ikke verificeres. Parseren er
derfor bevidst tolerant: den leder efter kendte elementnavne uafhængigt af
namespace, falder tilbage til at udtrække al tekst, og behandler ugyldig XML
som ren tekst. En skemaændring hos kilden giver dermed dårligere metadata frem
for et nedbrud i importen. Når skemaet er verificeret, bør
`FIELD_CANDIDATES` i `xml_parser.py` strammes op.

### Øvrige begrænsninger

- **Fixturdata er syntetiske.** De 18 dokumenter er skrevet til udvikling og
  test. De er ikke gældende ret. De er markeret som sådan overalt.
- **Relevansmotoren er regelbaseret.** Den forstår ikke semantik. Et dokument,
  der behandler maritime forhold uden at bruge maritim terminologi, vil ikke
  blive fanget. Konfigurationen kan udvides løbende.
- **Ændringsloggen fortolker ikke juridisk.** Version 1 registrerer at noget
  ændrede sig, ikke hvad ændringen betyder retligt.
- **Ingen autentifikation.** Systemet er tiltænkt lokal kørsel. Skal det
  eksponeres, kræves adgangsstyring — særligt på `POST /api/import/run`.
- **Importen kører synkront.** Tilstrækkeligt til ændringsfeedens volumen, men
  et større genindlæsningsjob bør flyttes til en baggrundsworker.
- **Ingen planlægning.** Version 1 kører importen manuelt. Servicen er
  forberedt til cron eller worker, men planlæggeren er ikke bygget.

---

## 20. Fremtidige udvidelsespunkter

Arkitekturen er lagt an på disse udvidelser:

| Udvidelse | Hvor det gribes an |
|---|---|
| AI-baseret relevansvurdering | Ny klasse der opfylder `RelevanceEngine`. Importeren ændres ikke |
| Semantisk søgning | `pgvector` + ny `SearchBackend`. Kontrakten er allerede på plads |
| RAG og juridisk spørgsmål-svar | Versionerede tekster med stabile hashes er et velegnet grundlag |
| Ændringsanalyse | `document_versions` indeholder allerede fuld historik til diff |
| Relationer mellem regler | Ny tabel; `retsinformation_id` giver stabil nøgle |
| Planlagt import | `ImportService.run(since=…)` er statuløs og klar til cron eller worker |
| Adgangsstyring | Ét sted: FastAPI-dependencies på ruterne |

Nøglen er, at motorerne ligger bag protokoller. En `HybridAIRelevanceEngine`
kan indsættes uden ændringer i importer, persistering eller API.

---

## Licens og kilde

Lovdata tilhører Retsinformation (Civilstyrelsen). Dette projekt er et lokalt
indekseringsværktøj og hverken erstatter eller videredistribuerer den
officielle kundgørelse.

**Kontrollér altid den gældende officielle tekst på
[retsinformation.dk](https://www.retsinformation.dk) ved juridisk anvendelse.**
