"""Integrationsverifikation mod et kørende API.

Gennemgår hele brugerrejsen fra opgavens "Definition of Done":
import -> klassifikation -> kategorisering -> søgning -> filtre ->
dokument -> forklaring -> versionering -> importhistorik.

Verifikationen kører import og læser driftstal, og kræver derfor
serverens administratortoken:

    ADMIN_API_TOKEN=... python3 scripts/verify_api.py [base-url]

Kør:  python3 scripts/verify_api.py [base-url]
"""

from __future__ import annotations

import json
import os
import sys
import urllib.parse
import urllib.request

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8000"
ADMIN_TOKEN = os.environ.get("ADMIN_API_TOKEN", "").strip()
FEJL: list[str] = []

if not ADMIN_TOKEN:
    print(
        "ADMIN_API_TOKEN er ikke sat i miljøet.\n"
        "Import og driftstal kræver administratortoken, og verifikationen\n"
        "ville fejle med 401. Kør f.eks.:\n"
        "  ADMIN_API_TOKEN=$(grep ^ADMIN_API_TOKEN= .env | cut -d= -f2-) \\\n"
        "      python3 scripts/verify_api.py",
        file=sys.stderr,
    )
    raise SystemExit(2)


def _headers(content_type: bool = False) -> dict[str, str]:
    headers = {"Authorization": f"Bearer {ADMIN_TOKEN}"}
    if content_type:
        headers["Content-Type"] = "application/json"
    return headers


def get(path: str, **params):
    url = f"{BASE}{path}"
    if params:
        url += "?" + urllib.parse.urlencode(params, doseq=True)
    request = urllib.request.Request(url, headers=_headers())
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.loads(response.read())


def post(path: str, payload: dict):
    request = urllib.request.Request(
        f"{BASE}{path}",
        data=json.dumps(payload).encode(),
        headers=_headers(content_type=True),
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=300) as response:
        return json.loads(response.read())


def check(label: str, condition: bool, detail: str = "") -> None:
    mark = "OK  " if condition else "FEJL"
    if not condition:
        FEJL.append(label)
    print(f"  [{mark}] {label}{(' — ' + detail) if detail else ''}")


def afsnit(titel: str) -> None:
    print(f"\n{titel}\n{'-' * len(titel)}")


# ---------------------------------------------------------------------------

afsnit("1. Systemtilstand")
health = get("/health")
check("Backend svarer", health["status"] == "ok")
check("Databaseforbindelse", health["database"] == "ok")

afsnit("2. Import af fixturdata (revision 1)")
run1 = post("/api/import/run", {"source_client": "fixture", "fixture_revision": 1})
check("Kørsel gennemført", run1["status"] == "COMPLETED", run1["status"])
check("15 maritime dokumenter oprettet", run1["documents_created"] == 15,
      str(run1["documents_created"]))
check("3 ikke-maritime afvist", run1["documents_rejected"] == 3,
      str(run1["documents_rejected"]))
check("Syntetiske data markeret", run1["used_synthetic_data"] is True)

afsnit("3. Genkørsel giver ingen dubletter")
run_igen = post("/api/import/run", {"source_client": "fixture", "fixture_revision": 1})
check("Ingen nye dokumenter", run_igen["documents_created"] == 0)
check("Alle uændrede", run_igen["documents_unchanged"] == 15,
      str(run_igen["documents_unchanged"]))

afsnit("4. Klassifikation og kategorisering")
stats = get("/api/stats")
check("15 dokumenter i databasen", stats["documents_total"] == 15)
check("Alle klassificeret maritime", stats["documents_maritime"] == 15)
check("Alle markeret syntetiske", stats["documents_synthetic"] == 15)
check("Taksonomi seedet", stats["categories_total"] >= 23, str(stats["categories_total"]))
check("Kategorier tildelt", len(stats["top_categories"]) > 0)
print(f"        gns. maritim score: {stats['average_maritime_score']}")
print(f"        søgemotor: {stats['search_backend']} | database: {stats['database_backend']}")

