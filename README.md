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
├── backfill/          Historisk efterindlæsning via accessionsnumre
│   ├── manifest.py      Kø, reservation (lease) og fencing token
│   └── worker.py        Portionsvis kørsel gennem ImportService
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
│   │   │   └── backfill/   Kø og arbejder til historisk efterindlæsning
│   │   ├── cli.py          Kommandolinjegrænseflade
│   │   └── main.py         FastAPI-applikationen
│   ├── migrations/         Alembic
│   ├── tests/              168 tests
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
docker compose exec backend python -m app.cli import --source production
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
python -m app.cli import --source production # hent officielle ændringer
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

Syv tabeller:

| Tabel | Indhold |
|---|---|
| `documents` | Det logiske dokument: identitet, metadata, maritim klassifikation |
| `document_versions` | Uforanderlige indholdsversioner med SHA-256-hash |
| `categories` | Maritim taksonomi, seedet fra `config/categories.yaml` |
| `document_categories` | Mange-til-mange med `confidence` og matchede termer |
| `import_runs` | Én række pr. importkørsel med tællinger og fejl |
| `change_log` | `CREATED`, `CONTENT_UPDATED`, `METADATA_UPDATED`, `STATUS_CHANGED` |
| `backfill_manifest_items` | Kø til [historisk efterindlæsning](#historisk-efterindlæsning-backfill) med reservation og fencing token |

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
cd backend && python -m pytest          # 168 tests
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
køre importen dagligt. Ældre dokumenter kan hentes ved at angive deres
accessionsnummer eksplicit — det er formålet med
[efterindlæsningskøen](#historisk-efterindlæsning-backfill).

Køen løser *genoptagelse og samtidighed*, ikke opdagelse. Numrene skal komme
udefra: fra Lovtidende, fra en myndighedsoversigt, fra en eksisterende liste.
Systemet kan ikke selv finde ud af, hvilke accessionsnumre der findes.

Ønskes en fuld grunddatabase, kræver det en aftale med Civilstyrelsen om et
datadump eller en anden adgangsform.

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
