/** Fælles visningsformatering. Dansk sprogbrug og datoformat. */

export function formatDate(value) {
  if (!value) return '—'
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return '—'
  return date.toLocaleDateString('da-DK', { year: 'numeric', month: 'long', day: 'numeric' })
}

export function formatDateTime(value) {
  if (!value) return '—'
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return '—'
  return date.toLocaleString('da-DK', {
    year: 'numeric', month: '2-digit', day: '2-digit',
    hour: '2-digit', minute: '2-digit',
  })
}

/** Farvebånd for maritim score. Tærsklerne følger relevansmotorens. */
export function scoreBand(score) {
  if (score >= 60) return 'high'
  if (score >= 30) return 'mid'
  return 'low'
}

export function classificationLabel(classification) {
  return {
    maritime: 'Maritimt',
    possible: 'Mulig maritim relevans',
    not_maritime: 'Ikke maritimt',
  }[classification] || 'Ukendt'
}

/** Status afgør om reglen kan anvendes — derfor tydelig markering. */
export function statusClass(status) {
  if (!status) return 'historic'
  const value = status.toLowerCase()
  if (value.startsWith('gæld')) return 'current'
  if (value.startsWith('ophæv')) return 'repealed'
  return 'historic'
}

export const FIELD_LABELS = {
  title: 'Titel',
  authority: 'Myndighed',
  metadata: 'Metadata',
  content: 'Dokumenttekst',
}
