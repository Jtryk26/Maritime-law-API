# M3 — curated EUR-Lex integration

This milestone extends the merged PR #31 rights/provenance architecture. It
uses `SourceClient` → `ImportService` → `DocumentRepository` →
`Document`/`DocumentVersion`, the existing opt-in instrument catalogue, and
`RightsService`. There is no parallel document store, automatic harvest,
applicability-rule generation, deployment, or M4 work.

## Migration and identity

The baseline is main `1f2da6b6b5534c926053d9ba1a863d9161896d94` (PR #31).
The verified linear Alembic chain ends at `0011_source_rights`, after
`0010_document_relations`. New revision `0012_eurlex_provenance` adds source
edition JSON, retrieval URL, response-hash scope and a version-bound
`rights_context` snapshot (attribution, legal basis, evidence hash, unresolved
conditions and restrictions) to `legal_provenance`.
Existing provenance receives empty edition metadata and retains its original
`normalized_metadata` hash scope. Earlier migrations are unchanged.

An instrument's stable `source_id` is its original CELEX ID. Its ELI `/oj`
reference and CELEX ID are retained in `InstrumentIdentifier`. Each version
also records the exact requested CELEX edition, original/consolidated kind,
consolidation date where applicable, selected language, and original ELI
reference. A consolidated edition is not presented as the original text or
as an authoritative replacement for the Official Journal. Its exact source
link uses its edition CELEX. No consolidated ELI URI is invented.

A changed edition/language creates a new `DocumentVersion`, including for
metadata-only documents or identical normalized text. A changed permission
review also creates a new version when text is acquired. Historical bodies,
source hashes and evidence bindings remain attached to their prior versions.
One language per act is selected in each run; switching language retains the
previous expression as a version. M3 does not provide simultaneous multilingual
presentation or automatic latest-consolidation discovery.

## Official access and the reported AWS WAF challenge

Official reference starting points:

- [Publications Office Cellar data](https://op.europa.eu/en/web/cellar/cellar-data)
- [Cellar SPARQL endpoint](https://publications.europa.eu/webapi/rdf/sparql)
- [EUR-Lex data reuse and web services](https://eur-lex.europa.eu/content/help/data-reuse/webservice.html)
- [EUR-Lex legal notice](https://eur-lex.europa.eu/content/legal-notice/legal-notice.html)

The adapter uses CDM SPARQL metadata with exact CELEX and expression-language
constraints, then Cellar CELEX content negotiation with explicit XHTML and
language headers. It verifies metadata identity and rejects ambiguous results.
Text acquisition additionally binds the returned physical Cellar work to the
exact CELEX metadata result and checks the expression language. Real XHTML
is parsed by `eurlex/xhtml.py`, retaining ELI article/recital/annex anchors,
paragraph ranges and nested table rows/cells, including colspan/rowspan. All
ranges refer to the exact `DocumentVersion.content` string. This structure is
stored under its version's normalized metadata only with store permission;
it is never retained under metadata-only permissions. Formex is explicitly
unsupported and rejected; it is not passed to a flat HTML parser.

Cellar emits HTTP canonical resource identifiers in some 303 Location headers.
The transport upgrades only same-host `/resource/cellar/` locations to HTTPS
before the next request. No unencrypted HTTP request is sent.

The interactive EUR-Lex website is not scraped. Responses 202, 401, 403,
WAF headers or recognized challenge HTML stop acquisition. Redirects must
remain on the official Cellar HTTPS host. There is no challenge solving,
cookie/token replay, alternate proxy, verification bypass or fixture fallback.
429/5xx/timeouts are reported as transient without an internal retry loop.
Responses have a decoded-byte limit. TLS verification stays enabled.

If Cellar is unavailable or its approved contract changes, stop and consult
the Publications Office service documentation/support. EUR-Lex registered
web services and approved data deliveries are alternatives requiring their
published access conditions and, where applicable, an account/agreement.
M3 neither claims access to those services nor silently changes providers.

Live evidence from this session (after the egress rules became effective):

- `GET https://publications.europa.eu/resource/celex/32009L0016` with
  `Accept: application/xhtml+xml`, `Accept-Language: eng`: 303 → 200 at
  `https://publications.europa.eu/resource/cellar/061599fd-0b4a-49fe-8ea3-394dbdff7d0b.0006.03/DOC_1`.
  499,493 bytes; SHA-256
  `4abf117c6f7c67ccd54158bb5987fbf9ca9bd066b679a02f64091be8ad68da8c`.
- Exact-CELEX/language SPARQL metadata returned the matching Cellar work,
  official title and `2009-04-23` date. Typed string values require an exact
  datatype or a `STR()` comparison; the initial plain-literal query was empty.
- A separate live query identified `02009L0016-20250105`. Its REST download
  returned 405,772 bytes, SHA-256
  `3afba73bc0aa9c9f0293d5e0e6f6561d004e66a50d6997b5950136262f5c673e`.
- Assertions against these actual files passed: original 40 recitals,
  39 articles, 16 annexes, 476 tables/638 rows; consolidated 43 articles,
  16 annexes, 9 tables. Article/annex/table-cell offsets and the consolidated
  non-authoritative-text disclaimer were checked. These are payload parsing
  checks, not proof of successful full-text persistence.
- A real metadata-only PostgreSQL ingestion created version 1 for the original,
  version 2 for the consolidated act, and returned unchanged on repetition.
  Version 1's source hash and rights evidence remained intact. Both versions
  persist attribution and unresolved reuse conditions in `rights_context`.
  At that review stage their bodies remained empty and the text endpoint correctly returned 403.
- The EUR-Lex-specific legal notice returned HTTP 202 with
  `x-amzn-waf-action: challenge`. That upstream WAF response is now observed,
  distinct from the earlier cloud egress CONNECT 403. No challenge was solved.

The [Publications Office copyright notice](https://op.europa.eu/en/web/about-us/legal-notices/publications-office-of-the-european-union-copyright)
was retrieved successfully. It expressly directs EUR-Lex users to that
service's own copyright notice. Its CC BY statement for website editorial
content is not treated as blanket permission for legislative documents.
Decision 2011/833/EU was also retrieved directly through Cellar and reviewed:
Articles 1–2 limit scope to Commission documents or documents produced on its
behalf; Article 2 excludes third-party IP/restricted material and deceptive
reuse; Article 6 permits source acknowledgement, preservation of meaning and
non-liability conditions. This does not alone establish the selected
Parliament/Council act's reuse grant. Those limitations are recorded with
`state=unknown`, `allowed_actions=[]`, `unresolved_conditions=true`.

The user subsequently supplied an explicit CC BY 4.0 rights review for the
selected EU legislative editions, referencing 2011/833/EU and 2009/496/EC,
requiring EU/CELEX/ELI/manifestation attribution and the exact disclaimer:

> Only European Union legislation published in the Official Journal of the European Union is deemed authentic.

This is recorded as `review_basis=user_approved_policy`, with a SHA-256 of the
review statement, explicit per-version actions and `official_notice_live_verified=false`.
It is not represented as successful retrieval of the challenged notice.
Runtime rights reviews remain explicit; the repository ships no automatic
permission grant for unknown documents or third-party material.

A new isolated PostgreSQL database then exercised **real full-text ingestion**:

| Requested edition | Result | Version | Stored characters | Articles | Annexes | Tables |
| --- | --- | ---: | ---: | ---: | ---: | ---: |
| `32009L0016` | CREATED | 1 | 126,370 | 39 | 16 | 476 |
| `02009L0016-20250105` | UPDATED | 2 | 133,341 | 43 | 16 | 9 |
| Same consolidated edition | UNCHANGED | 2 | 133,341 | 43 | 16 | 9 |

All runs had zero failures. Version 1's full content, source hash, content hash,
provenance and exact rights binding remained unchanged after version 2 and the
repeat. Both versions store the article/annex/table structure in their own
`DocumentVersion.metadata_json`, with ranges pointing to that version's exact
`content`. Their provenance snapshots contain CC BY 4.0, EU/CELEX/ELI/physical
Cellar manifestation attribution, the verbatim authenticity disclaimer, review
basis, and reuse restrictions. Normalization changes are acknowledged.

The live text API returned HTTP 200 for version 2 with its full text, structure,
attribution, CC BY 4.0, disclaimer and no-store response header. Its original
consolidation disclaimer also remains in the text. Pre-approval metadata-only
and permission-denied behavior were validated separately.

Raw files, redirect evidence, real-payload assertions, full-text ingestion
reports, approval-review hashes and isolated database identifiers are retained
in session artifacts outside the checkout. No downloaded legal payload is
bundled as a synthetic fixture or automatically trusted permission record.

## Curated scope and commands

`config/eurlex-maritime.yaml` selects three original English acts:

- `32009L0016`: port State control;
- `32002L0059`: vessel traffic monitoring and information;
- `32015R0757`: maritime emissions monitoring/reporting/verification.

This file defines candidate scope, not verified titles, current legal status,
or permission evidence. Official titles are resolved through Cellar. Legal
status and effective dates remain unknown rather than guessed.

From `backend`, using an explicitly isolated development database:

```sh
python -m app.cli migrate
INTERNATIONAL_CATALOGUE_ENABLED=true python -m app.cli catalogue import-eurlex --enable-provider
```

No body request occurs without a current applicable rights review. Metadata
is available through the existing `/api/instruments` endpoints when the
feature and provider are enabled. The legacy Danish document/search APIs keep
their registered-source exclusion boundary. A new
`GET /api/instruments/{id}/text` requires current display permission and returns
the version, attribution, official link and response hash alongside the text.

## Rights review

Accessibility is not permission. M3 ships no automatically trusted licence
or permission grant. Review the current official notice and each selected
edition's restrictions before enabling text. Preserve a SHA-256 of the reviewed
notice, the evidence URL, reviewer, review date, expiry, attribution, exact
CELEX version, language, resolved conditions and individually permitted actions.
Third-party material must be excluded unless independently authorized.

Pass a reviewed JSON array using `--rights-review /secure/path/review.json`.
Each object has these fields (the schema is `PermissionReview`):

| Field | Meaning |
| --- | --- |
| `external_id` | Original act CELEX, e.g. `32009L0016` |
| `version_celex`, `language` | Exact selected edition and language (`en` or `da`) |
| `state` | `unknown` (no allowed actions), `open_reuse_verified` or `permission_with_conditions` |
| `allowed_actions` | Explicit subset of the existing rights `Action` values |
| `evidence_url`, `evidence_sha256` | Official HTTPS notice URL and reviewed-byte SHA-256 |
| `reviewer`, `reviewed_at`, `expires_at` | Named review, ISO timestamps with timezone, current validity |
| `attribution` | Required source credit accompanying displayed text |
| `unresolved_conditions` | Defaults to true; only false permits action gates to open |
| `legal_basis`, `conditions`, `scope_note` | Reviewed legal basis, obligations/exclusions and explicit scope limitations; retained in the version's rights snapshot |
| `licence`, `licence_url` | `CC-BY-4.0` and its licence URL for the approved policy |
| `authenticity_disclaimer` | Exact Official Journal authenticity disclaimer |
| `review_basis`, `policy_review_sha256` | Distinguish operator review from explicit user policy approval and hash its review statement |
| `official_notice_live_verified` | Defaults false; never claim a WAF-challenged HTML notice was retrieved |
| `supporting_references` | Supporting official legal references |

Fetch and store permissions are both required before body acquisition. Display,
index, embeddings, snippets, export, cache and summarization remain separate
actions; no action is implied by another. The adapter cannot validate the
truth of a review supplied by an operator. Never populate real permission
records from test fixtures. New reviews are immutable evidence records;
revocation/purge uses the existing rights service. Prior-edition grants cannot
authorize another edition or language.

## Validation and acceptance

Run from `backend`:

```sh
python -m pytest
# Only a local disposable PostgreSQL test server with CREATE DATABASE rights:
EURLEX_TEST_POSTGRES_ADMIN_URL=postgresql://postgres@127.0.0.1:55432/postgres python -m pytest tests/test_eurlex_postgresql.py
```

PostgreSQL tests create uniquely named databases, run real Alembic migrations,
verify pgvector, ingest controlled metadata/text, check rights-aware HTTP reads,
and remove only their databases. They also exercise upgrade from merged 0011,
0012 downgrade/re-upgrade and preservation of prior data. Default backend tests
use migrated per-test SQLite DBs and offline hashing embeddings. These do not
claim semantic-model quality or live EUR-Lex service compatibility.

Acceptance requires the regression suite, PostgreSQL checks, existing running-API
integration verifier and frontend build, plus successful official documentation,
service-contract and permissions review and a small live curated import.
The real original/consolidated full-text round trip was verified after the
explicit user policy review described above. The interactive EUR-Lex legal
notice remains WAF-challenged; use the documented Cellar services. M4, merge
and deployment remain out of scope.
