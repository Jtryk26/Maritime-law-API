/**
 * Paragrafankre.
 *
 * Applikationen bruger hash-routing. Et almindeligt sideanker — `#p-12` —
 * er derfor ikke uskyldigt: det overskriver hele hashen, routeren læser
 * `#p-12` som en ukendt sti og falder tilbage til søgesiden. Brugeren
 * klikker på en paragraf og bliver kastet tilbage til forsiden.
 *
 * Ankre skrives i stedet ind i ruten som et query-parameter,
 *
 *     #/dokument/3190?p=p-70-stk-4
 *
 * så ruten overlever klikket. Selve rulningen til elementet gør
 * dokumentsiden selv — browserens indbyggede ankerspring kan ikke bruges,
 * når ankeret ikke står i hashen.
 */

/**
 * Gør et paragraf-id til et URL-sikkert anker.
 *
 *   "§ 12 a"          -> "p-12-a"
 *   "§ 70, stk. 4"    -> "p-70-stk-4"
 *
 * Funktionen bruges både når ankeret sættes på elementet og når linket
 * bygges. De to må aldrig komme fra hver sin implementering — så peger
 * halvdelen af henvisningerne på et element, der ikke findes.
 */
export function anchorFor(paragraphId) {
  return `p-${(paragraphId || '')
    .replace(/[§\s.,;:]+/g, '-')
    .replace(/-{2,}/g, '-')
    .replace(/^-+|-+$/g, '')
    .toLowerCase()}`
}

/** Dyb henvisning til en paragraf i et dokument. */
export function paragraphHref(documentId, paragraphId) {
  if (!paragraphId) return `#/dokument/${documentId}`
  return `#/dokument/${documentId}?p=${encodeURIComponent(anchorFor(paragraphId))}`
}