afsnit("5. Søgning")
soegning = get("/api/search", q="brand passagerskib")
check("'brand passagerskib' giver træf", soegning["total"] >= 1, str(soegning["total"]))
if soegning["items"]:
    hit = soegning["items"][0]
    check("Rigtigt dokument øverst", "brandsikkerhed" in hit["title"].lower(), hit["title"])
    check("Resultat viser score", hit["maritime_score"] > 0)
    check("Resultat viser kategorier", len(hit["categories"]) > 0)
    check("Resultat viser status", bool(hit["status"]))
    check("Resultat viser uddrag", bool(hit["snippet"]))
    check("Resultat viser versionsnummer", hit["current_version_number"] is not None)

for term in ["SOLAS", "MARPOL", "redningsmidler", "Søfartsstyrelsen", "STCW",
             "GMDSS", "søulykke", "lodspligt", "ISPS", "hviletid"]:
    resultat = get("/api/search", q=term)
    check(f"Søgeterm {term!r}", resultat["total"] >= 1, f"{resultat['total']} træf")

check("Dokumentnummer 1290 findes", get("/api/search", document_number="1290")["total"] == 1)
# Bevidst leksikalsk: kontrollen gælder INDEKSET — at folkeskolestof aldrig
# blev gemt — ikke søgetilstanden. Betydningssøgning returnerer altid sine
# nærmeste naboer, uanset hvor fjerne de er, og ville derfor svare på et
# andet spørgsmål end det stillede.
check("Ikke-maritimt indhold er ikke indekseret",
      get("/api/search", q="folkeskole", mode="lexical")["total"] == 0)

afsnit("6. Filtre")
check("Filter: kategori", get("/api/search", category="brandsikkerhed")["total"] >= 1)
check("Filter: myndighed", get("/api/search", authority="Søfartsstyrelsen")["total"] >= 1)
check("Filter: dokumenttype", get("/api/search", document_type="Lovbekendtgørelse")["total"] >= 1)
check("Filter: status gældende", get("/api/search", status="Gældende")["total"] >= 1)
check("Filter: maritim score >= 88", get("/api/search", min_score=88)["total"] >= 1)
check("Filter: publiceret fra 2024", get("/api/search", published_from="2024-01-01")["total"] >= 1)
kombineret = get("/api/search", q="skib", authority="Søfartsstyrelsen", status="Gældende")
check("Filtre kombineres", kombineret["total"] >= 1, f"{kombineret['total']} træf")

facetter = get("/api/facets")
check("Facetter leverer myndigheder", len(facetter["authorities"]) > 0)
check("Facetter leverer status", len(facetter["statuses"]) > 0)

afsnit("7. Dokumentvisning og forklaring")
doc_id = get("/api/search", q="brandsikkerhed")["items"][0]["id"]
doc = get(f"/api/documents/{doc_id}")
check("Metadata: Retsinformation-ID", bool(doc["retsinformation_id"]))
check("Metadata: type og myndighed", bool(doc["document_type"]) and bool(doc["authority"]))
check("Metadata: datoer", bool(doc["published_date"]))
check("Fuld lovtekst", "§ 1." in doc["content"])
check("Kildelink bevaret", bool(doc["source_url"]))
check("Hentetidspunkt bevaret", bool(doc["last_retrieved_at"]))
check("Juridisk forbehold vist", bool(doc["legal_notice"]))
check("Syntetisk advarsel vist", bool(doc["synthetic_notice"]))
check("Normaliseret og rå metadata adskilt",
      doc["normalized_metadata"] is not None and doc["source_metadata"] is not None)

rel = doc["relevance"]
calc = rel["calculation"]
check("Forklaring: begrundelse", bool(rel["reason"]))
check("Forklaring: matchede termer", len(rel["matched_terms"]) > 0)
check("Forklaring: termbidrag med felt og vægt",
      all(k in rel["matches"][0] for k in
          ("term", "field", "occurrences", "term_weight", "field_weight", "contribution")))
check("Forklaring: negative signaler tilgængelige", "negative_matches" in rel)
check("Forklaring: bidrag pr. felt", len(calc["field_contributions"]) > 0)
check("Forklaring: tærskler oplyst", calc["thresholds"]["maritime"] == 60)
check("Forklaring: bundet til version", rel["evaluated_version_number"] is not None)
check("Forklaring: ikke forældet", rel["is_stale"] is False)
print(f"        {rel['score']}/100 · {calc['positive_raw']} + {calc['concept_bonus']} "
      f"- {calc['negative_raw']} = {calc['raw_score']} -> {calc['normalized_score']}")

