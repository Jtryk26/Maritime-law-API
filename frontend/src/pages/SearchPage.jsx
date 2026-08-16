/**
 * Søgeside.
 *
 * Layout
 * ======
 * Desktop: klæbende filterpanel i venstre spalte, resultater i
 * hovedspalten. Panelet klæber fra toppen af sit eget spor — ikke fra
 * det øjeblik brugeren har scrollet forbi det — så det følger med fra
 * første scroll i stedet for at "komme med" senere.
 *
 * Mobil: ingen permanent sidebjælke. En filterknap ved siden af
 * resultattællingen åbner en skuffe med de samme filtre og en klæbende
 * bundlinje.
 *
 * Resultatkort
 * ============
 * Kortet viser den korte visningstitel, et par badges og det bedst
 * matchende **paragrafhit** med kapitelhenvisning — ikke en tekststump
 * fra et vilkårligt sted i dokumentet. Er der flere matchende
 * paragraffer, kan de foldes ud uden at forlade listen.
 *
 * Filtertilstanden holdes i URL'ens hash, så en søgning kan deles og
 * genindlæses — vigtigt når en kollega skal se præcis samme resultat.
 */

import { useCallback, useEffect, useMemo, useState } from 'react'
import { api } from '../lib/api.js'
import { navigate } from '../lib/router.js'
import { displayTitle, formatDate, INTENT_LABELS } from '../lib/format.js'
import SearchFilters from '../components/SearchFilters.jsx'
import SearchModeToggle from '../components/SearchModeToggle.jsx'
import MatchExplanation from '../components/MatchExplanation.jsx'
import FilterDrawer from '../components/FilterDrawer.jsx'
import ActiveFilters, { activeFilterCount } from '../components/ActiveFilters.jsx'
import CoreLaws from '../components/CoreLaws.jsx'
import {
  Empty, ErrorBox, LawClassTag, LegalNotice, Loading, ScoreTag, StatusTag, SyntheticBadge,
} from '../components/Common.jsx'

const ARRAY_KEYS = ['category', 'status', 'document_type', 'authority', 'law_class']

const SORT_OPTIONS = [
  { value: 'relevance', label: 'Relevans' },
  { value: 'date_desc', label: 'Nyeste først' },
  { value: 'date_asc', label: 'Ældste først' },
  { value: 'score_desc', label: 'Højeste maritime score' },
  { value: 'title', label: 'Titel (A–Å)' },
]

/** Læser filtertilstand ud af hash-query. */
function filtersFromQuery(query) {
  const filters = { page: Number(query.page) || 1, sort: query.sort || 'relevance' }
  filters.q = query.q || ''
  filters.mode = query.mode || 'hybrid'
  for (const key of ARRAY_KEYS) {
    filters[key] = query[key] ? query[key].split('|').filter(Boolean) : []
  }
  filters.min_score = query.min_score ? Number(query.min_score) : null
  filters.published_from = query.published_from || null
  filters.published_to = query.published_to || null
  return filters
}

function queryFromFilters(filters) {
  const params = new URLSearchParams()
  if (filters.q) params.set('q', filters.q)
  for (const key of ARRAY_KEYS) {
    if (filters[key]?.length) params.set(key, filters[key].join('|'))
  }
  if (filters.min_score) params.set('min_score', String(filters.min_score))
  if (filters.published_from) params.set('published_from', filters.published_from)
  if (filters.published_to) params.set('published_to', filters.published_to)
  if (filters.sort && filters.sort !== 'relevance') params.set('sort', filters.sort)
  if (filters.mode && filters.mode !== 'hybrid') params.set('mode', filters.mode)
  if (filters.page > 1) params.set('page', String(filters.page))
  const qs = params.toString()
  return qs ? `/?${qs}` : '/'
}

/**
 * Paragrafhittet.
 *
 * Det er her forskellen på den gamle og den nye søgning er synlig: i
 * stedet for "…et sted i dokumentet…" står der hvilken paragraf under
 * hvilket kapitel, reglen findes i. Det er den henvisning, brugeren
 * skal bruge videre.
 */
function ParagraphHit({ paragraph, documentId }) {
  if (!paragraph) return null
  return (
    <a className="paragraph-hit" href={`#/dokument/${documentId}`}>
      <span className="paragraph-path">{paragraph.legal_path || paragraph.paragraph_id}</span>
      <span className="paragraph-snippet">{paragraph.snippet}</span>
    </a>
  )
}

