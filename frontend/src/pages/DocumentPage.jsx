/**
 * Dokumentside — en læsevisning af lovtekst.
 *
 * Hvad der er vendt om
 * ====================
 * Tidligere mødte brugeren en lang formel titel, en kundgørelsesformel
 * med direktivhenvisninger og et sidepanel fuldt af metadata, før den
 * første regel overhovedet kom til syne. Opgaven på siden er at læse og
 * forstå loven; alt andet er kontekst.
 *
 * Første skærmbillede indeholder nu:
 *
 *   kort visningstitel · status · type · maritim relevans
 *   første kapiteloverskrift
 *   første paragraf
 *
 * Fuld juridisk titel, præambel, metadata, klassifikationsforklaring,
 * ændringslog og versionshistorik er foldet sammen som standard. De er
 * ét klik væk, og de er der stadig — de fylder bare ikke det, brugeren
 * kom for.
 *
 * Lovteksten sættes fra dokumentets **struktur**: kapitler og paragraffer
 * som selvstændige elementer med ankre, ikke som én forudformateret
 * blok. Det gør en indholdsfortegnelse mulig og gør det muligt at linke
 * direkte til en paragraf.
 *
 * Dybde-links
 * ===========
 * Hver paragraf og hvert stykke har et stabilt id ("p-3-12", "p-3-12-stk2"),
 * bygget af samme funktion som linket i søgeresultatet. Ankeret kommer ind
 * som `?p=` i hash-ruten, og siden ruller derhen og fremhæver bestemmelsen.
 */

import { useCallback, useEffect, useMemo, useState } from 'react'
import { api } from '../lib/api.js'
import {
  displayTitle, documentHref, formatDate, formatDateTime, paragraphAnchor,
  paragraphKey, parseParagraphAnchor, splitSubsections,
} from '../lib/format.js'
import RelevanceExplanation from '../components/RelevanceExplanation.jsx'
import {
  Disclosure, ErrorBox, LawClassTag, LegalNotice, Loading, ScoreTag, StatusTag,
  SyntheticBadge, SyntheticWarning,
} from '../components/Common.jsx'

const CHANGE_LABELS = {
  CREATED: 'Oprettet',
  CONTENT_UPDATED: 'Indhold ændret',
  METADATA_UPDATED: 'Metadata ændret',
  STATUS_CHANGED: 'Status ændret',
}

const TARGET_CLASS = 'target-highlight'

/**
 * Finder elementet for et anker. Først det præcise id; ellers paragraffen
 * (og evt. stykket) på nummer alene. Paragrafnumre er entydige i en lov,
 * så et kapitelnummer, der er læst anderledes, må ikke koste brugeren
 * springet til den rigtige bestemmelse.
 */
function findAnchorTarget(anchor) {
  if (!anchor) return null
  const exact = window.document.getElementById(anchor)
  if (exact) return exact
  const parsed = parseParagraphAnchor(anchor)
  if (!parsed) return null
  const paragraph = window.document.querySelector(
    `.legal-text [data-paragraph="${CSS.escape(parsed.paragraph)}"]`,
  )
  if (!paragraph || !parsed.stk) return paragraph
  return paragraph.querySelector(`[data-stk="${parsed.stk}"]`) || paragraph
}

/**
 * Ruller til ankeret og fremhæver bestemmelsen.
 *
 * Klassen sættes direkte på DOM-elementet frem for via React-tilstand:
 * klikker brugeren på samme paragraf igen, skal animationen starte forfra,
 * og det kræver at klassen fjernes og sættes på ny i samme omgang.
 */
