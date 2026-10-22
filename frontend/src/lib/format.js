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

/**
 * DOM-id for en paragraf, et stykke eller et nummereret punkt:
 * "p-3-12a", "p-3-12a-stk2", "p-1-nr10", "p-1-stk2-nr3".
 *
 * Et punkt i paragraffens første stykke får ikke "-stk1" med: det er
 * sådan bestemmelsen citeres ("§ 1, nr. 10"), og ankeret skal kunne
 * skrives af en person.
 */
export function paragraphAnchor(chapterNo, paragraphId, stk = null, nr = null) {
  const paragraph = anchorSlug(paragraphId)
  if (!paragraph) return null
  const chapter = anchorSlug(chapterNo)
  let anchor = chapter ? `p-${chapter}-${paragraph}` : `p-${paragraph}`
  if (stk && !(nr && stk === 1)) anchor += `-stk${stk}`
  if (nr) anchor += `-nr${nr}`
  return anchor
}

/**
 * Splitter et anker op igen. Bruges som reserve på dokumentsiden: findes
 * det præcise id ikke (fx fordi kapitelnummeret er læst anderledes),
 * kan paragraffen stadig findes på sit nummer, der er entydigt i loven.
 */
export function parseParagraphAnchor(anchor) {
  const match = /^p-(?:([0-9a-zæøå]+)-)?([0-9]+[a-zæøå]*)(?:-stk([0-9]+))?(?:-nr([0-9]+))?$/
    .exec(anchor || '')
  if (!match) return null
  return {
    chapter: match[1] || null,
    paragraph: match[2],
    stk: match[3] ? Number(match[3]) : null,
    nr: match[4] ? Number(match[4]) : null,
  }
}

/**
 * Link til et dokument, eventuelt direkte til en paragraf.
 *
 * Søgestrengen kan følge med som `q`. API'et peger kun på paragraffen,
 * så står paragraffen som en lang nummereret liste — fx en
 * ophævelsesbekendtgørelse med 20+ punkter — bruger dokumentsiden
 * søgeordene til at finde det punkt, der faktisk matchede.
 */
export function documentHref(documentId, anchor = null, query = null) {
  if (!anchor) return `#/dokument/${documentId}`
  const params = new URLSearchParams({ p: anchor })
  if (query) params.set('q', query)
  return `#/dokument/${documentId}?${params.toString()}`
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

/**
 * Deler et stykke i indledning og nummererede punkter.
 *
 *   "Følgende ophæves: 1) Bekendtgørelse nr. 101 …, 2) Bekendtgørelse …"
 *   -> { lead: "Følgende ophæves:", items: [{ nr: 1, text: "Bekendtgørelse nr. 101 …," }, …] }
 *
 * Talparenteser forekommer også i almindelig tekst ("jf. stk. 1, nr. 3)"),
 * så reglerne er bevidst stramme:
 *   - punkt 1) skal følge efter et kolon (evt. med linjeskift imellem) —
 *     det er sådan en opregning indledes i dansk lovsprog;
 *   - hvert næste punkt skal have præcis det næste nummer og stå efter
 *     et linjeskift eller efter det, der afslutter et punkt: "," ";" "."
 *     eller "og"/"eller". Så bliver "(del 2) og …" inde i et punkt ikke
 *     læst som punkt 2;
 *   - et tal efter "nr.", "stk." eller "pkt." er en henvisning, ikke et punkt;
 *   - der skal være mindst to punkter.
 * Holder mønstret ikke, returneres `items: []`, og teksten vises uændret.
 */
export function splitListItems(text) {
  const empty = { lead: text || '', items: [] }
  if (!text) return empty

  const start = /:\s+1\)\s/.exec(text)
  if (!start) return empty
  const leadEnd = start.index + 1

  // Alle "n) " der står som selvstændigt ord. Derefter vælges dem, der
  // danner rækken 1, 2, 3 … og hver er indledt korrekt.
  const candidates = [...text.matchAll(/(?<!\S)(\d+)\)\s/g)]
    .filter((match) => match.index >= start.index)
  const markers = []
  for (const match of candidates) {
    const nr = Number(match[1])
    if (nr !== markers.length + 1) continue
    if (nr > 1) {
      const before = text.slice(markers[markers.length - 1].textAt, match.index)
      const closesItem = /(?:[,;.]|\b(?:og|eller))\s*$/.test(before) || /\n\s*$/.test(before)
      // "jf. § 3, nr. 2) og …" er en henvisning: punktummet tilhører "nr.".
      const isReference = /\b(?:nr|stk|pkt|litra|jf)\.\s*$/i.test(before)
      if (!closesItem || isReference) continue
    }
    markers.push({ nr, at: match.index, textAt: match.index + match[1].length + 1 })
  }
  if (markers.length < 2) return empty

  const items = markers.map((marker, index) => {
    const end = index + 1 < markers.length ? markers[index + 1].at : text.length
    return { nr: marker.nr, text: text.slice(marker.textAt, end).trim() }
  })
  return { lead: text.slice(0, leadEnd).trim(), items }
}
