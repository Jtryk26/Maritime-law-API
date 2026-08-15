/**
 * Søgeside.
 *
 * Filtertilstanden holdes i URL'ens hash, så en søgning kan deles og
 * genindlæses — vigtigt når en kollega skal se præcis samme resultat.
 */

import { useCallback, useEffect, useMemo, useState } from 'react'
import { api } from '../lib/api.js'
import { navigate } from '../lib/router.js'
import { formatDate } from '../lib/format.js'
import SearchFilters from '../components/SearchFilters.jsx'
import SearchModeToggle from '../components/SearchModeToggle.jsx'
import MatchExplanation from '../components/MatchExplanation.jsx'
import {
  Empty, ErrorBox, LegalNotice, Loading, ScoreTag, StatusTag, SyntheticBadge,
} from '../components/Common.jsx'

const ARRAY_KEYS = ['category', 'status', 'document_type', 'authority']

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
  // Tom betyder "lad serveren vælge" — den kender konfigurationen og
  // ved om der overhovedet findes vektorer at søge i.
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

function ResultCard({ item, mode }) {
  return (
    <article className="result">
      <h2 className="result-title">
        <a href={`#/dokument/${item.id}`}>{item.title}</a>
      </h2>

      <div className="result-meta">
        <StatusTag status={item.status} />
        <span className="sep">·</span>
        <span>{item.document_type || 'Ukendt type'}</span>
        {item.document_number && <><span className="sep">·</span><span>nr. {item.document_number}</span></>}
        <span className="sep">·</span>
        <span>{item.authority || 'Ukendt myndighed'}</span>
        <span className="sep">·</span>
        <span>{formatDate(item.published_date)}</span>
        <span className="sep">·</span>
        <span>
          Maritim relevans <ScoreTag score={item.maritime_score} />
        </span>
        {item.current_version_number > 1 && (
          <>
            <span className="sep">·</span>
            <span>version {item.current_version_number}</span>
          </>
        )}
        {item.is_synthetic && <SyntheticBadge />}
      </div>

      {mode !== 'lexical' && <MatchExplanation item={item} />}

      {item.snippet && <p className="snippet">{item.snippet}</p>}

      {item.categories?.length > 0 && (
        <div className="chips">
          {item.categories.map((c) => (
            <span className="chip" key={c.slug} title={`Sikkerhed ${(c.confidence * 100).toFixed(0)} %`}>
              {c.name}
            </span>
          ))}
        </div>
      )}
    </article>
  )
}

export default function SearchPage({ query }) {
  const filters = useMemo(() => filtersFromQuery(query), [query])
  const [input, setInput] = useState(filters.q)
  const [results, setResults] = useState(null)
  const [facets, setFacets] = useState(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)
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

  const submit = (event) => {
    event.preventDefault()
    apply({ ...filters, q: input, page: 1 })
  }

  const activeFilterCount =
    ARRAY_KEYS.reduce((n, key) => n + (filters[key]?.length || 0), 0) +
    (filters.min_score ? 1 : 0) +
    (filters.published_from ? 1 : 0) +
    (filters.published_to ? 1 : 0)

  return (
    <>
      <div className="search-hero">
        <h1>Søg i maritim dansk lovgivning</h1>
        <p>
          Søg i titel, lovtekst, dokumentnummer, myndighed og kategorier — ordret,
          på betydning eller begge dele. Kun dokumenter med maritim relevans er
          indekseret.
        </p>
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

        <SearchModeToggle
          mode={filters.mode}
          actualMode={results?.mode}
          notice={results?.notice}
          onChange={(mode) => apply({ ...filters, mode, page: 1 })}
        />

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

      <LegalNotice text={results?.legal_notice ||
        'Dokumentdata er hentet fra Retsinformation. Kontrollér altid den gældende officielle tekst på Retsinformation ved juridisk anvendelse.'} />

      <div className="layout">
        <SearchFilters
          facets={facets}
          filters={filters}
          onChange={apply}
          onReset={() => apply({ q: filters.q, page: 1, sort: 'relevance' })}
        />

        <section aria-label="Søgeresultater">
          <div className="results-header">
            <span className="results-count">
              {loading ? 'Søger…'
                : results
                  ? `${results.truncated ? 'mindst ' : ''}${results.total} ${results.total === 1 ? 'dokument' : 'dokumenter'}`
                  : ''}
              {activeFilterCount > 0 && ` · ${activeFilterCount} filter${activeFilterCount === 1 ? '' : 'e'} aktive`}
            </span>
            <div className="sort">
              <label htmlFor="sort" style={{ fontSize: 13, color: 'var(--ink-muted)' }}>Sortér</label>
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
              {' '}Er databasen tom, skal der køres en import under{' '}
              <a href="#/import">Import og drift</a>.
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
    </>
  )
}
