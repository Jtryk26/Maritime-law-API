/**
 * Import- og driftsvisning.
 *
 * Viser databasens nøgletal, historik for importkørsler og giver
 * mulighed for at starte en import manuelt.
 *
 * Hele siden ligger bag `AdminGate`, og hvert enkelt kald herfra kræver
 * et administratortoken på API'et. Går tokenet tabt undervejs — det er
 * skiftet på serveren, eller sessionen er ryddet — falder siden tilbage
 * til login frem for at vise en række uforklarlige fejl.
 *
 * Den normale brugerflade importerer kun fra Retsinformations officielle
 * høsteservice. Syntetiske fixtures er isoleret til automatiske tests.
 */

import { useCallback, useEffect, useState } from 'react'
import { api } from '../lib/api.js'
import { formatDateTime } from '../lib/format.js'
import { ErrorBox, Loading } from '../components/Common.jsx'
import AdminGate from '../components/AdminGate.jsx'

const STATUS_CLASS = {
  COMPLETED: 'ok',
  COMPLETED_WITH_ERRORS: 'warn',
  RUNNING: 'warn',
  FAILED: 'fail',
}

const STATUS_LABEL = {
  COMPLETED: 'Gennemført',
  COMPLETED_WITH_ERRORS: 'Gennemført med fejl',
  RUNNING: 'Kører',
  FAILED: 'Fejlet',
}

function Stat({ value, label }) {
  return (
    <div className="stat">
      <div className="value">{value}</div>
      <div className="label">{label}</div>
    </div>
  )
}

