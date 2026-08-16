/**
 * Facetfiltre.
 *
 * Én komponent, to placeringer: den klæbende sidebjælke på desktop og
 * skuffen på mobil. Filtrene må ikke være to forskellige implementeringer
 * — så ville de før eller siden holde op med at kunne det samme, og
 * mobilbrugeren ville få den fattige udgave.
 *
 * Værdierne hentes fra /api/facets, så brugerfladen ikke hardcoder
 * myndigheder, typer eller status. Filtrene håndhæves i API'et.
 *
 * Lange grupper er foldet sammen som standard. Kategori og status er
 * åbne, fordi det er dem, folk faktisk bruger; myndighed og type er
 * lukkede, fordi de er lange og sjældnere.
 */

import { useEffect, useState } from 'react'
import { FilterSection } from './Common.jsx'

function CheckboxGroup({ options, selected, onToggle, scroll = false }) {
  if (!options?.length) return null
  return (
    <div className={scroll ? 'filter-scroll' : undefined}>
      {options.map((option) => (
        <label className="filter-option" key={option.value}>
          <input
            type="checkbox"
            checked={selected.includes(option.value)}
            onChange={() => onToggle(option.value)}
          />
          <span className="filter-option-label">
            {option.label}
            {option.description && (
              <small className="filter-option-hint">{option.description}</small>
            )}
          </span>
          {option.count !== undefined && <span className="count">{option.count}</span>}
        </label>
      ))}
    </div>
  )
}

export default function SearchFilters({ facets, filters, onChange, onReset }) {
  const [minScore, setMinScore] = useState(filters.min_score ?? 0)

  // Skuffen på mobil kan åbnes med filtre, som blev sat andetsteds fra
  // (fx en chip der blev fjernet). Skyderen skal følge med.
  useEffect(() => { setMinScore(filters.min_score ?? 0) }, [filters.min_score])

  const toggle = (key, value) => {
    const current = filters[key] || []
    const next = current.includes(value)
      ? current.filter((v) => v !== value)
      : [...current, value]
    onChange({ ...filters, [key]: next, page: 1 })
  }

  const categories = (facets?.categories || [])
    .filter((c) => c.document_count > 0)
    .map((c) => ({ value: c.slug, label: c.name, count: c.document_count }))

  const lawClasses = (facets?.law_classes || [])
    .filter((c) => c.count > 0)
    .map((c) => ({
      value: c.value, label: c.label, count: c.count, description: c.description,
    }))

  const simple = (list) => (list || []).map((f) => ({
    value: f.value, label: f.value, count: f.count,
  }))

  const n = (key) => filters[key]?.length || 0

  return (
    <div className="filters-inner">
      <FilterSection title="Dokumentets rolle" count={n('law_class')}>
        <CheckboxGroup
          options={lawClasses}
          selected={filters.law_class || []}
          onToggle={(v) => toggle('law_class', v)}
        />
      </FilterSection>

      <FilterSection title="Kategori" count={n('category')}>
        <CheckboxGroup
          options={categories}
          selected={filters.category || []}
          onToggle={(v) => toggle('category', v)}
          scroll
        />
      </FilterSection>

      <FilterSection title="Status" count={n('status')}>
        <CheckboxGroup
          options={simple(facets?.statuses)}
          selected={filters.status || []}
          onToggle={(v) => toggle('status', v)}
        />
      </FilterSection>

      <FilterSection title="Dokumenttype" count={n('document_type')} defaultOpen={false}>
        <CheckboxGroup
          options={simple(facets?.document_types)}
          selected={filters.document_type || []}
          onToggle={(v) => toggle('document_type', v)}
        />
      </FilterSection>

      <FilterSection title="Myndighed" count={n('authority')} defaultOpen={false}>
        <CheckboxGroup
          options={simple(facets?.authorities)}
          selected={filters.authority || []}
          onToggle={(v) => toggle('authority', v)}
          scroll
        />
      </FilterSection>

      <FilterSection
        title="Maritim relevans"
        count={filters.min_score ? 1 : 0}
        defaultOpen={false}
      >
        <div className="range-row">
          <input
            type="range" min="0" max="100" step="5" value={minScore}
            aria-label="Mindste maritime score"
            onChange={(e) => setMinScore(Number(e.target.value))}
            onMouseUp={() => onChange({ ...filters, min_score: minScore || null, page: 1 })}
            onTouchEnd={() => onChange({ ...filters, min_score: minScore || null, page: 1 })}
            onKeyUp={() => onChange({ ...filters, min_score: minScore || null, page: 1 })}
          />
          <strong className="range-value">{minScore}</strong>
        </div>
      </FilterSection>

      <FilterSection
        title="Publiceret"
        count={(filters.published_from ? 1 : 0) + (filters.published_to ? 1 : 0)}
        defaultOpen={false}
      >
        <label className="filter-date">
          <span>Fra</span>
          <input
            type="date"
            value={filters.published_from || ''}
            onChange={(e) => onChange({ ...filters, published_from: e.target.value || null, page: 1 })}
          />
        </label>
        <label className="filter-date">
          <span>Til</span>
          <input
            type="date"
            value={filters.published_to || ''}
            onChange={(e) => onChange({ ...filters, published_to: e.target.value || null, page: 1 })}
          />
        </label>
      </FilterSection>

      <button className="filter-reset" onClick={() => { setMinScore(0); onReset() }}>
        Ryd alle filtre
      </button>
    </div>
  )
}
