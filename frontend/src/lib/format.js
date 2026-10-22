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

/* --- Dybde-links til paragraffer --------------------------------------- */
/*
 * Appen router på URL'ens hash (#/dokument/12), så et klassisk anker
 * ("#p-3-12") kan ikke stå efter ruten — det ville blive læst som en ny
 * rute. Ankeret sendes derfor som query-parameteren `p`:
 *
 *   #/dokument/12?p=p-3-12        kapitel 3, § 12
 *   #/dokument/12?p=p-3-12a-stk2  kapitel 3, § 12 a, stk. 2
 *   #/dokument/12?p=p-12          § 12 uden for kapitel
 *
 * Samme funktion bygger både linket i søgeresultatet og elementets DOM-id
 * på dokumentsiden, så de to ikke kan glide fra hinanden.
 */

function anchorSlug(value) {
  return String(value ?? '')
    .toLowerCase()
    .replace(/§/g, '')
    .replace(/[^0-9a-zæøå]+/g, '')
}

/** Paragrafnummeret alene, som det står i ankeret: "§ 12 a" -> "12a". */
export function paragraphKey(paragraphId) {
  return anchorSlug(paragraphId)
}

/** DOM-id for en paragraf eller et stykke: "p-3-12a" / "p-3-12a-stk2". */
export function paragraphAnchor(chapterNo, paragraphId, stk = null) {
  const paragraph = anchorSlug(paragraphId)
  if (!paragraph) return null
  const chapter = anchorSlug(chapterNo)
  const base = chapter ? `p-${chapter}-${paragraph}` : `p-${paragraph}`
  return stk ? `${base}-stk${stk}` : base
}

/**
 * Splitter et anker op igen. Bruges som reserve på dokumentsiden: findes
 * det præcise id ikke (fx fordi kapitelnummeret er læst anderledes),
 * kan paragraffen stadig findes på sit nummer, der er entydigt i loven.
 */
export function parseParagraphAnchor(anchor) {
  const match = /^p-(?:([0-9a-zæøå]+)-)?([0-9]+[a-zæøå]*)(?:-stk([0-9]+))?$/.exec(anchor || '')
  if (!match) return null
  return { chapter: match[1] || null, paragraph: match[2], stk: match[3] ? Number(match[3]) : null }
}

/** Link til et dokument, eventuelt direkte til en paragraf. */
export function documentHref(documentId, anchor = null) {
  return anchor
    ? `#/dokument/${documentId}?p=${encodeURIComponent(anchor)}`
    : `#/dokument/${documentId}`
}

/**
 * Deler en paragraftekst i stykker.
 *
 * Strukturen fra API'et har paragraffen som én tekst. Stykkerne står
 * enten på hver sin linje ("…\nStk. 2. …") eller — når kilden er leveret
 * på én linje — inde i teksten efter et punktum. Et "Stk. N." godtages
 * kun som nyt stykke, hvis nummeret er det næste i rækken; ellers er det
 * sandsynligvis en henvisning og bliver stående i teksten.
 */
export function splitSubsections(text) {
  if (!text) return []
  // Den fangede gruppe beholder skillet, så et afvist "Stk. N." kan
  // sættes tilbage med præcis det mellemrum eller linjeskift, det havde.
  const parts = text.split(/(\n+|(?<=[.:;])[ \t]+(?=Stk\.\s*\d+\s*\.))/)
  const subsections = [{ number: 1, text: parts[0] }]
  for (let i = 1; i < parts.length; i += 2) {
    const separator = parts[i]
    const piece = parts[i + 1] ?? ''
    const match = /^\s*Stk\.\s*(\d+)\s*\./.exec(piece)
    const last = subsections[subsections.length - 1]
    if (match && Number(match[1]) === last.number + 1) {
      subsections.push({ number: Number(match[1]), text: piece })
    } else {
      last.text += separator + piece
    }
  }
  return subsections
}
