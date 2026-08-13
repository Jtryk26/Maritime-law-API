/** Små, genbrugte visningskomponenter. */

import { scoreBand, statusClass } from '../lib/format.js'

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