function LatestRun({ run }) {
  if (!run) {
    return (
      <div className="panel">
        <h2>Seneste import</h2>
        <div className="panel-body" style={{ color: 'var(--ink-muted)', fontSize: 14 }}>
          Der er endnu ikke kørt en import. Brug knappen ovenfor for at hente dokumenter
          ind i databasen.
        </div>
      </div>
    )
  }

  const rows = [
    ['Startet', formatDateTime(run.started_at)],
    ['Afsluttet', run.finished_at ? formatDateTime(run.finished_at) : 'Ikke afsluttet'],
    ['Varighed', run.duration_seconds != null ? `${run.duration_seconds.toFixed(1)} sek.` : '—'],
    ['Kilde', run.client_kind === 'fixture' ? 'Fixture (syntetiske testdata)' : 'Retsinformation (produktion)'],
    ['Udløst af', run.trigger],
    ['Dokumenter kontrolleret', run.documents_checked],
    ['Nye dokumenter', run.documents_created],
    ['Opdaterede dokumenter', run.documents_updated],
    ['Uændrede dokumenter', run.documents_unchanged],
    ['Afviste (ikke maritime)', run.documents_rejected],
    ['Fejlede dokumenter', run.documents_failed],
  ]

  return (
    <div className="panel">
      <h2>Seneste import</h2>
      <div className="panel-body">
        <p style={{ marginTop: 0 }}>
          <span className={`run-status ${STATUS_CLASS[run.status] || ''}`}>
            {STATUS_LABEL[run.status] || run.status}
          </span>
        </p>
        <table className="meta-table">
          <tbody>
            {rows.map(([label, value]) => (
              <tr key={label}><th scope="row">{label}</th><td>{value}</td></tr>
            ))}
          </tbody>
        </table>

        {run.error_message && (
          <div className="stale-warning" style={{ marginTop: 12 }}>{run.error_message}</div>
        )}

        {run.errors?.length > 0 && (
          <>
            <h3 style={{ fontSize: 11, textTransform: 'uppercase', letterSpacing: '0.06em',
                         color: 'var(--ink-faint)', margin: '16px 0 6px' }}>
              Fejldetaljer
            </h3>
            <table className="term-table">
              <thead>
                <tr><th>Dokument</th><th>Fejltype</th><th>Besked</th></tr>
              </thead>
              <tbody>
                {run.errors.slice(0, 20).map((error, index) => (
                  <tr key={index}>
                    <td>{error.source_id}</td>
                    <td>{error.error_type}</td>
                    <td style={{ color: 'var(--ink-muted)' }}>{error.error}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </>
        )}
      </div>
    </div>
  )
}

/**
 * Det semantiske indeks.
 *
 * Vektorisering er bevidst adskilt fra importen: en import må ikke kunne
 * fejle, fordi en embedding-model ikke kunne indlæses. Derfor har den sin
 * egen knap og sin egen dækningsgrad, og derfor kan en driftsansvarlig se
 * "15 dokumenter mangler vektorer" som en selvstændig tilstand.
 */
function EmbeddingPanel({ status, onDone }) {
  const [running, setRunning] = useState(false)
  const [error, setError] = useState(null)
  const [result, setResult] = useState(null)

  if (!status) return null

  const run = async () => {
    setRunning(true)
    setError(null)
    setResult(null)
    try {
      setResult(await api.runEmbedding({ limit: 200 }))
      await onDone()
    } catch (err) {
      setError(err)
    } finally {
      setRunning(false)
    }
  }

  if (!status.enabled) {
    return (
      <div className="panel">
        <h2>Betydningssøgning</h2>
        <div className="panel-body" style={{ color: 'var(--ink-muted)', fontSize: 14 }}>
          Vektorlaget er slået fra i konfigurationen (EMBEDDINGS_ENABLED).
          Søgningen kører udelukkende på ord.
        </div>
      </div>
    )
  }

  const rows = [
    ['Udbyder', status.provider || '—'],
    ['Model', status.model || '—'],
    ['Vektorlængde', status.dimensions ?? '—'],
    ['Databaseindeks', status.pgvector ? 'pgvector (HNSW)' : 'portabel sammenligning'],
    ['Maritime dokumenter', status.maritime_documents],
    ['Vektoriseret', `${status.embedded_documents} (${status.coverage_pct} %)`],
    ['Mangler', status.pending_documents],
    ['Stykker i indeks', status.chunks],
  ]

  return (
    <div className="panel">
      <h2>Betydningssøgning</h2>
      <div className="panel-body">
        {status.error && (
          <div className="error-box" style={{ textAlign: 'left', marginTop: 0 }}>
            Embedding-modellen er ikke tilgængelig: {status.error}
          </div>
        )}

        {status.semantic === false && !status.error && (
          <div className="synthetic-warning">
            <strong>Ikke-semantisk udbyder. </strong>
            Vektorerne er lavet med en deterministisk hash og finder ikke
            beslægtede formuleringer. Kun til test og fejlsøgning.
          </div>
        )}

        <table className="meta-table">
          <tbody>
            {rows.map(([label, value]) => (
              <tr key={label}><th scope="row">{label}</th><td>{value}</td></tr>
            ))}
          </tbody>
        </table>

        {status.chunks_from_other_model > 0 && (
          <p className="panel-hint">
            {status.chunks_from_other_model} stykker stammer fra en anden model.
            Byg indekset om med <code>python -m app.cli embed run --reset</code>.
          </p>
        )}

        <div className="import-controls" style={{ marginTop: 14 }}>
          <button
            className="primary"
            onClick={run}
            disabled={running || status.pending_documents === 0 || Boolean(status.error)}
          >
            {running ? 'Vektoriserer…' : 'Vektorisér manglende'}
          </button>
        </div>

        <p className="panel-hint">
          Kører synkront og højst 200 dokumenter ad gangen. Hele indekset bygges
          fra kommandolinjen: <code>python -m app.cli embed run</code>.
        </p>

        {running && <div className="spinner-line" style={{ marginTop: 14 }}><i /></div>}
        {error && (
          <div className="error-box" style={{ marginTop: 14, textAlign: 'left' }}>
            {error.message}
          </div>
        )}
        {result && !error && (
          <p style={{ marginBottom: 0, marginTop: 14, fontSize: 13.5 }}>
            <strong>{result.documents_embedded}</strong> dokumenter vektoriseret,{' '}
            <strong>{result.chunks_written}</strong> stykker skrevet,{' '}
            <strong>{result.pending_after}</strong> mangler stadig.
          </p>
        )}
      </div>
    </div>
  )
}

/**
 * Hvad brugerne søger efter.
 *
 * Listen over søgninger uden resultat er den mest brugbare: den viser
 * enten hvad materialet mangler, eller hvor brugernes ordvalg og
 * lovtekstens går fra hinanden.
 */
function SearchLogPanel({ stats }) {
  const [kind, setKind] = useState('popular')
  const [items, setItems] = useState(null)

  useEffect(() => {
    let cancelled = false
    api.loggedQueries({ kind, limit: 15 })
      .then((data) => { if (!cancelled) setItems(data) })
      .catch(() => { if (!cancelled) setItems([]) })
    return () => { cancelled = true }
  }, [kind])

  return (
    <div className="panel">
      <h2>Søgelog</h2>
      <div className="panel-body">
        <p className="panel-hint" style={{ marginTop: 0 }}>
          {stats?.distinct_queries ?? 0} forskellige søgninger ·{' '}
          {stats?.total_searches ?? 0} søgninger i alt ·{' '}
          {stats?.queries_without_results ?? 0} uden resultat. Loggen indeholder
          hverken bruger, IP-adresse eller session.
        </p>

        <div className="mode-buttons" role="group" aria-label="Visning">
          <button
            type="button"
            className={kind === 'popular' ? 'mode active' : 'mode'}
            onClick={() => setKind('popular')}
          >
            Hyppigste
          </button>
          <button
            type="button"
            className={kind === 'without_results' ? 'mode active' : 'mode'}
            onClick={() => setKind('without_results')}
          >
            Uden resultat
          </button>
        </div>

        {items?.length ? (
          <table className="run-table" style={{ marginTop: 12 }}>
            <thead>
              <tr>
                <th>Søgning</th>
                <th style={{ textAlign: 'right' }}>Antal</th>
                <th style={{ textAlign: 'right' }}>Træf sidst</th>
                <th>Senest</th>
              </tr>
            </thead>
            <tbody>
              {items.map((item) => (
                <tr key={item.id}>
                  <td>{item.query}</td>
                  <td className="num">{item.occurrences}</td>
                  <td className="num">{item.last_result_count}</td>
                  <td>{formatDateTime(item.last_seen_at)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        ) : (
          <p style={{ color: 'var(--ink-muted)', fontSize: 14, marginBottom: 0 }}>
            {kind === 'popular'
              ? 'Der er endnu ikke søgt i systemet.'
              : 'Alle søgninger har givet mindst ét resultat.'}
          </p>
        )}
      </div>
    </div>
  )
}

export default function AdminPage() {
  return <AdminGate>{({ signOut }) => <AdminDashboard signOut={signOut} />}</AdminGate>
}

function AdminDashboard({ signOut }) {
  const [stats, setStats] = useState(null)
  const [runs, setRuns] = useState(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)

  const sourceClient = 'production'
  const [running, setRunning] = useState(false)
  const [runError, setRunError] = useState(null)
  const [runResult, setRunResult] = useState(null)

  const load = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      const [statsData, runsData] = await Promise.all([
        api.stats(),
        api.importRuns({ page_size: 15 }),
      ])
      setStats(statsData)
      setRuns(runsData)
    } catch (err) {
      // Tokenet duer ikke længere — tilbage til login frem for en
      // driftsside fuld af fejlbokse.
      if (err.isAuthError) { signOut(); return }
      setError(err)
    } finally {
      setLoading(false)
    }
  }, [signOut])

  useEffect(() => { load() }, [load])

  const runImport = async () => {
    setRunning(true)
    setRunError(null)
    setRunResult(null)
    try {
      const result = await api.runImport({
        source_client: sourceClient,
      })
      setRunResult(result)
      await load()
    } catch (err) {
      if (err.isAuthError) { signOut(); return }
      setRunError(err)
    } finally {
      setRunning(false)
    }
  }

  if (loading && !stats) return <Loading label="Henter driftsdata…" />
  if (error && !stats) return <ErrorBox error={error} onRetry={load} />

  return (
    <>
      <div className="search-hero">
        <h1>Import og drift</h1>
        <p>
          Status for den lokale database og historik for importkørsler.
        </p>
        <p style={{ marginTop: 8 }}>
          <button type="button" className="mode" onClick={signOut}>
            Lås igen
          </button>
        </p>
      </div>

      <div className="stat-grid">
        <Stat value={stats?.documents_total ?? 0} label="Dokumenter" />
        <Stat value={stats?.documents_maritime ?? 0} label="Maritime" />
        <Stat value={stats?.versions_total ?? 0} label="Versioner" />
        <Stat value={stats?.categories_total ?? 0} label="Kategorier" />
        <Stat value={stats?.average_maritime_score ?? 0} label="Gns. score" />
        <Stat value={stats?.documents_synthetic ?? 0} label="Syntetiske" />
      </div>

      {stats?.documents_synthetic > 0 && (
        <div className="synthetic-warning">
          <strong>Databasen indeholder syntetiske testdata. </strong>
          {stats.documents_synthetic} af {stats.documents_total} dokumenter er
          konstrueret til udvikling og test. De er ikke hentet fra Retsinformation
          og er ikke gældende ret.
        </div>
      )}

      <div className="panel">
        <h2>Kør import</h2>
        <div className="panel-body">
          <div className="import-controls">
            <div className="field">
              <label>Kilde</label>
              <strong>Retsinformation — officielle data</strong>
            </div>

            <button className="primary" onClick={runImport} disabled={running}>
              {running ? 'Kører import…' : 'Kør import nu'}
            </button>
          </div>

          <p style={{ fontSize: 12.5, color: 'var(--ink-muted)', marginBottom: 0, marginTop: 12 }}>
            Henter fra Retsinformations officielle høsteservice. Tjenesten er en
            ændringsfeed med højst 10 dages tilbageblik og tillader ét kald pr. 10
            sekunder, så en kørsel kan tage tid. Åbningstid 03:00–23:45.
          </p>

          {running && <div className="spinner-line" style={{ marginTop: 14 }}><i /></div>}
          {runError && (
            <div className="error-box" style={{ marginTop: 14, textAlign: 'left' }}>
              {runError.message}
            </div>
          )}
          {runResult && !runError && (
            <p style={{ marginBottom: 0, marginTop: 14, fontSize: 13.5 }}>
              Import #{runResult.id} afsluttet:{' '}
              <strong>{runResult.documents_created}</strong> nye,{' '}
              <strong>{runResult.documents_updated}</strong> opdaterede,{' '}
              <strong>{runResult.documents_unchanged}</strong> uændrede,{' '}
              <strong>{runResult.documents_rejected}</strong> afvist,{' '}
              <strong>{runResult.documents_failed}</strong> fejlet.
            </p>
          )}
        </div>
      </div>

      <EmbeddingPanel status={stats?.embeddings} onDone={load} />

      <SearchLogPanel stats={stats?.search_log} />

      <LatestRun run={stats?.last_import} />

      <div className="panel">
        <h2>Importhistorik</h2>
        <div className="panel-body" style={{ padding: 0 }}>
          {runs?.items?.length ? (
            <table className="run-table">
              <thead>
                <tr>
                  <th>#</th><th>Startet</th><th>Kilde</th><th>Status</th>
                  <th style={{ textAlign: 'right' }}>Kontrolleret</th>
                  <th style={{ textAlign: 'right' }}>Nye</th>
                  <th style={{ textAlign: 'right' }}>Opdateret</th>
                  <th style={{ textAlign: 'right' }}>Afvist</th>
                  <th style={{ textAlign: 'right' }}>Fejlet</th>
                </tr>
              </thead>
              <tbody>
                {runs.items.map((run) => (
                  <tr key={run.id}>
                    <td>{run.id}</td>
                    <td>{formatDateTime(run.started_at)}</td>
                    <td>
                      {run.client_kind === 'fixture' ? 'Fixture' : 'Produktion'}
                      {run.used_synthetic_data && (
                        <span className="badge-synthetic" style={{ marginLeft: 6 }}>Testdata</span>
                      )}
                    </td>
                    <td>
                      <span className={`run-status ${STATUS_CLASS[run.status] || ''}`}>
                        {STATUS_LABEL[run.status] || run.status}
                      </span>
                    </td>
                    <td className="num">{run.documents_checked}</td>
                    <td className="num">{run.documents_created}</td>
                    <td className="num">{run.documents_updated}</td>
                    <td className="num">{run.documents_rejected}</td>
                    <td className="num">{run.documents_failed}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          ) : (
            <div style={{ padding: 20, color: 'var(--ink-muted)', fontSize: 14 }}>
              Ingen importkørsler registreret endnu.
            </div>
          )}
        </div>
      </div>

      <div className="panel">
        <h2>Systemoplysninger</h2>
        <div className="panel-body">
          <table className="meta-table">
            <tbody>
              <tr><th scope="row">Konfigureret kilde</th><td>{stats?.source_client}</td></tr>
              <tr><th scope="row">Database</th><td>{stats?.database_backend}</td></tr>
              <tr><th scope="row">Søgemotor</th><td>
                {stats?.search_backend === 'postgresql'
                  ? 'PostgreSQL fuldtekstsøgning'
                  : 'Portabel token-søgning (SQLite)'}
              </td></tr>
              <tr><th scope="row">Betydningssøgning</th><td>
                {!stats?.embeddings?.enabled
                  ? 'Slået fra'
                  : stats.embeddings.error
                    ? 'Model ikke tilgængelig'
                    : `${stats.embeddings.model} · ${stats.embeddings.coverage_pct} % dækning`}
              </td></tr>
            </tbody>
          </table>
        </div>
      </div>
    </>
  )
}
