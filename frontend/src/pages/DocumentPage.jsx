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
 * Hver paragraf, hvert stykke og hvert nummereret punkt har et stabilt id
 * ("p-3-12", "p-3-12-stk2", "p-1-nr10"), bygget af samme funktion som
 * linket i søgeresultatet. Ankeret kommer ind som `?p=` i hash-ruten, og
 * siden ruller derhen og fremhæver bestemmelsen.
 *
 * Relationer
 * ==========
 * "Dokumenthistorik" viser lovens forgængere og efterfølgere som en
 * tidslinje. Bekendtgørelser udstedt med hjemmel i en paragraf vises i en
 * neutral boks UNDER paragraffen — adskilt fra lovteksten, så der ikke er
 * tvivl om, hvad der er vedtaget tekst, og hvad der er administrative regler.
 *
 * Traktater på flere sprog
 * ========================
 * Bekendtgørelser af internationale overenskomster (BKI) har ofte den
 * autentiske engelske tekst ved siden af den danske. Importen gemmer den
 * som et selvstændigt dokument (language = "en"), bundet til det danske
 * med relationen translation_of. Har dokumentet `translations`, viser
 * værktøjslinjen en sprogvælger, der skifter til det andet dokument —
 * så link, søgning og historik følger det dokument, man læser.
 *
 * Nummererede lister ("Følgende ophæves: 1) … 2) …") sættes som en
 * egentlig punktliste. Peger ankeret på en paragraf med en liste, vælges
 * det punkt, der matcher søgeordene (`?q=`); ellers fremhæves kun
 * indledningen — ikke hele listen, som kan fylde flere skærme.
 */

import { useCallback, useEffect, useMemo, useState } from 'react'
import { api } from '../lib/api.js'
import { navigate } from '../lib/router.js'
import {
  displayTitle, documentHref, formatDate, formatDateTime, paragraphAnchor,
  documentTypeLabel, highlightSegments, paragraphKey, typeCode, parseParagraphAnchor, splitListItems, splitSubsections,
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
  if (!paragraph) return null
  // Et punkt uden stykke ("p-1-nr10") ligger i paragraffens første stykke.
  const stk = parsed.stk || (parsed.nr ? 1 : null)
  const scope = (stk && paragraph.querySelector(`[data-stk="${stk}"]`)) || paragraph
  if (!parsed.nr) return scope
  return scope.querySelector(`[data-nr="${parsed.nr}"]`) || scope
}

/** Hvor mange af søgeordene der står i et element. */
function termHits(element, query) {
  return highlightSegments(element.textContent, query).filter((s) => s.match).length
}

/**
 * Snævrer målet ind, når det indeholder en nummereret liste: til det punkt
 * med flest søgeord, eller til indledningen, hvis intet punkt matcher.
 * Peger ankeret allerede på et punkt, står det uændret.
 */
function refineListTarget(element, query) {
  if (!element || element.matches('.legal-list-item')) return element
  const items = [...element.querySelectorAll('.legal-list-item')]
  if (!items.length) return element
  let best = null
  let bestHits = 0
  for (const item of items) {
    const hits = query ? termHits(item, query) : 0
    if (hits > bestHits) { best = item; bestHits = hits }
  }
  return best || element.querySelector('.legal-list-lead') || element
}

/**
 * Ruller til ankeret og fremhæver bestemmelsen.
 *
 * Klassen sættes direkte på DOM-elementet frem for via React-tilstand:
 * klikker brugeren på samme paragraf igen, skal animationen starte forfra,
 * og det kræver at klassen fjernes og sættes på ny i samme omgang.
 */
