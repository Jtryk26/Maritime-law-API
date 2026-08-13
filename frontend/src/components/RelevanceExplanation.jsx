/**
 * Forklaring på den maritime klassifikation.
 *
 * Systemet arbejder med lovgivning, hvor en uforklaret vurdering er
 * ubrugelig. Panelet viser derfor hele regnestykket: hvilke termer der
 * talte, hvor de stod, hvad de bidrog med, hvilke negative signaler der
 * trak fra — og hvilken version af teksten vurderingen gælder.
 */

import { useState } from 'react'
import { FIELD_LABELS, classificationLabel, scoreBand } from '../lib/format.js'

function TermRows({ terms, negative = false }) {
  return terms.map((term, index) => (
    <tr key={`${term.term}-${term.field}-${index}`} className={negative ? 'negative' : undefined}>
      <td>
        {term.term}
        {term.capped && (
          <span className="capped" title="Loftet for gentagelser blev nået"> · loft</span>
        )}
      </td>
      <td>{FIELD_LABELS[term.field] || term.field}</td>
      <td className="num">{term.occurrences}</td>
      <td className="num">
        {negative ? '−' : ''}{term.contribution.toFixed(1)}
      </td>
    </tr>
  ))
}

export default function RelevanceExplanation({ relevance }) {
  const [showAll, setShowAll] = useState(false)
  if (!relevance) return null

  const calc = relevance.calculation || {}
  const band = scoreBand(relevance.score)
  const matches = relevance.matches || []
  const negatives = relevance.negative_matches || []
  const visible = showAll ? matches : matches.slice(0, 8)

  const fieldContributions = Object.entries(calc.field_contributions || {})
    .sort((a, b) => b[1] - a[1])

  return (
    <div className="panel">
      <h2>Maritim relevans</h2>
      <div className="panel-body">
        {relevance.is_stale && (
          <div className="stale-warning">
            Vurderingen blev foretaget på version {relevance.evaluated_version_number} af
            teksten, men dokumentet har siden fået en nyere version. Kør en import for
            at opdatere klassifikationen.
          </div>
        )}

        <div className={`score-headline score ${band}`} style={{ border: 'none', padding: 0 }}>
          <span className="big">{relevance.score}</span>
          <div>
            <div style={{ fontWeight: 600 }}>{classificationLabel(relevance.classification)}</div>
            <div style={{ fontSize: 12, color: 'var(--ink-muted)' }}>
              af 100 · tærskel for maritim: {calc.thresholds?.maritime ?? 60}
            </div>
          </div>
        </div>

        <div className={`score-bar score ${band}`} style={{ border: 'none', padding: 0 }}>
          <i style={{ width: `${relevance.score}%` }} />
        </div>

        <p className="reason">{relevance.reason}</p>

        {relevance.evaluated_version_number && (
          <p style={{ fontSize: 12.5, color: 'var(--ink-muted)', marginTop: -4 }}>
            Vurderet på version {relevance.evaluated_version_number} · motor:{' '}
            <code>{relevance.engine}</code>
          </p>
        )}

        {fieldContributions.length > 0 && (
          <>
            <h3 style={{ fontSize: 11, textTransform: 'uppercase', letterSpacing: '0.06em',
                         color: 'var(--ink-faint)', margin: '16px 0 6px' }}>
              Bidrag pr. felt
            </h3>
            {fieldContributions.map(([field, value]) => (
              <div className="calc-row" key={field}>
                <span>{FIELD_LABELS[field] || field}</span>
                <span>{value.toFixed(1)}</span>
              </div>
            ))}
          </>
        )}

        <h3 style={{ fontSize: 11, textTransform: 'uppercase', letterSpacing: '0.06em',
                     color: 'var(--ink-faint)', margin: '16px 0 6px' }}>
          Regnestykke
        </h3>
        <div className="calc-row"><span>Termbidrag</span><span>{calc.positive_raw?.toFixed(1)}</span></div>
        {calc.concept_bonus > 0 && (
          <div className="calc-row">
            <span>Breddebonus ({relevance.concepts?.length} begreber)</span>
            <span>+{calc.concept_bonus.toFixed(1)}</span>
          </div>
        )}
        {calc.negative_raw > 0 && (
          <div className="calc-row"><span>Negative signaler</span><span>−{calc.negative_raw.toFixed(1)}</span></div>
        )}
        <div className="calc-row total">
          <span>Rå score</span><span>{calc.raw_score?.toFixed(1)}</span>
        </div>
        <div className="calc-row">
          <span>Normaliseret (mætning {calc.saturation})</span>
          <span>{calc.normalized_score}</span>
        </div>
        {calc.title_floor_applied && (
          <div className="calc-row">
            <span>Titelautoritet: {calc.title_floor_terms?.join(', ')}</span>
            <span>→ {calc.normalized_score}</span>
          </div>
        )}

        {matches.length > 0 && (
          <>
            <h3 style={{ fontSize: 11, textTransform: 'uppercase', letterSpacing: '0.06em',
                         color: 'var(--ink-faint)', margin: '16px 0 6px' }}>
              Matchede maritime termer
            </h3>
            <table className="term-table">
              <thead>
                <tr><th>Term</th><th>Felt</th><th style={{ textAlign: 'right' }}>Antal</th>
                    <th style={{ textAlign: 'right' }}>Bidrag</th></tr>
              </thead>
              <tbody>
                <TermRows terms={visible} />
                {negatives.length > 0 && <TermRows terms={negatives} negative />}
              </tbody>
            </table>
            {matches.length > 8 && (
              <button style={{ marginTop: 10, fontSize: 13 }} onClick={() => setShowAll(!showAll)}>
                {showAll ? 'Vis færre' : `Vis alle ${matches.length} termer`}
              </button>
            )}
          </>
        )}

        {relevance.concepts?.length > 0 && (
          <>
            <h3 style={{ fontSize: 11, textTransform: 'uppercase', letterSpacing: '0.06em',
                         color: 'var(--ink-faint)', margin: '16px 0 6px' }}>
              Dækkede maritime begreber
            </h3>
            <div className="chips">
              {relevance.concepts.map((c) => <span className="chip plain" key={c}>{c}</span>)}
            </div>
          </>
        )}
      </div>
    </div>
  )
}
