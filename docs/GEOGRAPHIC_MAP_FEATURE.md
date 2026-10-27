# Geographic applicability and map

This feature links documents to geographic areas (seas, straits, territorial
waters, MARPOL Special Areas, ECAs, PSSAs and routeing measures) and shows
them on a MapLibre map at `#/kort`.

> **Not legally authoritative.** See [What is not authoritative](#what-is-not-legally-authoritative).

## Experimental — off by default

| Switch | Default | Effect when off |
|---|---|---|
| `GEOGRAPHY_ENABLED` (backend) | `false` | No geographic API routes at all (404, not in OpenAPI), no geographic analysis during imports. Search and documents are unaffected. This is the enforcement point. |
| `VITE_GEOGRAPHY_ENABLED` (frontend build) | not set | No "Kort" navigation item, no `#/kort` page, no map link on document pages. Display only: the backend never relies on it. |

Docker Compose doesn't pass `GEOGRAPHY_ENABLED`, so containers start with
the feature off. To enable it, add the variable to the backend service and
build the frontend with `VITE_GEOGRAPHY_ENABLED=true`. The CLI
(`python -m app.cli geography …`) works either way.

**Seeding is never done at startup.** The production runtime role is
SELECT-only (`database/provision_roles.sql`), so startup must not write.
Areas are loaded explicitly with the owner role:
`python -m app.cli geography seed`. With empty tables the API simply returns
empty lists. Analysis never creates or changes an area either: if an area
from the YAML is missing from the database, the analysis stops before
writing anything and says to run `geography seed`. During an import that
is logged (`import.geography.areas_missing`) and the import continues; the
CLI exits with code 1 and the admin endpoint answers 409.

**Source rights.** Every analysis path (import, CLI, admin) applies the
central rights policy (`RightsService.version_permits`). Danish documents
from an unregistered source are analysed as before. A registered or
rights-managed source needs explicit permission for `fetch_text`,
`store_text`, `index_text` and `snippets`; otherwise the result is
`RIGHTS_DENIED` and nothing is stored (admin: 403).

## Three separate concepts

| Concept | Where it lives | Meaning |
|---|---|---|
| **Mention** | `document_geographic_applicability.applicability = 'MENTIONS'` | The text names a place. Nothing more. |
| **Applicability** | same table, `APPLIES` / `EXEMPTS` / `DEFINES` / `HISTORICAL` | A rule-based *assessment* of how the text relates to the place, with `confidence`, `cue`, `detection_method` and `review_status`. |
| **Geometry** | `geographic_areas.geometry` + `geometry_kind` | How the area is drawn on the map. Usually an `indicative_point`, not a boundary. |

A mention never becomes applicability unless the surrounding sentence holds
a cue (see below), and nothing becomes authoritative without human review.

## How detection works

Code: `backend/app/services/geography/`.

1. **Ingestion hook.** `ImportService._sync_geography` runs after a document
   is created or changed (and for the English column of bilingual treaties).
   It runs in a savepoint, so a failure never costs the import.
   It only runs with `GEOGRAPHY_ENABLED=true`.
   `GeographyService.analyse` skips a document whose `input_hash`
   (SHA-256 of title, content, legal status and the gazetteer file) and
   `DETECTOR_VERSION` match the last completed run.
2. **Text extraction.** The title and `normalize_legal_text(content)`, the
   same text the structure parser and the document page use. Offsets
   (`char_start`/`char_end`) refer to that normalized text (or to the title
   when `source_field = 'title'`). Each content match gets the document
   page's `paragraph_anchor` (`p-3-12-stk2`) when it falls inside a paragraph.
3. **Entity recognition** (`gazetteer.py`). A curated gazetteer
   (`config/geographic_areas.yaml`) is used, never an LLM. Each alias becomes
   a regex that ignores case, matches both `ø` and `oe`, allows any whitespace
   between words and Danish inflections (`Østersø` → `Østersøen`, `Nordsøens`),
   and needs word boundaries. When matches overlap, the longest wins, so
   "Baltic Sea area" (MARPOL) beats "Baltic Sea". A narrow fuzzy pass catches
   misspelled capitalised single-word names (`Skagerack` → Skagerrak) at
   0.85 quality.
4. **Applicability classification** (`classify.py`). The classifier looks at
   the sentence around each match:
   - **EXEMPTS**: `gælder ikke`, `finder ikke anvendelse`, `undtagen`, `bortset fra`,
     `excluding`, `except`, `shall not apply`, `outside`…
   - **DEFINES**: after the name: `forstås`, `means`, `is bounded by`, `omfatter`…;
     before it: `designated as`, `definition`…
   - **APPLIES**: `gælder for`, `finder anvendelse`, `applies to`, `within`,
     `inden for`, `sejlads i`, `operating in`…; with less weight, an obligation
     (`skal`, `shall`, `must`) plus a locative (`i`, `in the`) right before
     the name. A title like "… i Storebælt" also counts.
   - **HISTORICAL**: `ophævet`, `repealed`, `no longer`… When the document
     status itself is historic (`Historisk`), every match becomes HISTORICAL.
   - **MENTIONS**: everything else (confidence 0.4).

   For cues before the name, the **nearest** one wins. "applies within the
   Baltic Sea, excluding the Kattegat" gives APPLIES for the Baltic Sea and
   EXEMPTS for the Kattegat.

   **Negations are decided first and never become APPLIES**, whatever the
   confidence. A negated application before the name ("gælder dog ikke",
   "gælder heller ikke", "finder dog ikke anvendelse", "ikke omfattet",
   "undtaget herfra", "does not apply") governs the rest of the sentence,
   including a subordinate clause: "Kravene gælder ikke, når skibet sejler i
   Nordsøen" is EXEMPTS. Only a contrast ("men", "but") ends it. A negation
   right after the name in the same clause ("Skibe i Kattegat er ikke
   omfattet") is EXEMPTS; one in a coordinated clause is about something else
   ("applies to ships in the North Sea and does not apply to warships" stays
   APPLIES). Ambiguous sentences become MENTIONS: a new application after the
   negation without a contrast, or a loose "ikke"/"not" before the name
   ("gælder for skibe, der ikke sejler i Nordsøen", "skal ikke"). A
   prohibition ("må ikke", "shall not discharge") is not a negation.
   Areas marked `mention_only` (states such as Denmark, which is usually the
   flag state, not a sea area) are capped at MENTIONS.
   `confidence = cue strength × alias weight × match quality`.
   The LLM fallback is **not** implemented. `detection_method` leaves room for
   `llm`, but such rows would need the same confidence and review handling,
   and no LLM may create geometry.
5. **Area resolution.** Each match resolves to a `geographic_areas` row by
   slug. Place-like names the gazetteer does not know ("Gulf of Finland",
   "Isefjorden") go to `geographic_review_queue` with reason `unknown_area`.
   **No area or polygon is ever created automatically.**
6. **Storage.** A re-analysis replaces only automatic rows that nobody has
   ruled on (`unreviewed`, `needs_rereview`). Human decisions are never
   transferred (see [Stale reviews](#stale-reviews)). Each document
   keeps at most 5 matches per area per classification (the strongest), so
   "Danmark" ×200 does not flood the table. Each row stores the verbatim
   source excerpt (`context`, exposed as `source_excerpt`), the document
   version, the paragraph anchor, the detection method and the review status.

## Detection is not publication

The default public responses contain **only reviewed applicability**. Automatic
detections are only available from separate endpoints:

| | Reviewed (default) | Automatic detections (separate) |
|---|---|---|
| Area | `GET /geographic-areas/{slug}/documents` | `GET /geographic-areas/{slug}/detections` |
| Document | `GET /documents/{id}/geography` | `GET /documents/{id}/geography/detections` |
| `result_kind` | `reviewed_applicability` | `automatic_detection` (on the page and on every item) |
| Notice | "Reviewed geographic applicability only …" | "Automatic detections — not reviewed …" |

Counts are split the same way (`reviewed_document_count` and
`detected_document_count`; `reviewed_counts_by_applicability` and
`detected_counts_by_applicability`). The map shows reviewed results and
offers automatic detections only behind an explicit toggle in a visually
separate section.

Every match also carries a `publication_status` and a verbatim
`publication_label` (`services/geography/service.py: publication_status`):

| `publication_status` | Label | When |
|---|---|---|
| `reviewed` | **Reviewed geographic applicability** | `confirmed`/`corrected`, current, same document version, **and** `context_hash` matches the document's current title and status and the area's current definition |
| `reviewed_mention` | Reviewed geographic mention | Same, for MENTIONS |
| `detected_unreviewed` | **Detected geographic applicability — not reviewed** | Every automatic classification, whatever its confidence |
| `detected_mention_unreviewed` | Detected geographic mention — not reviewed | Automatic MENTIONS |
| `needs_rereview` | Detected geographic applicability — not reviewed (an earlier review no longer matches the current text) | An earlier review exists but something changed |
| `superseded` | Historical review of an earlier text — not applied to the current text | Only in `review_history` |

Confidence and the rule-based classification never count as approval.

## Stale reviews

**No human decision is ever transferred automatically.** Each row stores a
`context_hash`: SHA-256 over the document version (which covers the whole
legal text, since versions are immutable), the title, the legal status and
the area's legal definition (type, related area, legal basis, dates,
designation). Each row also has an `evidence_hash` over the matched text
and verbatim excerpt, used only as information.

On every analysis (`_reconcile_reviews`), for each current decision
(confirmed, corrected, rejected or manual):

- **Nothing changed** (same context, same match; only a forced re-run): the
  decision stays.
- **Anything changed** (text anywhere, title, status, version, or the area's
  definition): the old row becomes `superseded`, with its review fields kept
  untouched as history. New rows in that area are created as
  `needs_rereview`, with `supersedes_id` pointing to the old decision. This
  also applies when the excerpt is word-for-word the same, because the
  context may have changed elsewhere.
- **Previously rejected** links become `needs_rereview`: they are neither
  published nor still treated as rejected.
- **Manual links:** a `needs_rereview` copy is created and the original
  becomes `superseded`.
- **Read-time guard:** a row only counts as reviewed when its `context_hash`
  matches the current state. A change that hasn't been re-analysed yet (for
  example because the feature was off during an import) is therefore shown
  as `needs_rereview`, never as reviewed.

The earlier decision appears **as context only** on automatic detections:
`previous_review` holds the reviewer, the time, the old version and
`excerpt_unchanged`, with the note "Context only — this earlier decision is
not applied to the current text." Superseded reviews are listed under
`review_history` on `/documents/{id}/geography`. They can't be reviewed
again; review the current row instead.

A review is also refused when a current row's evidence is stale: its
version is no longer the document's current version, or the title, status
or area definition has changed (`context_hash`), for example because the
document was imported while the feature was off. The CLI says to re-analyse
(`geography analyse --document-id N --force`) and review the new row. The
row and any earlier review are left unchanged.

## Distinct geographies

A shared name does not make two things the same legal area. Each is its own
`geographic_areas` row with its own slug, type, legal basis, dates and
(future) geometry:

| Concept | Example slugs | Type |
|---|---|---|
| Named sea | `baltic-sea`, `north-sea` | `sea_area` |
| MARPOL Special Area, annex not determined | `marpol-baltic-sea-area` | `marpol_special_area` |
| MARPOL Special Area under a specific annex | `marpol-annex-i-baltic-sea-area`, `…-annex-iv-…`, `…-annex-v-…` | `marpol_special_area` |
| SOx/PM ECA | `eca-baltic-sea-sox`, `eca-north-sea-sox`, `eca-mediterranean-sea` | `eca` (`designation.pollutant = SOx_PM`) |
| NOx ECA | `eca-baltic-sea-nox`, `eca-north-sea-nox` | `eca` (`pollutant = NOx`) |
| ECA, pollutant not determined | `eca-baltic-sea`, `eca-north-sea` | `eca` (`pollutant = null`) |
| PSSA | `pssa-baltic-sea`, `pssa-wadden-sea` | `pssa` |
| National maritime jurisdiction | `danish-territorial-waters` | `territorial_waters` |
| Descriptive national waters / state | `danish-waters`, `denmark` | `sea_area`, `state` (mention only) |

Rules:
- A text match links **only** to the area whose alias matched. "Østersøen"
  links to `baltic-sea` and never to its ECAs, Special Areas or PSSA.
- An ambiguous designation ("Baltic Sea ECA", "Baltic Sea area") links to
  the *not determined* entry, never to a guessed pollutant or annex. The
  annex-specific Special Areas have no aliases on purpose: they can only be
  linked manually (`geography add`) by someone who has read the text.
- `parent_slug` (exposed as `related_area_slug`/`related_areas`) is a
  navigation hint only. `GET …/{slug}/documents` excludes related areas by
  default. `include_related=true` adds them, and each result still names
  its own `area_slug`.
- The North American and US Caribbean ECAs are not split by pollutant yet.
  Their names say so.
- Several distinct areas can share an approximate marker. The map lists them
  separately in a chooser.

## Unverified metadata

Areas with `legal_status = curated_unverified` (or `descriptive`) never
publish `legal_basis`, `effective_from` or `effective_to` as facts. The
API sets those fields to `null` and puts the entered values under
`provisional` with `status_note`: "Provisional, unverified metadata — not
checked against the official legal text." The map shows them only inside a
collapsed "provisional" notice. Setting `legal_status = verified`, after
checking against the official text, publishes them. No values were invented
to replace them.

## Geometry labels

`geometry_label` (API and map) says what is drawn:
`indicative_point` → **"Approximate area marker"**; `approximate` →
"Approximate outline — not a legal boundary"; `official` → "Boundary copied
from an official source — the official source prevails"; `none` → "No map
geometry". Markers have a fixed size so they don't read as an extent.

`official` requires, in `config/geographic_areas.yaml`, `legal_status:
verified` and `metadata.boundary_evidence` with `source_url` (HTTPS),
`reviewer`, `reviewed_at` (not in the future), `licence` and
`reuse_permitted: true`, and the geometry's `--source` must be that
`source_url`. `load-geometry --kind official` refuses otherwise. If the
evidence later disappears (for example `legal_status` is withdrawn in the
YAML), the API and the map show the geometry as `approximate`.
`display_extent` is `null` for markers. The `bbox` query parameter only
selects what to draw in a viewport. No endpoint answers whether a coordinate
or route lies inside an area, and one must not be built on markers or
bounding boxes.

## Data model

Migrations: `backend/migrations/versions/0013_geographic_applicability.py` and
`0014_geographic_review_evidence.py` (evidence hash, superseded reviews, `needs_rereview`).
Models: `backend/app/models/geography.py`. No existing table is changed.

- **`geographic_areas`**: `slug` (stable, unique), `name`, `name_da`, `area_type`,
  `parent_slug`, `legal_status` (`descriptive` | `curated_unverified` | `verified`),
  `legal_basis`, `source_url`, `source_reference`, `effective_from/to`,
  `aliases`, `geometry` (GeoJSON, WGS 84), `geometry_kind`
  (`none` | `indicative_point` | `approximate` | `official`), `geometry_source`,
  bbox columns `min_lon/min_lat/max_lon/max_lat` (indexed; display only, never a containment test), and `metadata_json` (incl. `designation`).
- **`document_geographic_applicability`**: `document_id`, `document_version_id`, `area_id`,
  `detection_run_id`, `source_field`, `match_text`, `char_start/end`,
  `paragraph_anchor`, `context`, `applicability`, `confidence`, `cue`,
  `detection_method`, `detector_version`, `review_status`
  (`unreviewed` | `confirmed` | `rejected` | `corrected` | `needs_rereview`), `reviewed_by/at`, `review_note`,
  `evidence_hash`, `evidence_status` (`current` | `superseded`), `supersedes_id` (migration 0014),
  timestamps. Indexed on `document_id`, `(area_id, applicability)` and `review_status`.
- **`geographic_detection_runs`**: one row per analysis, with `input_hash`,
  `detector_version`, `trigger` (`import` / `admin` / `cli`), `status` and counts.
- **`geographic_review_queue`**: unknown place names per document. One row per
  `(document_id, candidate_key)`. A `dismissed` or `resolved` entry is never
  reopened.

**PostGIS.** The database image (pgvector/pg16) has no PostGIS, so geometry
is portable JSON plus a bbox, and that works the same on SQLite. To add
PostGIS later: create the extension, add a `geometry(Geometry, 4326)` column
filled with `ST_GeomFromGeoJSON(geometry::text)` and a GIST index. The API
contract does not change. Historical boundaries (`geographic_area_versions`)
are not implemented yet; `effective_from/to` covers the area's own validity.

## API

Public and read-only (only when `GEOGRAPHY_ENABLED=true`). Rejected matches
and rights-managed documents are left out.

| Endpoint | Purpose |
|---|---|
| `GET /api/geographic-areas?area_type=&bbox=minLon,minLat,maxLon,maxLat&page=` | Paginated areas with `reviewed_document_count` and `detected_document_count`. `bbox` is a viewport filter, never a containment test. |
| `GET /api/geographic-areas/map?area_type=&bbox=` | GeoJSON FeatureCollection for MapLibre; areas without geometry listed in `unmapped` |
| `GET /api/geographic-areas/{slug}` | Area detail: `designation`, `geometry_label`, verified legal basis or `provisional` block, `reference_url`, geometry, `related_areas`, reviewed and detected counts per classification |
| `GET /api/geographic-areas/{slug}/documents?applicability=&include_related=` | **Reviewed applicability only.** Title, status, effective date, classification, `publication_label`, `match_text`, `source_excerpt`, `document_version_id`, `detection_method`, vessel relevance, source URL, `document_path` with paragraph anchor |
| `GET /api/geographic-areas/{slug}/detections?applicability=&include_related=&min_confidence=` | **Automatic detections — not reviewed.** Same item shape plus `previous_review` |
| `GET /api/documents/{id}/geography` | Reviewed areas, `review_history`, `detected_area_count` and `detections_path` |
| `GET /api/documents/{id}/geography/detections` | Automatic detections for a document — not reviewed |

Default for `applicability`: everything except MENTIONS. `include_related`
defaults to `false`.

Admin (Bearer token, like `/api/admin/*`, only when enabled):

| Endpoint | Purpose |
|---|---|
| `POST /api/admin/geography/reanalyse/{document_id}?force=true` | Re-runs detection for one document |

## Map

`frontend/src/pages/MapPage.jsx`, route `#/kort` (`#/kort?dokument=ID`
preselects a document; the document page links there). MapLibre GL 4.7.1 is
lazy-loaded, so it does not weigh down the other pages.

- Layers can be toggled per area type, and each type has its own colour.
- Clicking an area shows its **reviewed** documents. Automatic detections
  sit in a separate, warning-styled section behind a toggle. When several
  areas share a point (Baltic Sea / ECA / PSSA), a chooser opens.
- Clicking "Vis dokumentets områder" highlights all areas for that document.
- The page is labelled as educational and non-navigational.
- The basemap is blank (graticule only) by default, so nothing is fetched
  from other domains. Set `VITE_MAP_STYLE_URL` to use a real style, and add
  its domains to `connect-src`/`img-src` in `frontend/security-headers.conf`.
- The global CSP is unchanged. The map uses MapLibre's CSP build
  (`maplibre-gl-csp.js`), whose web worker is a same-origin file
  (`setWorkerUrl`) instead of a `blob:` URL.

## How to add a new area

1. Add an entry to `config/geographic_areas.yaml` with `slug`, `name`,
   `name_da`, `area_type`, `aliases` (Danish and English), `legal_status`,
   and optionally `legal_basis`, `source_url`, `effective_from`, `parent`
   and `indicative_point: [lon, lat]`. Never change an existing slug.
2. Load it with the owner role: `python -m app.cli geography seed`. Areas
   are **not** seeded at startup (the runtime role is read-only).
3. Optional: load a real boundary from a named source:
   `python -m app.cli geography load-geometry <slug> boundary.geojson --kind official --source "<URL>"`.
   `official` needs the verified boundary evidence described under
   "Geometry labels"; otherwise use `--kind approximate`.
   Seeding never overwrites `approximate` or `official` geometry.
4. Run `python -m app.cli geography analyse`. A changed gazetteer file is
   part of the analysis hash, so documents are re-analysed. When the
   classification rules change, also bump `DETECTOR_VERSION` in
   `services/geography/service.py`. A changed area definition invalidates
   existing approvals for that area (see [Stale reviews](#stale-reviews)).
5. Close the matching entries in the review queue:
   `python -m app.cli geography queue --resolve <id> --note "added as <slug>"`.

## How to mark a document as reviewed

Review happens through the CLI only, like the other rule review flows in this
repo. There is deliberately no public HTTP write path.

```bash
# Find match ids (automatic detections, including needs_rereview)
curl /api/documents/42/geography/detections

# Confirm, reject, or correct the classification (status becomes 'corrected')
python -m app.cli geography review 1234 --status confirmed --reviewer "initials"
python -m app.cli geography review 1235 --status rejected  --reviewer "initials" --note "flag state, not sea area"
python -m app.cli geography review 1236 --status confirmed --applicability EXEMPTS --reviewer "initials"

# Add a link the text does not state (e.g. it follows from the legal basis)
python -m app.cli geography add 42 eca-baltic-sea --applicability APPLIES --reviewer "initials"

# Review queue
python -m app.cli geography queue --verbose
python -m app.cli geography queue --dismiss 17 --note "not a place"
```

Any change to the document or the area's definition invalidates a review:
the old review is kept as history and the current row is `needs_rereview`.
Review that row with the same command (see [Stale reviews](#stale-reviews)).
Only a forced re-run with nothing changed keeps a review. Rejected rows are
hidden from the public API.

## Running tests locally

```bash
cd backend
pip install -r requirements.txt
python -m pytest tests/test_geography.py tests/test_geography_safeguards.py   # this feature
python -m pytest                                    # full suite (SQLite, migrations run per test)
```

`tests/test_migrations_postgresql.py` also compiles migrations 0013–0014 as
PostgreSQL SQL offline and checks identifier lengths. Everything runs on
fixture documents: no network, no Retsinformation import, no embeddings.

Frontend: `cd frontend && npm install && npm run build` (map off), or
`VITE_GEOGRAPHY_ENABLED=true npm run build` (map on).

## Proposed next step: a single boundary pilot

**Proposal only. Nothing has been downloaded, no basemap added, no CSP changed.**

**Area and designation.** `eca-baltic-sea-sox`: the Baltic Sea Emission
Control Area for SOx and particulate matter under MARPOL Annex VI,
regulation 14. One designation only. The NOx ECA (`eca-baltic-sea-nox`,
regulation 13), the MARPOL Annex I/IV/V Special Areas, the PSSA and the
named sea are out of scope even where the text describes the same waters.

**Why this one.** It is the regulatory area most often relevant to the
Danish sulphur rules already in the collection. Its extent is defined in
words with a single explicit boundary line, which makes independent
verification practical. It also exercises the sea-vs-designation
separation. It was **not** picked because a polygon is available: as far as
we know, no official polygon is published for it, and that is part of what
the pilot must establish.

**Authoritative legal reference.** To be confirmed from the current
consolidated MARPOL text before any work starts:
- Annex VI regulation 14 designates the Baltic Sea area as an ECA for SOx
  and particulate matter (expected paragraph 14.3.1).
- That reference is to the "Baltic Sea area" as defined in Annex I
  regulation 1.11.2: the Baltic Sea proper with the Gulf of Bothnia and the
  Gulf of Finland, and the entrance to the Baltic bounded by the parallel of
  the Skaw in the Skagerrak.
- The exact latitude of that parallel must be **transcribed from the
  authoritative text**, not from memory or a secondary source.

The EU and Danish implementing acts (Directive (EU) 2016/802 and the Danish
sulphur order in the collection) are recorded as derived references, not as
the definition of the extent.

**Geometry source and licence.** The legal definition has one coordinate
line plus coastlines. A polygon is therefore a *construction*: coastline
dataset + the Skaw parallel + the gulf closing lines implied by the text.
Requirements:
- The coastline dataset's licence must allow redistribution in this
  service, and be recorded (for example, public-domain vs. ODbL share-alike
  have different obligations).
- An official map service (e.g. HELCOM) may be used only after its terms
  are checked and recorded.
- Provenance is recorded in `geometry_source`: dataset, version or
  retrieval date, licence and file checksum.
- A constructed polygon is stored as `geometry_kind = approximate`
  ("Approximate outline — not a legal boundary"). `official` is reserved
  for coordinates published by the authority itself.
- Before publication, a named reviewer compares the construction against
  the transcribed legal text and signs off. Only then is
  `legal_status = verified` set.

**Staying distinct from similarly named areas.** The geometry is loaded
for `eca-baltic-sea-sox` only, with `load-geometry`, which touches one row.
It is never copied to `baltic-sea`, `eca-baltic-sea`, `eca-baltic-sea-nox`,
`marpol-*-baltic-sea-area` or `pssa-baltic-sea`, even if their textual
extent appears identical. Each needs its own legal reference, its own
verification and its own load. Documents still link to the SOx ECA only
through a matching alias or a manual link, never because they mention the
Baltic Sea.

**Tests needed before publication.**
1. *Provenance gate:* a polygon is served by `/geographic-areas/map` only
   when `geometry_source` (with licence) is set and `legal_status = verified`.
   Otherwise it falls back to the approximate marker.
2. *Geometry validity:* closed rings, WGS 84 ranges, no self-intersection.
   This needs a decision on a small geometry dependency (none is used today).
3. *Legal-definition fixtures:* sample points from the transcribed text.
   For example, a point in the Kattegat south of the Skaw parallel lies
   inside, a point in the Skagerrak north of it lies outside, and the
   Gulfs of Bothnia and Finland lie inside. These are verification tests of
   the construction only. They must not be exposed as an API answer.
4. *Isolation:* loading the pilot geometry leaves every other Baltic area's
   geometry, metadata and links unchanged, and `geography seed` does not
   overwrite it.
5. *Labelling:* API and map show the `approximate` label and the
   non-navigation disclaimer, and there is still no containment or route
   endpoint (`test_ingen_rute_afgoer_om_en_position_ligger_i_et_omraade`).
6. *Review boundary:* a change to the legal-reference text or the geometry
   file (checksum) sets the area back to `curated_unverified` until it is
   re-verified.

## What is not legally authoritative

- **Every automatic classification.** `APPLIES`/`EXEMPTS`/`DEFINES` are
  pattern-based readings of one sentence. They miss context such as
  definitions elsewhere in the text, legal basis, annexes, cross-references,
  vessel-type conditions and dates. `review_status = unreviewed` means no
  human has looked at it.
- **Area metadata marked `curated_unverified`.** The legal basis and
  effective dates in the YAML were entered from general knowledge and must
  be checked against the official IMO / MARPOL / HELCOM / Danish text.
- **Map geometry.** Seeded areas are `indicative_point` markers only. They
  are not boundaries, and the map must not be used for navigation or for
  deciding whether a position is inside a regulated area. Only geometry with
  `geometry_kind = official` comes from a named official source, and even
  that is a copy. The official source remains authoritative.
- **Confirmed reviews** are editorial judgements for this educational
  service, not legal advice. Retsinformation, IMO and the competent
  authorities remain the official sources.
