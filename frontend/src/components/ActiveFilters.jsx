/**
 * Aktive filtre som chips.
 *
 * Et filterpanel, der er foldet sammen eller ligger i en lukket skuffe,
 * kan skjule at der overhovedet ER filtreret. En bruger, der har glemt
 * et statusfilter fra sidste søgning, konkluderer så, at der ikke findes
 * regler om emnet. Chipsene står derfor lige under søgefeltet, hvor
 * resultaterne er — og hver enkelt kan fjernes med ét klik.
 */

import { LAW_CLASSES } from '../lib/format.js'

/** Bygger listen over aktive filtre med hver sin fjern-handling. */
function collect(filters, facets) {
  const chips = []
  const categoryName = (slug) =>
    (facets?.categories || []).find((c) => c.slug === slug)?.name || slug

  const push = (key, value, label) => {
    chips.push({
      id: `${key}:${value}`,
      label,
      remove: (current) => ({
        ...current,
        [key]: (current[key] || []).filter((v) => v !== value),
        page: 1,
      }),
    })
  }

  for (const value of filters.law_class || []) {
    push('law_class', value, LAW_CLASSES[value]?.label || value)
  }
  for (const value of filters.category || []) push('category', value, categoryName(value))
  for (const value of filters.status || []) push('status', value, value)
  for (const value of filters.document_type || []) push('document_type', value, value)
  for (const value of filters.authority || []) push('authority', value, value)

  if (filters.min_score) {
    chips.push({
      id: 'min_score',
      label: `Maritim relevans ≥ ${filters.min_score}`,
      remove: (current) => ({ ...current, min_score: null, page: 1 }),
    })
  }
  if (filters.published_from) {
    chips.push({
      id: 'published_from',
      label: `Fra ${filters.published_from}`,
      remove: (current) => ({ ...current, published_from: null, page: 1 }),
    })
  }
  if (filters.published_to) {
    chips.push({
      id: 'published_to',
      label: `Til ${filters.published_to}`,
      remove: (current) => ({ ...current, published_to: null, page: 1 }),
    })
  }
  return chips
}

export function activeFilterCount(filters) {
  return collect(filters, null).length
}

export default function ActiveFilters({ filters, facets, onChange, onReset }) {
  const chips = collect(filters, facets)
  if (chips.length === 0) return null

  return (
    <div className="active-filters" aria-label="Aktive filtre">
      {chips.map((chip) => (
        <button
          key={chip.id}
          type="button"
          className="chip removable"
          onClick={() => onChange(chip.remove(filters))}
          title="Fjern filter"
        >
          {chip.label}
          <span aria-hidden="true">✕</span>
        </button>
      ))}
      {chips.length > 1 && (
        <button type="button" className="linklike clear-all" onClick={onReset}>
          Ryd alle
        </button>
      )}
    </div>
  )
}