function ResultCard({ item, mode }) {
  const [expanded, setExpanded] = useState(false)
  const extra = item.paragraphs || []

  return (
    <article className="result">
      <h2 className="result-title">
        <a href={`#/dokument/${item.id}`} title={item.original_title || item.title}>
          {displayTitle(item)}
        </a>
      </h2>

      <div className="result-badges">
        <StatusTag status={item.status} />
        <LawClassTag value={item.law_class} label={item.law_class_label} />
        <span className="badge-plain">{item.document_type || 'Ukendt type'}</span>
        <span className="badge-plain" title="Maritim relevans (0–100)">
          Maritim <ScoreTag score={item.maritime_score} />
        </span>
        {item.is_synthetic && <SyntheticBadge />}
      </div>

      <div className="result-meta">
        <span>{item.authority || 'Ukendt myndighed'}</span>
        {item.document_number && <><span className="sep">·</span><span>nr. {item.document_number}</span></>}
        <span className="sep">·</span>
        <span>{formatDate(item.published_date)}</span>
        {item.current_version_number > 1 && (
          <><span className="sep">·</span><span>version {item.current_version_number}</span></>
        )}
      </div>

      {mode !== 'lexical' && <MatchExplanation item={item} />}

      {item.paragraph
        ? <ParagraphHit paragraph={item.paragraph} documentId={item.id} />
        : item.snippet && <p className="snippet">{item.snippet}</p>}

      {extra.length > 0 && (
        <>
          {expanded && extra.map((paragraph) => (
            <ParagraphHit
              key={paragraph.paragraph_id}
              paragraph={paragraph}
              documentId={item.id}
            />
          ))}
          <button
            type="button"
            className="linklike more-paragraphs"
            onClick={() => setExpanded((value) => !value)}
          >
            {expanded
              ? 'Skjul øvrige paragraffer'
              : `Vis ${extra.length} ${extra.length === 1 ? 'paragraf' : 'paragraffer'} mere`}
          </button>
        </>
      )}

      {item.categories?.length > 0 && (
        <div className="chips">
          {item.categories.slice(0, 4).map((c) => (
            <span className="chip" key={c.slug} title={`Sikkerhed ${(c.confidence * 100).toFixed(0)} %`}>
              {c.name}
            </span>
          ))}
        </div>
      )}
    </article>
  )
}

/**
 * Hvordan søgningen blev læst.
 *
 * Rangeringen opjusterer kernelove ved brede søgninger og speciallove
 * ved nichesøgninger. Sker det usynligt, ligner en uventet rækkefølge en
 * fejl. Denne linje siger hvad systemet troede, brugeren spurgte om.
 */
function IntentNote({ intent, mode }) {
  if (!intent || mode === undefined) return null
  const label = INTENT_LABELS[intent.kind] || intent.label
  // Læsbare navne, ikke slugs: "Grønland", ikke "groenland".
  const groups = intent.niche_labels?.length ? intent.niche_labels : (intent.niche_groups || [])

  return (
    <p className="intent-note">
      <span className={`intent-tag intent-${intent.kind}`}>{label}</span>
      {groups.length > 0 && (
        <span>
          {' '}Særregler for {groups.join(', ')} er prioriteret op.
        </span>
      )}
      {groups.length === 0 && intent.kind === 'broad' && (
        <span> Brede, centrale regler er prioriteret op.</span>
      )}
      {intent.refinement_reason && <span> {intent.refinement_reason}</span>}
    </p>
  )
}