function useScrollToAnchor(anchor, ready) {
  useEffect(() => {
    if (!anchor || !ready) return undefined
    let element = null
    const frame = window.requestAnimationFrame(() => {
      element = findAnchorTarget(anchor)
      if (!element) return
      const reduceMotion = window.matchMedia?.('(prefers-reduced-motion: reduce)').matches
      element.classList.remove(TARGET_CLASS)
      void element.offsetWidth // genstarter animationen
      element.classList.add(TARGET_CLASS)
      element.scrollIntoView({ behavior: reduceMotion ? 'auto' : 'smooth', block: 'center' })
      // Skærmlæsere skal også lande på bestemmelsen, ikke kun øjet.
      element.focus({ preventScroll: true })
    })
    return () => {
      window.cancelAnimationFrame(frame)
      element?.classList.remove(TARGET_CLASS)
    }
  }, [anchor, ready])
}

/**
 * Beslægtet regulering, fundet på vektorlighed.
 *
 * Kompakt liste med korte visningstitler: to linjer med ellipsis, hele
 * elementet klikbart. Tidligere stod den fulde juridiske titel her, og
 * fire af dem fyldte en halv skærm uden at kunne skimmes.
 *
 * Er indekset ikke bygget, vises panelet slet ikke — et tomt panel ville
 * ligne "der findes ingen beslægtede regler", hvilket ville være en
 * påstand, vi ikke har dækning for.
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
    <section className="related-docs" aria-labelledby="related-heading">
      <h2 id="related-heading">Beslægtede regler</h2>
      <p className="panel-hint">Fundet på indholdets betydning — ikke på fælles ord i titlen.</p>
      <div className="related-grid">
        {items.map((item) => (
          <a className="related-card" key={item.id} href={`#/dokument/${item.id}`}>
            <span className="related-title" title={item.original_title || item.title}>
              {displayTitle(item)}
            </span>
            <span className="related-meta">
              <span>{item.document_type || 'Dokument'}</span>
              {item.published_date && <span>· {item.published_date.slice(0, 4)}</span>}
              <span>· {Math.round(item.similarity * 100)} % lighed</span>
            </span>
          </a>
        ))}
      </div>
    </section>
  )
}

function MetaTable({ document }) {
  const rows = [
    ['Fuld juridisk titel', document.original_title || document.title],
    ['Retsinformation-ID', document.retsinformation_id || '—'],
    ['Dokumentnummer', document.document_number || '—'],
    ['Type', document.document_type || '—'],
    ['Myndighed', document.authority || '—'],
    ['Publiceret', formatDate(document.published_date)],
    ['Ikrafttræden', formatDate(document.effective_date)],
    ['Status', <StatusTag status={document.status} key="s" />],
    ['Rolle', document.law_class_label || '—'],
    ['Anvendelsesbredde', document.scope_score?.toFixed(2) ?? '—'],
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
    <>
      {document.versions.map((version) => {
        const isActive = activeVersion === version.version_number
        return (
          <div className={`version ${version.is_current ? 'is-current' : ''}`} key={version.id}>
            <span className="num">v{version.version_number}</span>
            <span>{formatDate(version.created_at)}</span>
            <span className="spacer" />
            <span className="hash" title={`SHA-256: ${version.content_hash}`}>
              {version.content_hash.slice(0, 10)}
            </span>
            <button
              className="version-button"
              disabled={isActive}
              onClick={() => onSelect(version.is_current ? null : version.version_number)}
            >
              {isActive ? 'Vises' : 'Vis'}
            </button>
          </div>
        )
      })}
      {document.versions.length === 1 && (
        <p className="panel-hint">
          Dokumentet har kun én version. Ændres teksten hos kilden, oprettes en ny
          version, og denne bevares uændret.
        </p>
      )}
    </>
  )
}

function ChangeLog({ entries }) {
  if (!entries?.length) return null
  return entries.map((entry) => (
    <div className="changelog-entry" key={entry.id}>
      <div className="type">{CHANGE_LABELS[entry.change_type] || entry.change_type}</div>
      <div className="detail">{entry.detail}</div>
      <div className="when">{formatDateTime(entry.created_at)}</div>
    </div>
  ))
}

/** Indholdsfortegnelse. Vises kun når der er noget at navigere i. */
function TableOfContents({ structure, documentId }) {
  const chapters = structure?.chapters || []
  if (chapters.length < 2) return null

  return (
    <Disclosure summary="Indhold" count={structure.paragraph_count}>
      <ol className="toc">
        {chapters.map((chapter) => (
          <li key={chapter.number}>
            <strong>Kapitel {chapter.number}{chapter.title ? ` — ${chapter.title}` : ''}</strong>
            <span className="toc-paragraphs">
              {chapter.paragraphs.map((paragraph) => (
                <a
                  key={paragraph.paragraph_id}
                  href={documentHref(documentId, paragraphAnchor(chapter.number, paragraph.paragraph_id))}
                >
                  {paragraph.paragraph_id}
                </a>
              ))}
            </span>
          </li>
        ))}
      </ol>
    </Disclosure>
  )
}