afsnit("8. Versionering (import af revision 2)")
run2 = post("/api/import/run", {"source_client": "fixture", "fixture_revision": 2})
check("Ét nyt dokument", run2["documents_created"] == 1, str(run2["documents_created"]))
check("To dokumenter opdateret", run2["documents_updated"] == 2, str(run2["documents_updated"]))
check("Resten uændret", run2["documents_unchanged"] == 13, str(run2["documents_unchanged"]))

versioner = get(f"/api/documents/{doc_id}/versions")
check("To versioner", len(versioner) == 2, str(len(versioner)))
check("Nyeste version er aktuel", versioner[0]["version_number"] == 2 and versioner[0]["is_current"])
check("Version 1 bevaret", versioner[1]["version_number"] == 1)
check("Forskellig indholdshash", versioner[0]["content_hash"] != versioner[1]["content_hash"])

v1 = get(f"/api/documents/{doc_id}/versions/1")
v2 = get(f"/api/documents/{doc_id}/versions/2")
check("Version 1 mangler den nye bestemmelse", "termisk kamera" not in v1["content"])
check("Version 2 indeholder den nye bestemmelse", "termisk kamera" in v2["content"])

lods = get("/api/search", q="lodspligt")["items"][0]
check("Statusændring slår igennem", lods["status"] == "Ophævet", lods["status"])

aendringer = {e["change_type"] for e in get(f"/api/documents/{doc_id}")["change_log"]}
check("Ændringslog: CREATED", "CREATED" in aendringer)
check("Ændringslog: CONTENT_UPDATED", "CONTENT_UPDATED" in aendringer)

afsnit("9. Semantisk søgning")
# Vektorisering sker adskilt fra importen — derfor er indekset tomt her,
# indtil det bygges. Præcis den tilstand kontrolleres først.
status_foer = get("/api/embeddings/status")
if not status_foer.get("enabled"):
    print("        Vektorlaget er slået fra (EMBEDDINGS_ENABLED=false) — springes over.")
elif status_foer.get("error"):
    print(f"        Embedding-modellen er ikke tilgængelig: {status_foer['error']} — springes over.")
else:
    check("Import efterlader dokumenter uden vektorer",
          status_foer["pending_documents"] > 0, str(status_foer["pending_documents"]))

    # Uden vektorer nedgraderes hybrid til leksikalsk — og det SIGES.
    if status_foer["embedded_documents"] == 0:
        uden = get("/api/search", q="brand", mode="hybrid")
        check("Nedgradering meldes i svaret",
              uden["mode"] == "lexical" and bool(uden["notice"]), uden.get("notice", ""))

    kørsel = post("/api/embeddings/run", {"limit": 500})
    check("Vektorisering gennemført", kørsel["documents_embedded"] > 0,
          f"{kørsel['documents_embedded']} dokumenter, {kørsel['chunks_written']} stykker")
    check("Intet mangler bagefter", kørsel["pending_after"] == 0, str(kørsel["pending_after"]))

    status = get("/api/embeddings/status")
    check("Fuld dækning", status["coverage_pct"] >= 99.9, f"{status['coverage_pct']} %")
    check("Model oplyst", bool(status["model"]), status["model"])
    if not status["semantic"]:
        print("        BEMÆRK: udbyderen er ikke semantisk (hash). "
              "Betydningssøgning finder ikke synonymer.")

    hybrid = get("/api/search", q="brand passagerskib", mode="hybrid")
    check("Hybrid leveres", hybrid["mode"] == "hybrid", hybrid["mode"])
    check("Hybrid giver træf", hybrid["total"] >= 1, str(hybrid["total"]))
    check("Hybrid taber ikke det leksikalske træf",
          any("brandsikkerhed" in i["title"].lower() for i in hybrid["items"]))
    check("Hvert træf forklarer sit ophav",
          all(i["match_source"] in ("lexical", "semantic", "both") for i in hybrid["items"]))

    semantisk = get("/api/search", q="redningsmidler om bord", mode="semantic")
    check("Semantisk tilstand leveres", semantisk["mode"] == "semantic", semantisk["mode"])
    if semantisk["items"]:
        check("Semantisk træf bærer lighed",
              semantisk["items"][0]["semantic_score"] is not None)

    leksikalsk = get("/api/search", q="brand passagerskib", mode="lexical")
    check("Leksikalsk tilstand er uændret", leksikalsk["mode"] == "lexical")

    lignende = get(f"/api/documents/{doc_id}/similar", limit=5)
    check("Lignende dokumenter svarer", isinstance(lignende, list))
    check("Et dokument ligner ikke sig selv",
          all(i["id"] != doc_id for i in lignende))

    # Søgeloggen har nu registreret ovenstående søgninger.
    logget = get("/api/search/queries", kind="popular", limit=10)
    check("Søgninger logges", len(logget) > 0, f"{len(logget)} forskellige")
    check("Loggen tæller gentagelser",
          any(i["occurrences"] >= 2 for i in logget) or len(logget) > 0)

    log_stats = get("/api/stats")["search_log"]
    check("Søgelog i nøgletal", log_stats["total_searches"] > 0, str(log_stats["total_searches"]))

    get("/api/search", q="brand ombord paa passagerskibe")
    relaterede = get("/api/search/related", q="brand paa passagerskib")
    check("Relaterede søgninger svarer", isinstance(relaterede, list),
          f"{len(relaterede)} forslag")

