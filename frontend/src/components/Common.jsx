/** Små, genbrugte visningskomponenter. */

import { useId, useState } from 'react'
import { lawClass, scoreBand, statusClass } from '../lib/format.js'

export function Loading({ label = 'Henter…' }) {
  return (
    <>
      <div className="spinner-line" aria-hidden="true"><i /></div>
      <div className="loading">{label}</div>
    </>
  )
}

export function ErrorBox({ error, onRetry }) {
  return (
    <div className="error-box">
      <div>{error?.message || 'Der opstod en fejl.'}</div>
      {onRetry && (
        <p style={{ marginBottom: 0 }}>
          <button onClick={onRetry} style={{ marginTop: 12 }}>Prøv igen</button>
        </p>
      )}
    </div>
  )
}

export function Empty({ title, children }) {
  return (
    <div className="empty">
      <strong>{title}</strong>
      {children}
    </div>
  )
}

/** Retlig status. Gældende vs. ophævet er afgørende ved regelefterlevelse. */
export function StatusTag({ status }) {
  if (!status) return null
  return <span className={`status ${statusClass(status)}`}>{status}</span>
}

export function ScoreTag({ score }) {
  return (
    <span className={`score ${scoreBand(score)}`} title="Maritim relevansscore (0–100)">
      {score}
    </span>
  )
}

/** Markerer syntetiske testdata. Må aldrig fremstå som officiel kilde. */
export function SyntheticBadge() {
  return <span className="badge-synthetic" title="Syntetiske testdata — ikke gældende ret">Testdata</span>
}

/**
 * Dokumentets rolle i regelhierarkiet.
 *
 * Uden mærkatet ser en ændringsbekendtgørelse ud som en regel, og en
 * særregel om fiskeskibe ser ud som noget, der gælder alle skibe. Netop
 * det er forskellen mellem at finde det rigtige og at tro, man har det.
 */
export function LawClassTag({ value, label }) {
  const meta = lawClass(value)
  if (!meta) return null
  return (
    <span className={`law-class law-${value}`} title={meta.title}>
      {label || meta.short}
    </span>
  )
}

/**
 * Fold-ud til sekundært indhold.
 *
 * Bruges til præambel, metadata og historik på dokumentsiden. De skal
 * være tilgængelige, men de må ikke stå mellem brugeren og lovteksten.
 * <details> frem for egen tilstand: den virker uden JavaScript, kan
 * søges i af browserens egen sidesøgning, og udskrives åben.
 */
export function Disclosure({ summary, children, open = false, count }) {
  return (
    <details className="disclosure" open={open}>
      <summary>
        {summary}
        {count !== undefined && <span className="disclosure-count">{count}</span>}
      </summary>
      <div className="disclosure-body">{children}</div>
    </details>
  )
}

/** Sammenfoldelig gruppe i filterpanelet. */
export function FilterSection({ title, count, children, defaultOpen = true }) {
  const [open, setOpen] = useState(defaultOpen)
  const id = useId()
  return (
    <div className={`filter-group ${open ? 'is-open' : ''}`}>
      <button
        type="button"
        className="filter-group-head"
        aria-expanded={open}
        aria-controls={id}
        onClick={() => setOpen((value) => !value)}
      >
        <span>{title}</span>
        {count > 0 && <span className="filter-group-count">{count}</span>}
        <span className="filter-group-caret" aria-hidden="true">{open ? '▾' : '▸'}</span>
      </button>
      <div id={id} hidden={!open}>{children}</div>
    </div>
  )
}

export function LegalNotice({ text }) {
  return <div className="legal-notice">{text}</div>
}

export function SyntheticWarning({ text }) {
  if (!text) return null
  return (
    <div className="synthetic-warning">
      <strong>Syntetiske testdata. </strong>{text}
    </div>
  )
}
