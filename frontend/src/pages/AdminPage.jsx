/**
 * Import- og adminvisning.
 *
 * Viser databasens nøgletal, historik for importkørsler og giver
 * mulighed for at starte en import manuelt.
 *
 * Kilden vælges bevidst i fladen. Vælges fixture, advares der eksplicit
 * om at der importeres syntetiske testdata — systemet må aldrig give
 * indtryk af, at opdigtede dokumenter er hentet fra Retsinformation.
 */

import { useCallback, useEffect, useState } from 'react'
import { api } from '../lib/api.js'
import { formatDateTime } from '../lib/format.js'
import { ErrorBox, Loading } from '../components/Common.jsx'

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

export default function AdminPage() {
  const [stats, setStats] = useState(null)
  const [runs, setRuns] = useState(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)

  const [sourceClient, setSourceClient] = useState('fixture')
  const [fixtureRevision, setFixtureRevision] = useState(1)
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
      setSourceClient((current) => statsData.source_client || current)
    } catch (err) {
      setError(err)
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => { load() }, [load])

  const runImport = async () => {
    setRunning(true)
    setRunError(null)
    setRunResult(null)
    try {
      const result = await api.runImport({
        source_client: sourceClient,
        fixture_revision: fixtureRevision,
      })
      setRunResult(result)
      await load()
    } catch (err) {
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
              <label htmlFor="source">Kilde</label>
              <select
                id="source"
                value={sourceClient}
                onChange={(e) => setSourceClient(e.target.value)}
                disabled={running}
              >
                <option value="fixture">Fixture — syntetiske testdata</option>
                <option value="production">Retsinformation — produktion</option>
              </select>
            </div>

            {sourceClient === 'fixture' && (
              <div className="field">
                <label htmlFor="revision">Fixtursæt</label>
                <select
                  id="revision"
                  value={fixtureRevision}
                  onChange={(e) => setFixtureRevision(Number(e.target.value))}
                  disabled={running}
                >
                  <option value={1}>Revision 1 — grundsæt (18 dokumenter)</option>
                  <option value={2}>Revision 2 — med ændringer (19 dokumenter)</option>
                </select>
              </div>
            )}

            <button className="primary" onClick={runImport} disabled={running}>
              {running ? 'Kører import…' : 'Kør import nu'}
            </button>
          </div>

          {sourceClient === 'fixture' && (
            <p style={{ fontSize: 12.5, color: 'var(--warn-ink)', marginBottom: 0, marginTop: 12 }}>
              Revision 2 ændrer ét dokuments indhold, ophæver ét dokument og tilføjer ét nyt.
              Kør revision 1 først og derefter revision 2 for at se versionering og
              ændringslog i praksis.
            </p>
          )}
          {sourceClient === 'production' && (
            <p style={{ fontSize: 12.5, color: 'var(--ink-muted)', marginBottom: 0, marginTop: 12 }}>
              Henter fra Retsinformations officielle høsteservice. Tjenesten er en
              ændringsfeed med højst 10 dages tilbageblik og tillader ét kald pr. 10
              sekunder, så en kørsel kan tage tid. Åbningstid 03:00–23:45.
            </p>
          )}

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
            </tbody>
          </table>
        </div>
      </div>
    </>
  )
}
