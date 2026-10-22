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

/**
 * Retsinformations korte dokumenttyper. API'et leverer normalt allerede
 * forkortelsen (BEK, LBK …); de fulde navne er reserven for ældre data.
 */
const DOCUMENT_TYPE_CODES = {
  lov: 'LOV',
  lovbekendtgørelse: 'LBK',
  bekendtgørelse: 'BEK',
  vejledning: 'VEJ',
  cirkulære: 'CIR',
  skrivelse: 'SKR',
  'ændringsbekendtgørelse': 'BEK',
}

/**
 * Retskildehenvisningen som den citeres: "BEK nr. 101 af 14. april 2023".
 * Det er den streng, en bruger skriver af i en rapport — derfor står den
 * øverst på resultatet og ikke gemt i metadata.
 */
export function sourceReference(item) {
  if (!item) return ''
  const raw = (item.document_type || '').trim()
  const code = DOCUMENT_TYPE_CODES[raw.toLowerCase()] || raw
  const parts = []
  if (code) parts.push(code)
  if (item.document_number) parts.push(`nr. ${item.document_number}`)
  if (parts.length && item.published_date) {
    const date = formatDate(item.published_date)
    if (date !== '—') parts.push(`af ${date}`)
  }
  return parts.join(' ')
}

/**
 * Deler en tekst op i stykker, hvor søgetermerne er markeret.
 *
 * Ren klientside-fremhævning: søgestrengen splittes på mellemrum, hvert
 * ord escapes og matches uden hensyn til store/små bogstaver. Der
 * returneres tekststykker — aldrig HTML — så React escaper alt selv.
 * Korte ord (under tre tegn) springes over; ellers lyser "og" og "af"
 * op over hele snippet'et.
 */
export function highlightSegments(text, query) {
  if (!text) return []
  const terms = [...new Set(
    (query || '')
      .toLowerCase()
      .split(/[\s"'()«»,.;:!?]+/)
      .map((term) => term.replace(/^[-+*]+|[-+*]+$/g, ''))
      .filter((term) => term.length >= 3),
  )].slice(0, 8)
  if (!terms.length) return [{ text, match: false }]

  const escaped = terms
    .sort((a, b) => b.length - a.length)
    .map((term) => term.replace(/[.*+?^${}()|[\]\\]/g, '\\$&'))
  const pattern = new RegExp(`(${escaped.join('|')})`, 'giu')

  return text
    .split(pattern)
    .filter((part) => part !== '')
    .map((part) => ({ text: part, match: terms.includes(part.toLowerCase()) }))
}