function useScrollToAnchor(anchor, query, ready) {
  useEffect(() => {
    if (!anchor || !ready) return undefined
    let element = null
    const frame = window.requestAnimationFrame(() => {
      element = refineListTarget(findAnchorTarget(anchor), query)
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
  }, [anchor, query, ready])
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
/**
 * Ét stykke. Indeholder det en nummereret opregning, sættes den som en
 * liste med hængende indrykning, og hvert punkt får sit eget anker.
 */
function Subsection({ subsection, chapterNo, paragraphId }) {
  const anchor = paragraphAnchor(chapterNo, paragraphId, subsection.number)
  const { lead, items } = splitListItems(subsection.text)

  if (!items.length) {
    return (
      <p className="subsection" id={anchor} data-stk={subsection.number} tabIndex={-1}>
        {subsection.text}
      </p>
    )
  }

  return (
    <div className="subsection" id={anchor} data-stk={subsection.number} tabIndex={-1}>
      <p className="legal-list-lead" tabIndex={-1}>{lead}</p>
      <ol className="legal-list">
        {items.map((item) => (
          <li
            className="legal-list-item"
            key={item.nr}
            id={paragraphAnchor(chapterNo, paragraphId, subsection.number, item.nr)}
            data-nr={item.nr}
            tabIndex={-1}
          >
            <span className="legal-list-nr">{item.nr})</span>
            <span className="legal-list-text">{item.text}</span>
          </li>
        ))}
      </ol>
    </div>
  )
}

function Paragraph({ paragraph, chapterNo, delegations }) {
  const anchor = paragraphAnchor(chapterNo, paragraph.paragraph_id)
  const subsections = splitSubsections(paragraph.text)

  return (
    <>
      <div
        className="paragraph"
        id={anchor}
        data-paragraph={paragraphKey(paragraph.paragraph_id)}
        tabIndex={-1}
      >
        {subsections.map((subsection) => (
          <Subsection
            key={subsection.number}
            subsection={subsection}
            chapterNo={chapterNo}
            paragraphId={paragraph.paragraph_id}
          />
        ))}
      </div>
      <DelegationBox items={delegations?.[paragraphKey(paragraph.paragraph_id)]} />
    </>
  )
}

/** "§ 24 b, stk. 1" -> "24b": paragrafnøglen, som paragrafferne bærer. */
function sectionParagraphKey(section) {
  const match = /^§\s*(\d+(?:\s*[a-zæøå](?![a-zæøå]))?)/i.exec(section || '')
  return match ? paragraphKey(match[1]) : null
}

/** Tilknyttede bekendtgørelser grupperet pr. paragraf. */
function delegationsByParagraph(regulations) {
  const grouped = {}
  for (const regulation of regulations || []) {
    const key = sectionParagraphKey(regulation.section)
    if (!key) continue
    if (!grouped[key]) grouped[key] = []
    grouped[key].push(regulation)
  }
  return grouped
}

function relatedTitle(item) {
  return item.title && item.title !== item.label ? `${item.label} — ${item.title}` : item.label
}

/** Op til så mange forskrifter vises åbent under paragraffen; flere foldes sammen. */
const DELEGATIONS_OPEN_LIMIT = 2

function DelegationItems({ items }) {
  return (
    <ul className="delegation-list">
      {items.map((item) => (
        <li key={`${item.id}-${item.section}`}>
          {item.id
            ? <a href={documentHref(item.id)}>{relatedTitle(item)}</a>
            : <span>{relatedTitle(item)}</span>}
          <span className="delegation-note">Udmønter bemyndigelse i {item.section}</span>
        </li>
      ))}
    </ul>
  )
}

/**
 * Bekendtgørelser udstedt med hjemmel i netop denne paragraf.
 *
 * Bevidst neutral og uden for paragraffens egen ramme: det er
 * administrative regler, ikke Folketingets vedtagne tekst, og boksen må
 * ikke kunne læses som en del af lovteksten.
 *
 * En central bemyndigelsesparagraf kan hjemle mange bekendtgørelser. Står
 * de åbent, fylder de mere end paragraffen selv — derfor foldes listen
 * sammen, når der er flere end `DELEGATIONS_OPEN_LIMIT`. <details> frem
 * for egen tilstand: den virker uden JavaScript og kan søges i af
 * browserens sidesøgning.
 */
function DelegationBox({ items }) {
  if (!items?.length) return null
  if (items.length <= DELEGATIONS_OPEN_LIMIT) {
    return (
      <aside className="delegation-box is-compact" aria-label="Tilknyttede forskrifter">
        <DelegationItems items={items} />
      </aside>
    )
  }
  return (
    <aside className="delegation-box is-collapsible" aria-label="Tilknyttede forskrifter">
      <details>
        <summary>
          <span aria-hidden="true">↳ </span>
          {items.length} tilknyttede forskrifter udmønter denne paragraf
        </summary>
        <DelegationItems items={items} />
      </details>
    </aside>
  )
}

/**
 * Dokumenthistorik: forgængere, dette dokument og efterfølgere, ældste først.
 * Forgængere, der ikke er i databasen, vises uden link.
 *
 * Kompakt som standard: kun den nærmeste forgænger og efterfølger står
 * fremme, så en ældre lov med årtiers revisioner ikke skubber § 1 ud af
 * første skærmbillede. Resten foldes ud på klik.
 */
function DocumentHistory({ document }) {
  const [expanded, setExpanded] = useState(false)
  const predecessors = document.history?.predecessors || []
  const successors = document.history?.successors || []
  if (!predecessors.length && !successors.length) return null

  const current = {
    id: document.id,
    label: [typeCode(document.document_type), document.document_number && `nr. ${document.document_number}`,
      document.published_date && `af ${document.published_date.slice(0, 4)}`].filter(Boolean).join(' '),
    year: document.published_date ? Number(document.published_date.slice(0, 4)) : null,
    status: document.status,
    current: true,
  }
  const entries = [...predecessors, current, ...successors]

  const currentIndex = predecessors.length
  const first = Math.max(0, currentIndex - 1)
  const last = Math.min(entries.length, currentIndex + 2)
  const hiddenBefore = first
  const hiddenAfter = entries.length - last
  const hidden = hiddenBefore + hiddenAfter
  const shown = expanded ? entries : entries.slice(first, last)

  const years = entries.map((entry) => entry.year).filter(Boolean)
  const span = years.length > 1 ? `${Math.min(...years)}–${Math.max(...years)}` : null
  const counts = [
    predecessors.length > 0
      && `${predecessors.length} tidligere ${predecessors.length === 1 ? 'version' : 'versioner'}`,
    successors.length > 0
      && `${successors.length} ${successors.length === 1 ? 'nyere version' : 'nyere versioner'}`,
  ].filter(Boolean).join(' og ')

  return (
    <section className="doc-history" aria-labelledby="doc-history-heading">
      <div className="doc-history-head">
        <h2 id="doc-history-heading">Dokumenthistorik</h2>
        <span className="doc-history-summary">
          {counts}{span && ` · ${span}`}
        </span>
        {hidden > 0 && (
          <button
            type="button"
            className="linklike doc-history-toggle"
            aria-expanded={expanded}
            aria-controls="doc-history-timeline"
            onClick={() => setExpanded((value) => !value)}
          >
            {expanded ? 'Vis kun nærmeste versioner' : `Fold hele tidslinjen ud (+${hidden})`}
          </button>
        )}
      </div>
      <ol className="timeline" id="doc-history-timeline">
        {!expanded && hiddenBefore > 0 && (
          <li className="timeline-gap" aria-hidden="true">
            {hiddenBefore} ældre {hiddenBefore === 1 ? 'version' : 'versioner'}
          </li>
        )}
        {shown.map((entry, index) => (
          <li
            key={entry.id ?? `ref-${entry.label}-${index}`}
            className={`timeline-item${entry.current ? ' is-current' : ''}`}
            aria-current={entry.current ? 'page' : undefined}
          >
            <span className="timeline-year">{entry.year || '—'}</span>
            <span className="timeline-body">
              {entry.current
                ? <strong className="timeline-label">{entry.label}</strong>
                : entry.id
                  ? <a className="timeline-label" href={documentHref(entry.id)}>{entry.label}</a>
                  : <span className="timeline-label">{entry.label}</span>}
              {entry.current && <span className="timeline-here">Du læser denne version</span>}
              {entry.status && <StatusTag status={entry.status} />}
              {!entry.id && <span className="timeline-missing">Ikke i databasen</span>}
            </span>
          </li>
        ))}
        {!expanded && hiddenAfter > 0 && (
          <li className="timeline-gap" aria-hidden="true">
            {hiddenAfter} nyere {hiddenAfter === 1 ? 'version' : 'versioner'}
          </li>
        )}
      </ol>
    </section>
  )
}

/**
 * Et valg, der huskes mellem besøg. Browserlageret kan mangle (privat
 * vindue, blokerede data); så gælder standardværdien bare for besøget.
 */
function usePersistentFlag(key, initial) {
  const [value, setValue] = useState(() => {
    try {
      const stored = window.localStorage.getItem(key)
      return stored === null ? initial : stored === '1'
    } catch {
      return initial
    }
  })
  const update = (next) => {
    setValue(next)
    try {
      window.localStorage.setItem(key, next ? '1' : '0')
    } catch {
      // Valget gælder stadig for dette besøg.
    }
  }
  return [value, update]
}

/**
 * Traktattekst, ét afsnit pr. linje. Importen har allerede samlet de trykte
 * spaltelinjer til afsnit (se backendens `parallel_text`), så hver linje er
 * et helt afsnit og vises med luft imellem.
 */
function TreatyText({ text, lang }) {
  const paragraphs = (text || '').split('\n').filter((line) => line.trim())
  if (!paragraphs.length) return <div className="legal-text">Ingen tekst gemt for denne version.</div>
  return (
    <div className="legal-text treaty-text" lang={lang}>
      {paragraphs.map((paragraph, index) => (
        <p className="treaty-paragraph" key={index}>{paragraph}</p>
      ))}
    </div>
  )
}

const LANGUAGE_NAMES = { da: 'Dansk', en: 'English', fr: 'Français' }

/**
 * Sprogvælger: [ Dansk | English ]. Hvert sprog er sit eget dokument
 * (relationen translation_of); et klik navigerer dertil. Samme
 * segmenterede knapper som søgetilstanden på søgesiden.
 */
function LanguageSelector({ document, translations }) {
  const options = [
    { id: document.id, language: document.language || 'da' },
    ...translations.map((t) => ({ id: t.id, language: t.language })),
  ].sort((a, b) => (a.language === 'da' ? -1 : b.language === 'da' ? 1 : a.language.localeCompare(b.language)))

  return (
    <div className="mode-buttons language-selector" role="group" aria-label="Sprog">
      {options.map((option) => {
        const current = option.id === document.id
        return (
          <button
            key={option.id}
            type="button"
            lang={option.language}
            className={`mode${current ? ' active' : ''}`}
            aria-pressed={current}
            onClick={() => { if (!current) navigate(documentHref(option.id)) }}
          >
            {LANGUAGE_NAMES[option.language] || option.language.toUpperCase()}
          </button>
        )
      })}
    </div>
  )
}

/** Til/fra-kontakt i dokumentværktøjslinjen. */
function Switch({ id, label, checked, onChange }) {
  return (
    <span className="switch-field">
      <span className="switch-label" id={`${id}-label`}>{label}</span>
      <button
        type="button"
        role="switch"
        id={id}
        aria-checked={checked}
        aria-labelledby={`${id}-label`}
        className={`switch${checked ? ' is-on' : ''}`}
        onClick={() => onChange(!checked)}
      >
        <span className="switch-track" aria-hidden="true"><span className="switch-thumb" /></span>
        <span className="switch-state">{checked ? 'Til' : 'Fra'}</span>
      </button>
    </span>
  )
}

/** "Udstedt med hjemmel i lov om X, § 3, stk. 2" — under titlen på en bekendtgørelse. */
function LegalBasis({ items }) {
  if (!items?.length) return null
  return (
    <p className="legal-basis">
      Udstedt med hjemmel i{' '}
      {items.map((item, index) => (
        <span key={item.id ?? item.title}>
          {index > 0 && '; '}
          {item.id ? <a href={documentHref(item.id)}>{item.title}</a> : item.title}
          {item.sections?.length > 0 && `, ${item.sections.join(' og ')}`}
        </span>
      ))}
    </p>
  )
}

/** Alle bekendtgørelser udstedt med hjemmel i loven — også dem uden paragraf. */
function DelegatedOverview({ regulations }) {
  if (!regulations?.length) return null
  return (
    <Disclosure summary="Vis bekendtgørelser udstedt med hjemmel i loven" count={regulations.length}>
      <ul className="delegation-list delegation-overview">
        {regulations.map((item) => (
          <li key={`${item.id}-${item.section}`}>
            {item.section && <span className="delegation-section">{item.section}</span>}
            {item.id
              ? <a href={documentHref(item.id)}>{relatedTitle(item)}</a>
              : <span>{relatedTitle(item)}</span>}
          </li>
        ))}
      </ul>
    </Disclosure>
  )
}

function LegalText({ structure, fallback, delegations }) {
  if (!structure?.has_paragraphs) {
    return <div className="legal-text">{fallback || 'Ingen tekst gemt for denne version.'}</div>
  }

  const chapters = structure.chapters || []
  const loose = structure.loose_paragraphs || []

  return (
    <div className="legal-text">
      {loose.map((paragraph) => (
        <Paragraph
          paragraph={paragraph}
          chapterNo={null}
          delegations={delegations}
          key={paragraph.paragraph_id}
        />
      ))}
      {chapters.map((chapter) => (
        <section className="chapter" key={chapter.number}>
          <h3 className="chapter-heading">
            Kapitel {chapter.number}
            {chapter.title && <span className="chapter-title">{chapter.title}</span>}
          </h3>
          {chapter.paragraphs.map((paragraph) => (
            <Paragraph
              paragraph={paragraph}
              chapterNo={chapter.number}
              delegations={delegations}
              key={paragraph.paragraph_id}
            />
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
  // Slået fra fremstår dokumentet udelukkende som den autoritative lovtekst.
  const [showDelegations, setShowDelegations] = usePersistentFlag(
    'maritim.visTilknyttedeForskrifter', true,
  )

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
  const translations = document?.translations || []
  // Traktattekst (engelsk original eller dansk kundgørelse af den) uden
  // paragrafstruktur vises med ét afsnit pr. linje.
  const treatyText = !showingHistoric && !structure?.has_paragraphs
    && (document?.language === 'en' || translations.length > 0)
  const structuredView = !showingHistoric && !fullText && Boolean(structure?.has_paragraphs)
  useScrollToAnchor(query?.p, query?.q, !loading && structuredView)

  if (loading) return <Loading label="Henter dokument…" />
  if (error) return <ErrorBox error={error} onRetry={load} />
  if (!document) return null

  return (
    <article className="document" lang={document.language || 'da'}>
      <p className="back-link"><a href="#/">← Tilbage til søgning</a></p>

      <SyntheticWarning text={document.synthetic_notice} />

      <header className="doc-header">
        <div className="result-badges">
          <StatusTag status={document.status} />
          <LawClassTag value={document.law_class} label={document.law_class_label} />
          <span className="badge-plain">{documentTypeLabel(document.document_type)}</span>
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

        <LegalBasis items={document.legal_basis} />
      </header>

      <DocumentHistory document={document} />

      <div className="doc-body">
        {showingHistoric && (
          <div className="stale-warning">
            Du ser en historisk version. Den er bevaret uændret som den blev hentet.{' '}
            <button className="version-button" onClick={() => setActiveVersion(null)}>
              Vis aktuel version
            </button>
          </div>
        )}

        {!showingHistoric && (structure?.has_paragraphs || translations.length > 0) && (
          <div className="doc-toolbar" role="group" aria-label="Visning">
            {translations.length > 0 && (
              <LanguageSelector document={document} translations={translations} />
            )}
            {structure?.has_paragraphs && (
              <button
                type="button"
                className="linklike full-text-toggle"
                onClick={() => setFullText((value) => !value)}
              >
                {fullText ? 'Vis som kapitler og paragraffer' : 'Vis den fulde tekst som ét dokument'}
              </button>
            )}
            {structuredView && document.delegated_regulations?.length > 0 && (
              <Switch
                id="delegations-switch"
                label="Tilknyttede forskrifter"
                checked={showDelegations}
                onChange={setShowDelegations}
              />
            )}
          </div>
        )}

        {!showingHistoric && <TableOfContents structure={structure} documentId={document.id} />}

        {treatyText ? (
          <TreatyText text={rawText} lang={document.language} />
        ) : showingHistoric || fullText || !structure?.has_paragraphs ? (
          <div className="legal-text">{rawText || 'Ingen tekst gemt for denne version.'}</div>
        ) : (
          <LegalText
            structure={structure}
            fallback={rawText}
            delegations={showDelegations ? delegationsByParagraph(document.delegated_regulations) : null}
          />
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

        <DelegatedOverview regulations={document.delegated_regulations} />

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

        <Disclosure summary="Vis hentede versioner og ændringslog" count={document.versions?.length}>
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