/**
 * Selve lovteksten, sat fra strukturen.
 *
 * Kapitler bliver overskrifter, paragraffer bliver afsnit med et anker.
 * Kan teksten ikke parses — bilag, tabeller, ældre kundgørelser — vises
 * den uændret. Hellere rå lovtekst end en visning, der lader som om der
 * er en struktur, som ikke findes.
 */
function Paragraph({ paragraph, chapterNo }) {
  const anchor = paragraphAnchor(chapterNo, paragraph.paragraph_id)
  const subsections = splitSubsections(paragraph.text)

  return (
    <div
      className="paragraph"
      id={anchor}
      data-paragraph={paragraphKey(paragraph.paragraph_id)}
      tabIndex={-1}
    >
      {subsections.map((subsection) => (
        <p
          className="subsection"
          key={subsection.number}
          id={paragraphAnchor(chapterNo, paragraph.paragraph_id, subsection.number)}
          data-stk={subsection.number}
          tabIndex={-1}
        >
          {subsection.text}
        </p>
      ))}
    </div>
  )
}

function LegalText({ structure, fallback }) {
  if (!structure?.has_paragraphs) {
    return <div className="legal-text">{fallback || 'Ingen tekst gemt for denne version.'}</div>
  }

  const chapters = structure.chapters || []
  const loose = structure.loose_paragraphs || []

  return (
    <div className="legal-text">
      {loose.map((paragraph) => (
        <Paragraph paragraph={paragraph} chapterNo={null} key={paragraph.paragraph_id} />
      ))}
      {chapters.map((chapter) => (
        <section className="chapter" key={chapter.number}>
          <h3 className="chapter-heading">
            Kapitel {chapter.number}
            {chapter.title && <span className="chapter-title">{chapter.title}</span>}
          </h3>
          {chapter.paragraphs.map((paragraph) => (
            <Paragraph paragraph={paragraph} chapterNo={chapter.number} key={paragraph.paragraph_id} />
          ))}
        </section>
      ))}
    </div>
  )
}