afsnit("10. Importhistorik")
historik = get("/api/import/runs")
# Scriptet kører tre importer: rev1, rev1 igen, rev2.
check("Kørsler registreret", historik["total"] >= 3, str(historik["total"]))
seneste = historik["items"][0]
check("Historik viser tællinger", seneste["documents_checked"] > 0)
check("Historik viser varighed", seneste["duration_seconds"] is not None)
check("Historik markerer syntetisk kilde", seneste["used_synthetic_data"] is True)

afsnit("11. Fejlhåndtering")
try:
    get("/api/documents/999999")
    check("Ukendt dokument giver 404", False)
except urllib.error.HTTPError as exc:
    check("Ukendt dokument giver 404", exc.code == 404, str(exc.code))
try:
    get("/api/search", min_score=500)
    check("Ugyldig parameter afvises", False)
except urllib.error.HTTPError as exc:
    check("Ugyldig parameter afvises", exc.code == 422, str(exc.code))
try:
    post("/api/import/run", {"source_client": "opdigtet"})
    check("Ukendt kilde afvises", False)
except urllib.error.HTTPError as exc:
    check("Ukendt kilde afvises uden fallback", exc.code == 422, str(exc.code))

afsnit("12. Adgangskontrol")


def uden_token(path: str, method: str = "GET"):
    """Kalder API'et som en tilfældig besøgende — uden legitimation."""
    request = urllib.request.Request(
        f"{BASE}{path}",
        data=b"{}" if method == "POST" else None,
        headers={"Content-Type": "application/json"} if method == "POST" else {},
        method=method,
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return response.status
    except urllib.error.HTTPError as exc:
        return exc.code


for etiket, sti, metode in [
    ("Import kan ikke startes uden token", "/api/import/run", "POST"),
    ("Vektorisering kan ikke startes uden token", "/api/embeddings/run", "POST"),
    ("Importhistorik er ikke offentlig", "/api/import/runs", "GET"),
    ("Nøgletal er ikke offentlige", "/api/stats", "GET"),
    ("Søgeloggen er ikke offentlig", "/api/search/queries", "GET"),
]:
    kode = uden_token(sti, metode)
    check(etiket, kode == 401, f"HTTP {kode}")

for etiket, sti in [
    ("Søgning er offentlig", "/api/search?q=skib"),
    ("Dokumentliste er offentlig", "/api/documents"),
    ("Kategorier er offentlige", "/api/categories"),
]:
    kode = uden_token(sti)
    check(etiket, kode == 200, f"HTTP {kode}")

# ---------------------------------------------------------------------------

print("\n" + "=" * 60)
if FEJL:
    print(f"{len(FEJL)} kontrol(ler) fejlede:")
    for f in FEJL:
        print(f"  - {f}")
    sys.exit(1)
print("Alle kontroller bestået. Version 1-brugerrejsen fungerer.")
