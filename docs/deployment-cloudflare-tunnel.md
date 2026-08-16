# Offentlig udgivelse med Cloudflare Tunnel

Sådan gøres Maritim Lovdatabase tilgængelig på internettet fra en maskine,
der står hjemme — uden at åbne en eneste port i routeren.

Dokumentet er skrevet til at blive fulgt oppefra og ned. Rækkefølgen er
ikke tilfældig: **systemet lukkes først, og åbnes derefter ét sted.**

---

## Indhold

1. [Hvorfor tunnel og ikke portvideresending](#1-hvorfor-tunnel-og-ikke-portvideresending)
2. [Hvad der er beskyttet, og hvad der er offentligt](#2-hvad-der-er-beskyttet-og-hvad-der-er-offentligt)
3. [Trin 1 — Klargør .env](#3-trin-1--klargør-env)
4. [Trin 2 — Køb domænet og læg det i Cloudflare](#4-trin-2--køb-domænet-og-læg-det-i-cloudflare)
5. [Trin 3 — Opret tunnelen](#5-trin-3--opret-tunnelen)
6. [Trin 4 — Start systemet bag tunnelen](#6-trin-4--start-systemet-bag-tunnelen)
7. [Trin 5 — Kontrollér at det er lukket](#7-trin-5--kontrollér-at-det-er-lukket)
8. [Daglig drift](#8-daglig-drift)
9. [Valgfrit: Cloudflare Access foran driftssiden](#9-valgfrit-cloudflare-access-foran-driftssiden)
10. [Fejlsøgning](#10-fejlsøgning)
11. [Hvad der bevidst ikke er lavet](#11-hvad-der-bevidst-ikke-er-lavet)

---

## 1. Hvorfor tunnel og ikke portvideresending

Ved portvideresending åbner routeren en dør ind til hjemmenettet, og
maskinens IP-adresse bliver offentlig. Alt hvad der lytter bag den dør —
også ting man har glemt — bliver en del af angrebsfladen.

Cloudflare Tunnel vender retningen om. `cloudflared` kører i en container
og etablerer en **udgående** forbindelse til Cloudflare. Trafikken kommer
ind gennem den forbindelse. Der åbnes ingen port, routeren ændres ikke, og
hjemmets IP-adresse offentliggøres ikke.

```text
Bruger
   │  HTTPS til maritimlov.dk
   ▼
Cloudflare
   │  ▲ udgående forbindelse, oprettet af os
   ▼  │
cloudflared ──► frontend (nginx) ──► backend (FastAPI) ──► PostgreSQL
   └────────────── alt dette er inde i Docker-netværket ─────────────┘
```

Kun `frontend` udstilles. Backend og database er ikke nåelige udefra og
har ikke engang en portbinding på værtsmaskinen i denne opsætning.

---

## 2. Hvad der er beskyttet, og hvad der er offentligt

Adgangskontrollen ligger i API'et. Det er værd at holde fast i: at skjule
en side i brugerfladen er ikke sikkerhed — at afvise kaldet er.

**Offentligt — ingen legitimation:**

| Endepunkt | Hvorfor |
|---|---|
| `GET /api/search` | Selve formålet med tjenesten |
| `GET /api/documents`, `/api/documents/{id}` | Lovtekst er offentlig |
| `GET /api/documents/{id}/versions` | Versionshistorik hører til dokumentet |
| `GET /api/categories`, `/api/facets` | Filtrene i søgefladen |
| `GET /health` | Docker og overvågning |

**Kræver `Authorization: Bearer <ADMIN_API_TOKEN>`:**

| Endepunkt | Hvorfor |
|---|---|
| `POST /api/import/run` | Skriver til databasen og kalder Retsinformation |
| `POST /api/embeddings/run` | Tung beregning; kan bruges til at lamme serveren |
| `GET /api/import/runs`, `/api/import/runs/{id}` | Driftsdata |
| `GET /api/stats` | Afslører opsætning og datamængder |
| `GET /api/embeddings/status` | Driftsdata |
| `GET /api/search/queries`, `/api/search/related` | Søgeloggen |

Søgeloggen indeholder hverken bruger, IP-adresse eller session — men den
viser hvad et navngivet sted interesserer sig for, og hvad materialet
mangler. Den hører ikke til på et offentligt web.

Bemærk desuden:

* Er `ADMIN_API_TOKEN` **tom**, svarer de beskyttede endepunkter `503`.
  De står altså aldrig åbne, fordi nogen glemte at sætte den.
* Med `ENVIRONMENT=production` **nægter backenden at starte** uden et
  token på mindst 24 tegn.
* Driftssiden i brugerfladen ligger på `#/drift` og er ikke linket fra
  navigationen. Den beder om tokenet, før den viser noget.

---

## 3. Trin 1 — Klargør `.env`

```bash
cp .env.example .env        # hvis du ikke allerede har en
make admin-token            # generér et administratortoken
```

Skriv i `.env`:

```dotenv
ENVIRONMENT=production
ADMIN_API_TOKEN=<det genererede token>
POSTGRES_PASSWORD=<et nyt, langt kodeord — ikke "maritim">
EXPOSE_API_DOCS=false
CORS_ORIGINS=
TRUST_PROXY_HEADERS=true
TUNNEL_TOKEN=          # udfyldes i trin 3
```

`CORS_ORIGINS` skal være **tom**. nginx serverer både brugerfladen og
`/api` fra samme domæne, så browseren har ikke brug for CORS — og en tom
liste betyder, at intet andet websted må kalde API'et fra en browser.

Kontrollér undervejs:

```bash
make deploy-check
```

Scriptet gennemgår de fejl, der er nemme at begå og dyre at opdage
bagefter: manglende token, databasens standardkodeord, en `.env` der er
kommet med i git.

> `.env` er i `.gitignore` og må aldrig committes. Tokenet er et kodeord.

---

## 4. Trin 2 — Køb domænet og læg det i Cloudflare

1. Køb et `.dk`-domæne hos en dansk registrator (DK Hostmaster-forhandler).
   `maritimlov.dk` er et fint valg, hvis det er ledigt — kort, sigende og
   uden stavefælder. Kontrollér ledigheden hos registratoren; den kan
   ændre sig fra dag til dag.
2. Opret en gratis konto på Cloudflare.
3. **Add a site** → indtast domænet → vælg **Free**.
4. Cloudflare viser to navneservere. Skift domænets navneservere til dem
   hos din registrator.
5. Vent til Cloudflare markerer domænet som **Active**. Det tager typisk
   fra få minutter til nogle timer.

Opret **ingen** Quick Tunnel undervejs. En Quick Tunnel giver en offentlig
`trycloudflare.com`-adresse med det samme — og dermed en offentlig
adresse til et system, der endnu ikke er klar.

---

## 5. Trin 3 — Opret tunnelen

I Cloudflare-dashboardet: **Zero Trust → Networks → Tunnels → Create a
tunnel → Cloudflared**.

1. Giv tunnelen et navn, f.eks. `maritim-lovdatabase`.
2. Cloudflare viser en installationskommando med et langt token.
   Kopiér **kun tokenværdien** og skriv den i `.env`:

   ```dotenv
   TUNNEL_TOKEN=<den lange tokenstreng>
   ```

   Vi bruger ikke kommandoen fra dashboardet — `cloudflared` kører som en
   container i vores egen compose-fil og læser tokenet fra miljøet, hvor
   det ikke er synligt i `docker ps`.

3. Under **Public Hostnames** → **Add a public hostname**:

   | Felt | Værdi |
   |---|---|
   | Subdomain | *(tomt, eller f.eks. `www`)* |
   | Domain | `maritimlov.dk` |
   | Type | `HTTP` |
   | URL | `frontend:80` |

   `frontend:80` er containernavnet på Docker-netværket. Peg **aldrig** på
   `backend:8000` — det ville omgå nginx og dermed både rate limiting og
   sikkerhedsheaderne. Og aldrig på `db:5432`.

---

## 6. Trin 4 — Start systemet bag tunnelen

```bash
make tunnel-up
```

Kommandoen kører `deploy-check` først og starter derefter:

```bash
docker compose -f docker-compose.yml -f docker-compose.tunnel.yml up -d --build
```

> Kræver **Docker Compose 2.24 eller nyere** — overlejringen bruger
> `!reset` til at fjerne portbindingerne, og ældre versioner ignorerer
> mærket. Kontrollér med `docker compose version`.

Overlejringen `docker-compose.tunnel.yml`:

* tilføjer `cloudflared`,
* **fjerner portbindingerne** på `db`, `backend` og `frontend`,
* sætter `ENVIRONMENT=production`, `EXPOSE_API_DOCS=false` og tom `CORS_ORIGINS`,
* nægter at starte, hvis `ADMIN_API_TOKEN`, `POSTGRES_PASSWORD` eller
  `TUNNEL_TOKEN` mangler.

Følg tunnelen:

```bash
make tunnel-logs
```

En fungerende forbindelse skriver `Registered tunnel connection` — typisk
fire gange, én pr. Cloudflare-datacenter.

---

## 7. Trin 5 — Kontrollér at det er lukket

Kør dette **fra en anden maskine end serveren**, f.eks. en telefon på
mobildata.

```bash
# 1. Søgning virker for alle
curl -s https://maritimlov.dk/api/search?q=skib | head -c 200

# 2. Import kan IKKE startes udefra  -> forventet: 401
curl -s -o /dev/null -w '%{http_code}\n' \
     -X POST https://maritimlov.dk/api/import/run

# 3. Driftstal er lukkede            -> forventet: 401
curl -s -o /dev/null -w '%{http_code}\n' https://maritimlov.dk/api/stats

# 4. Søgeloggen er lukket            -> forventet: 401
curl -s -o /dev/null -w '%{http_code}\n' https://maritimlov.dk/api/search/queries

# 5. API-dokumentationen er væk      -> forventet: 404
curl -s -o /dev/null -w '%{http_code}\n' https://maritimlov.dk/docs

# 6. Rate limiting virker            -> forventet: 429 til sidst
for i in $(seq 1 60); do
  curl -s -o /dev/null -w '%{http_code} ' "https://maritimlov.dk/api/search?q=test$i"
done; echo

# 7. Databasen er ikke nåelig        -> forventet: timeout/refused
nc -vz maritimlov.dk 5432
```

Kør også hele brugerrejsen mod serveren selv:

```bash
ADMIN_API_TOKEN=$(grep '^ADMIN_API_TOKEN=' .env | cut -d= -f2-) \
    python3 scripts/verify_api.py http://127.0.0.1:8080
```

Scriptets afsnit 12 kontrollerer netop, at driftsendepunkterne svarer 401
uden token, mens søgningen er åben.

---

## 8. Daglig drift

**Åbn driftssiden:** `https://maritimlov.dk/#/drift`, indsæt
`ADMIN_API_TOKEN`, klik **Lås op**. Tokenet ligger kun i den åbne fane og
forsvinder, når den lukkes. **Lås igen** rydder det med det samme.

Almindelige brugere ser hverken siden eller et link til den.

**Fra kommandolinjen på serveren** — ofte nemmere til de lange kørsler:

```bash
TUNNEL="-f docker-compose.yml -f docker-compose.tunnel.yml"

docker compose $TUNNEL exec backend python -m app.cli import --source production
docker compose $TUNNEL exec backend python -m app.cli embed run
docker compose $TUNNEL exec backend python -m app.cli stats
docker compose $TUNNEL logs -f backend
```

**Skift token:** generér et nyt med `make admin-token`, skriv det i `.env`
og genstart backenden. Alle åbne driftsfaner mister adgangen med det
samme, næste gang de kalder API'et.

```bash
docker compose $TUNNEL up -d --force-recreate backend
```

**Sikkerhedskopi af databasen:**

```bash
docker compose $TUNNEL exec db pg_dump -U maritim maritim | gzip > backup-$(date +%F).sql.gz
```

**Stop igen:** `make tunnel-down`.

---

## 9. Valgfrit: Cloudflare Access foran driftssiden

Administratortokenet er den egentlige beskyttelse. Vil du have et lag mere
— så en fremmed ikke engang kan nå login-formularen — kan Cloudflare
Access lægges foran:

**Zero Trust → Access → Applications → Add an application → Self-hosted**

| Felt | Værdi |
|---|---|
| Application domain | `maritimlov.dk` |
| Path | `api/import` *(gentag for `api/stats`, `api/embeddings`, `api/search/queries`)* |
| Policy | Allow → Emails → din egen e-mail |

Bemærk, at driftssiden bruger hash-routing (`#/drift`). Fragmentet sendes
aldrig til serveren, så Access kan **ikke** filtrere på det — regler skal
sættes på API-stierne, som er dem der betyder noget.

Access erstatter ikke tokenet. Bliver en Access-session efterladt åben på
en fælles computer, er tokenet stadig det, der forhindrer en import.

---

## 10. Fejlsøgning

**`cloudflared` kan ikke forbinde**

```bash
make tunnel-logs
```

`Unauthorized: Failed to get tunnel` betyder næsten altid et forkert eller
afkortet `TUNNEL_TOKEN`. Hent det igen i dashboardet — det er meget langt,
og en manglende sidste linje er den typiske fejl.

**502 fra Cloudflare**

Public hostname peger et forkert sted hen. Værdien skal være `frontend:80`
— containernavnet, ikke `localhost` og ikke maskinens IP.

**Backenden vil ikke starte**

```text
ADMIN_API_TOKEN er ikke sat ...
```

Det er med vilje. `ENVIRONMENT=production` kræver et token på mindst 24
tegn. Kør `make admin-token`.

**429 under almindelig brug**

Grænserne står to steder og skal følges ad:

* `RATE_LIMIT_SEARCH_PER_MINUTE` / `RATE_LIMIT_REQUESTS_PER_MINUTE` i `.env`
* `limit_req_zone` øverst i `frontend/nginx.conf`

Bruger en hel klasse tjenesten samtidig fra skolens net, deler de i mange
tilfælde én udgående adresse. Hæv grænsen, eller sæt skolens adresse på en
undtagelse i Cloudflare.

**Alle brugere rammer samme kvote**

Så bliver `CF-Connecting-IP` ikke sendt videre. Kontrollér `proxy_set_header
CF-Connecting-IP` i `frontend/nginx.conf` og `TRUST_PROXY_HEADERS=true` i
backendens miljø.

---

## 11. Hvad der bevidst ikke er lavet

Ærlighed om afgrænsningen er en del af leverancen:

* **Ingen brugerdatabase.** Ét delt administratortoken. Skal flere personer
  have hver sin adgang, skal `require_admin` i
  `backend/app/core/security.py` udskiftes — det er det eneste sted,
  ruterne kender.
* **Rate limiting er pr. proces.** Tælleren lever i backend-containeren.
  Med flere replikaer skal `SlidingWindowLimiter` erstattes af en
  Redis-baseret udgave. Grænsefladen er holdt lille netop derfor.
* **Ingen revisionslog over administratorhandlinger.** Importkørsler
  registreres i `import_runs`, men ikke *hvem* der startede dem — med ét
  token findes svaret ikke.
* **Tokenet ligger i `sessionStorage`.** Kunne en angriber køre JavaScript
  på siden, kunne vedkommende læse det. Beskyttelsen mod det er
  Content-Security-Policy'en i `frontend/security-headers.conf`.
* **Ingen WAF-regler.** Cloudflares gratis plan har grundlæggende
  beskyttelse; egentlige WAF-regler er ikke sat op.
* **Ingen automatisk sikkerhedskopi.** Kommandoen findes i afsnit 8, men
  der er ikke sat en tidsplan op.