export default function DocumentPage({ documentId, query }) {
  const [document, setDocument] = useState(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)
  const [activeVersion, setActiveVersion] = useState(null)
  const [versionContent, setVersionContent] = useState(null)
  const [fullText, setFullText] = useState(false)

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
    if (activeVersion === null) { setVersionContent(null); return undefined }
    let cancelled = false
    api.documentVersion(documentId, activeVersion)
      .then((v) => { if (!cancelled) setVersionContent(v) })
      .catch(() => { if (!cancelled) setVersionContent(null) })
    return () => { cancelled = true }
  }, [documentId, activeVersion])

  const structure = document?.structure
  const showingHistoric = activeVersion !== null && versionContent

  // Den rå tekst bruges to steder: som "vis alt som ét dokument" og for
  // historiske versioner. En gammel version har sin egen ordlyd og må
  // ikke sættes ind i den nuværende versions struktur.
  const rawText = useMemo(
    () => (showingHistoric ? versionContent.content : document?.content) || '',
    [showingHistoric, versionContent, document],
  )

  // Ankre findes kun i den strukturerede visning af den aktuelle version.
  const structuredView = !showingHistoric && !fullText && Boolean(structure?.has_paragraphs)
  useScrollToAnchor(query?.p, !loading && structuredView)

  if (loading) return <Loading label="Henter dokument…" />
  if (error) return <ErrorBox error={error} onRetry={load} />
  if (!document) return null

  return (
    <article className="document">
      <p className="back-link"><a href="#/">← Tilbage til søgning</a></p>

      <SyntheticWarning text={document.synthetic_notice} />

      <header className="doc-header">
        <div className="result-badges">
          <StatusTag status={document.status} />
          <LawClassTag value={document.law_class} label={document.law_class_label} />
          <span className="badge-plain">{document.document_type}</span>
          <span className="badge-plain" title="Maritim relevans (0–100)">
            Maritim <ScoreTag score={document.maritime_score} />
          </span>
          {document.is_synthetic && <SyntheticBadge />}
        </div>

        <h1>{displayTitle(document)}</h1>

        <div className="doc-subline">
          <span>{document.authority || 'Ukendt myndighed'}</span>
          {document.document_number && <span>· nr. {document.document_number}</span>}
          <span>· {formatDate(document.published_date)}</span>
        </div>
      </header>

      <div className="doc-body">
        {showingHistoric && (
          <div className="stale-warning">
            Du ser en historisk version. Den er bevaret uændret som den blev hentet.{' '}
            <button className="version-button" onClick={() => setActiveVersion(null)}>
              Vis aktuel version
            </button>
          </div>
        )}

        {!showingHistoric && <TableOfContents structure={structure} documentId={document.id} />}

        {showingHistoric || fullText || !structure?.has_paragraphs ? (
          <div className="legal-text">{rawText || 'Ingen tekst gemt for denne version.'}</div>
        ) : (
          <LegalText structure={structure} fallback={rawText} />
        )}

        {!showingHistoric && structure?.has_paragraphs && (
          <button
            type="button"
            className="linklike full-text-toggle"
            onClick={() => setFullText((value) => !value)}
          >
            {fullText ? 'Vis som kapitler og paragraffer' : 'Vis den fulde tekst som ét dokument'}
          </button>
        )}

        {structure?.preamble && (
          <Disclosure summary="Vis fuld titel og præambel">
            <p className="full-title">{document.original_title || document.title}</p>
            <div className="preamble">{structure.preamble}</div>
          </Disclosure>
        )}
        {!structure?.preamble && (
          <Disclosure summary="Vis fuld juridisk titel">
            <p className="full-title">{document.original_title || document.title}</p>
          </Disclosure>
        )}

        <Disclosure summary="Vis metadata">
          <MetaTable document={document} />
          {document.source_url && (
            <p className="source-link">
              <a href={document.source_url} target="_blank" rel="noreferrer">
                Åbn original på Retsinformation ↗
              </a>
            </p>
          )}
        </Disclosure>

        {document.categories?.length > 0 && (
          <Disclosure summary="Vis maritime kategorier" count={document.categories.length}>
            {document.categories.map((category) => (
              <div className="category-row" key={category.slug}>
                <strong>{category.name}</strong>
                <span className="category-confidence">
                  {(category.confidence * 100).toFixed(0)} %
                </span>
                {category.matched_terms?.length > 0 && (
                  <div className="category-terms">
                    {category.matched_terms.slice(0, 6).join(', ')}
                  </div>
                )}
              </div>
            ))}
          </Disclosure>
        )}

        <Disclosure summary="Vis maritim relevansvurdering">
          <RelevanceExplanation relevance={document.relevance} bare />
        </Disclosure>

        <Disclosure summary="Vis historik" count={document.versions?.length}>
          <VersionHistory
            document={document}
            activeVersion={activeVersion}
            onSelect={setActiveVersion}
          />
          <ChangeLog entries={document.change_log} />
        </Disclosure>
      </div>

      <SimilarDocuments documentId={document.id} />

      <LegalNotice text={document.legal_notice} />
    </article>
  )
}
