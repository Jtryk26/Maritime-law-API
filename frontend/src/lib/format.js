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

/**
 * Dokumentklasserne i klartekst.
 *
 * Brugeren skal kunne se, hvorfor en bred bekendtgørelse står før en
 * særregel — og hvad "speciallov" overhovedet dækker. Etiketterne
 * kommer også fra API'et; disse er reserven, når feltet mangler.
 */
export const LAW_CLASSES = {
  kernelaw: {
    label: 'Kernelov',
    short: 'Kernelov',
    title: 'Bredt anvendeligt, centralt regelsæt.',
  },
  speciallaw: {
    label: 'Speciallov',
    short: 'Speciallov',
    title: 'Smal anvendelse — bestemte fartøjstyper, farvande eller personkredse.',
  },
  support: {
    label: 'Støttedokument',
    short: 'Støtte',
    title: 'Vejledning, ændringsbekendtgørelse eller cirkulære.',
  },
}

export function lawClass(value) {
  return LAW_CLASSES[value] || null
}

/** Titlen der vises i brugerfladen. Falder tilbage til den fulde. */
export function displayTitle(item) {
  return item?.display_title || item?.title || ''
}

export const INTENT_LABELS = {
  broad: 'Bred søgning',
  semi: 'Semispecifik søgning',
  niche: 'Nichesøgning',
}

export const FIELD_LABELS = {
  title: 'Titel',
  authority: 'Myndighed',
  metadata: 'Metadata',
  content: 'Dokumenttekst',
}
