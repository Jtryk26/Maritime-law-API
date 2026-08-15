/**
 * Dokumentside.
 *
 * Viser metadata, gældende lovtekst, kategorier, klassifikationsforklaring
 * og versionshistorik. Historiske versioner kan åbnes — teksten som den
 * så ud dengang bevares uændret.
 */

import { useCallback, useEffect, useState } from 'react'
import { api } from '../lib/api.js'
import { formatDate, formatDateTime } from '../lib/format.js'
import RelevanceExplanation from '../components/RelevanceExplanation.jsx'
import {
  ErrorBox, LegalNotice, Loading, ScoreTag, StatusTag, SyntheticBadge, SyntheticWarning,
} from '../components/Common.jsx'

const CHANGE_LABELS = {
  CREATED: 'Oprettet',
  CONTENT_UPDATED: 'Indhold ændret',
  METADATA_UPDATED: 'Metadata ændret',
  STATUS_CHANGED: 'Status ændret',
}

/**
 * Beslægtet regulering, fundet på vektorlighed.
 *
 * Værdien ligger i, at den ikke bygger på titler eller kategorier: to
 * bekendtgørelser kan regulere det samme uden at dele et eneste ord i
 * overskriften. Er indekset ikke bygget, vises panelet slet ikke — et
 * tomt panel ville ligne "der findes ingen beslægtede regler", hvilket
 * ville være en påstand vi ikke har dækning for.
 */
function SimilarDocuments({ documentId }) {
  const [items, setItems] = useState(null)

  useEffect(() => {
    let cancelled = false
    api.similar(documentId, 6)
      .then((data) => { if (!cancelled) setItems(data) })
      .catch(() => { if (!cancelled) setItems([]) })
    return () => { cancelled = true }
  }, [documentId])

  if (!items || items.length === 0) return null

  return (
    <div className="panel">
      <h2>Lignende dokumenter</h2>
      <div className="panel-body">
        <p className="panel-hint">
          Fundet på indholdets betydning — ikke på fælles ord i titlen.
        </p>
        {items.map((item) => (
          <div key={item.id} className="similar-item">
            <a href={`#/dokument/${item.id}`}>{item.title}</a>
            <div className="similar-meta">
              <span title="Lighed med dette dokument">
                {Math.round(item.similarity * 100)} % lighed
              </span>
              {item.matched_heading && <span>· {item.matched_heading}</span>}
              {item.status && <span>· {item.status}</span>}
            </div>
          </div>
        ))}
      </div>
    </div>
  )
}

function MetaTable({ document }) {
  const rows = [
    ['Retsinformation-ID', document.retsinformation_id || '—'],
    ['Dokumentnummer', document.document_number || '—'],
    ['Type', document.document_type || '—'],
    ['Myndighed', document.authority || '—'],
    ['Publiceret', formatDate(document.published_date)],
    ['Ikrafttræden', formatDate(document.effective_date)],
    ['Status', <StatusTag status={document.status} key="s" />],
    ['Aktuel version', document.current_version_number ?? '—'],
    ['Senest hentet', formatDateTime(document.last_retrieved_at)],
    ['Kilde', document.source],
  ]
  return (
    <table className="meta-table">
      <tbody>
        {rows.map(([label, value]) => (
          <tr key={label}><th scope="row">{label}</th><td>{value}</td></tr>
        ))}
      </tbody>
    </table>
  )
}

function VersionHistory({ document, activeVersion, onSelect }) {
  return (
    <div className="panel">
      <h2>Versionshistorik</h2>
      <div className="panel-body">
        {document.versions.map((version) => {
          const isActive = activeVersion === version.version_number
          return (
            <div
              className={`version ${version.is_current ? 'is-current' : ''}`}
              key={version.id}
            >
              <span className="num">v{version.version_number}</span>
              <span>{formatDate(version.created_at)}</span>
              <span className="spacer" />
              <span className="hash" title={`SHA-256: ${version.content_hash}`}>
                {version.content_hash.slice(0, 10)}
              </span>
              <button
                style={{ padding: '2px 8px', fontSize: 12 }}
                disabled={isActive}
                onClick={() => onSelect(version.is_current ? null : version.version_number)}
              >
                {isActive ? 'Vises' : 'Vis'}
              </button>
            </div>
          )
        })}
        {document.versions.length === 1 && (
          <p style={{ fontSize: 12.5, color: 'var(--ink-muted)', margin: '8px 0 0' }}>
            Dokumentet har kun én version. Ændres teksten hos kilden, oprettes en ny
            version, og denne bevares uændret.
          </p>
        )}
      </div>
    </div>
  )
}