export default function SearchPage({ query }) {
  const filters = useMemo(() => filtersFromQuery(query), [query])
  const [input, setInput] = useState(filters.q)
  const [results, setResults] = useState(null)
  const [facets, setFacets] = useState(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)
  const [drawerOpen, setDrawerOpen] = useState(false)
  const relatedQueries = results?.related_queries || []

  useEffect(() => { setInput(filters.q) }, [filters.q])

  useEffect(() => {
    api.facets().then(setFacets).catch(() => setFacets(null))
  }, [])

  const load = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      setResults(await api.search({
        q: filters.q || null,
        category: filters.category,
        status: filters.status,
        document_type: filters.document_type,
        authority: filters.authority,
        law_class: filters.law_class,
        min_score: filters.min_score,
        published_from: filters.published_from,
        published_to: filters.published_to,
        sort: filters.sort,
        mode: filters.mode,
        page: filters.page,
        page_size: 10,
      }))
    } catch (err) {
      setError(err)
    } finally {
      setLoading(false)
    }
  }, [JSON.stringify(filters)])

  useEffect(() => { load() }, [load])

  const apply = (next) => navigate(queryFromFilters(next))
  const reset = () => apply({ q: filters.q, mode: filters.mode, page: 1, sort: 'relevance' })

  const submit = (event) => {
    event.preventDefault()
    apply({ ...filters, q: input, page: 1 })
  }

  const activeCount = activeFilterCount(filters)
  const isPristine = !filters.q && activeCount === 0 && filters.page === 1

  const filterPanel = (
    <SearchFilters
      facets={facets}
      filters={filters}
      onChange={apply}
      onReset={reset}
    />
  )

  return (
    <>
      <div className="search-hero">
        <h1>Søg i maritim dansk lovgivning</h1>
        <form className="search-bar" onSubmit={submit} role="search">
          <label className="visually-hidden" htmlFor="q">Søgeord</label>
          <input
            id="q"
            type="search"
            placeholder="Søg i maritim dansk lovgivning…"
            value={input}
            onChange={(e) => setInput(e.target.value)}
            autoComplete="off"
          />
          <button className="primary" type="submit">Søg</button>
        </form>

        {/*
          Nedgraderingsbeskeden hører kun til, når der FAKTISK blev søgt.
          Uden en søgestreng leverer serveren en filtreret liste og kalder
          den "lexical" — helt korrekt, men vist på en tom forside ville
          "betydningssøgning er ikke tilgængelig" være en advarsel om noget,
          der ikke er sket endnu.
        */}
        <SearchModeToggle
          mode={filters.mode}
          actualMode={filters.q ? results?.mode : undefined}
          notice={filters.q ? results?.notice : undefined}
          onChange={(mode) => apply({ ...filters, mode, page: 1 })}
        />

        <ActiveFilters
          filters={filters}
          facets={facets}
          onChange={apply}
          onReset={reset}
        />

        {filters.q && <IntentNote intent={results?.intent} mode={results?.mode} />}

        {relatedQueries.length > 0 && (
          <div className="related-queries">
            <span className="related-label">Andre har også søgt efter</span>
            {relatedQueries.map((related) => (
              <button
                key={related.query}
                type="button"
                className="related-query"
                title={`${Math.round(related.similarity * 100)} % lighed · søgt ${related.occurrences} gang${related.occurrences === 1 ? '' : 'e'}`}
                onClick={() => apply({ ...filters, q: related.query, page: 1 })}
              >
                {related.query}
              </button>
            ))}
          </div>
        )}
      </div>

      <CoreLaws visible={isPristine} />

      <LegalNotice text={results?.legal_notice ||
        'Dokumentdata er hentet fra Retsinformation. Kontrollér altid den gældende officielle tekst på Retsinformation ved juridisk anvendelse.'} />

      <div className="layout">
        <aside className="filters" aria-label="Filtre">{filterPanel}</aside>

        <section aria-label="Søgeresultater">
          <div className="results-header">
            <button
              type="button"
              className="filter-toggle"
              onClick={() => setDrawerOpen(true)}
              aria-haspopup="dialog"
            >
              Filtre{activeCount > 0 && <span className="filter-toggle-count">{activeCount}</span>}
            </button>

            <span className="results-count">
              {loading ? 'Søger…'
                : results
                  ? `${results.truncated ? 'mindst ' : ''}${results.total} ${results.total === 1 ? 'dokument' : 'dokumenter'}`
                  : ''}
            </span>

            <div className="sort">
              <label className="visually-hidden" htmlFor="sort">Sortér</label>
              <select
                id="sort"
                value={filters.sort}
                onChange={(e) => apply({ ...filters, sort: e.target.value, page: 1 })}
              >
                {SORT_OPTIONS.map((o) => (
                  <option key={o.value} value={o.value}>{o.label}</option>
                ))}
              </select>
            </div>
          </div>

          {loading && <Loading label="Søger i databasen…" />}
          {!loading && error && <ErrorBox error={error} onRetry={load} />}

          {!loading && !error && results?.items.length === 0 && (
            <Empty title="Ingen dokumenter matchede søgningen">
              Prøv færre filtre eller et bredere søgeord — for eksempel{' '}
              <em>brand</em>, <em>redningsmidler</em> eller <em>MARPOL</em>.
              {filters.mode === 'lexical' && (
                <>
                  {' '}Prøv eventuelt{' '}
                  <button
                    type="button"
                    className="linklike"
                    onClick={() => apply({ ...filters, mode: 'hybrid', page: 1 })}
                  >
                    kombineret søgning
                  </button>
                  , som også finder beslægtede formuleringer.
                </>
              )}
            </Empty>
          )}

          {!loading && !error && results?.items.map((item) => (
            <ResultCard key={item.id} item={item} mode={results.mode} />
          ))}

          {!loading && results && results.total_pages > 1 && (
            <nav className="pagination" aria-label="Sider">
              <button
                onClick={() => apply({ ...filters, page: filters.page - 1 })}
                disabled={filters.page <= 1}
              >
                ← Forrige
              </button>
              <span>Side {results.page} af {results.total_pages}</span>
              <button
                onClick={() => apply({ ...filters, page: filters.page + 1 })}
                disabled={filters.page >= results.total_pages}
              >
                Næste →
              </button>
            </nav>
          )}
        </section>
      </div>

      <FilterDrawer
        open={drawerOpen}
        onClose={() => setDrawerOpen(false)}
        onReset={reset}
        resultCount={results?.total}
        loading={loading}
      >
        {filterPanel}
      </FilterDrawer>
    </>
  )
}