function ChangeLog({ entries }) {
  if (!entries?.length) return null
  return (
    <div className="panel">
      <h2>Ændringslog</h2>
      <div className="panel-body">
        {entries.map((entry) => (
          <div className="changelog-entry" key={entry.id}>
            <div className="type">{CHANGE_LABELS[entry.change_type] || entry.change_type}</div>
            <div style={{ color: 'var(--ink-muted)' }}>{entry.detail}</div>
            <div style={{ color: 'var(--ink-faint)', fontSize: 11.5 }}>
              {formatDateTime(entry.created_at)}
            </div>
          </div>
        ))}
      </div>
    </div>
  )
}

export default function DocumentPage({ documentId }) {
  const [document, setDocument] = useState(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)
  const [activeVersion, setActiveVersion] = useState(null)
  const [versionContent, setVersionContent] = useState(null)

  const load = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      setDocument(await api.document(documentId))
    } catch (err) {
      setError(err)
    } finally {
      setLoading(false)
    }
  }, [documentId])

  useEffect(() => { load() }, [load])

  useEffect(() => {
    if (activeVersion === null) { setVersionContent(null); return }
    let cancelled = false
    api.documentVersion(documentId, activeVersion)
      .then((v) => { if (!cancelled) setVersionContent(v) })
      .catch(() => { if (!cancelled) setVersionContent(null) })
    return () => { cancelled = true }
  }, [documentId, activeVersion])

  if (loading) return <Loading label="Henter dokument…" />
  if (error) return <ErrorBox error={error} onRetry={load} />
  if (!document) return null

  const showingHistoric = activeVersion !== null && versionContent
  const text = showingHistoric ? versionContent.content : document.content

  return (
    <>
      <p className="back-link"><a href="#/">← Tilbage til søgning</a></p>

      <SyntheticWarning text={document.synthetic_notice} />

      <header className="doc-header">
        <div className="result-meta" style={{ marginBottom: 0 }}>
          <StatusTag status={document.status} />
          <span className="sep">·</span>
          <span>{document.document_type}</span>
          {document.document_number && (
            <><span className="sep">·</span><span>nr. {document.document_number}</span></>
          )}
          <span className="sep">·</span>
          <span>Maritim relevans <ScoreTag score={document.maritime_score} /></span>
          {document.is_synthetic && <SyntheticBadge />}
        </div>
        <h1>{document.title}</h1>
        {document.short_title && document.short_title !== document.title && (
          <p style={{ color: 'var(--ink-muted)', margin: 0 }}>{document.short_title}</p>
        )}
      </header>

      <LegalNotice text={document.legal_notice} />

      <div className="doc-grid">
        <div>
          <div className="panel">
            <h2>
              {showingHistoric
                ? `Lovtekst — version ${activeVersion} (historisk)`
                : 'Gældende lovtekst i denne database'}
            </h2>
            <div className="panel-body">
              {showingHistoric && (
                <div className="stale-warning">
                  Du ser en historisk version. Den er bevaret uændret som den blev hentet.{' '}
                  <button
                    style={{ padding: '1px 8px', fontSize: 12, marginLeft: 4 }}
                    onClick={() => setActiveVersion(null)}
                  >
                    Vis aktuel version
                  </button>
                </div>
              )}
              <div className="legal-text">{text || 'Ingen tekst gemt for denne version.'}</div>
            </div>
          </div>

          <ChangeLog entries={document.change_log} />
        </div>

        <aside>
          <div className="panel">
            <h2>Dokumentoplysninger</h2>
            <div className="panel-body">
              <MetaTable document={document} />
              {document.source_url && (
                <p style={{ marginBottom: 0, marginTop: 14 }}>
                  <a href={document.source_url} target="_blank" rel="noreferrer">
                    Åbn original på Retsinformation ↗
                  </a>
                </p>
              )}
            </div>
          </div>

          {document.categories?.length > 0 && (
            <div className="panel">
              <h2>Maritime kategorier</h2>
              <div className="panel-body">
                {document.categories.map((category) => (
                  <div key={category.slug} style={{ marginBottom: 10 }}>
                    <div style={{ display: 'flex', justifyContent: 'space-between', gap: 8 }}>
                      <strong style={{ fontSize: 13.5 }}>{category.name}</strong>
                      <span style={{ fontFamily: 'var(--mono)', fontSize: 12,
                                     color: 'var(--ink-muted)' }}>
                        {(category.confidence * 100).toFixed(0)} %
                      </span>
                    </div>
                    {category.matched_terms?.length > 0 && (
                      <div style={{ fontSize: 12, color: 'var(--ink-faint)' }}>
                        {category.matched_terms.slice(0, 6).join(', ')}
                      </div>
                    )}
                  </div>
                ))}
              </div>
            </div>
          )}

          <RelevanceExplanation relevance={document.relevance} />

          <SimilarDocuments documentId={document.id} />

          <VersionHistory
            document={document}
            activeVersion={activeVersion}
            onSelect={setActiveVersion}
          />
        </aside>
      </div>
    </>
  )
}
