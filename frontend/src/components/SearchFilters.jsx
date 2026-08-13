/**
 * Facetfiltre.
 *
 * Værdierne hentes fra /api/facets, så brugerfladen ikke hardcoder
 * myndigheder, typer eller status. Filtrene håndhæves i API'et.
 */

import { useState } from 'react'

function CheckboxGroup({ title, options, selected, onToggle, scroll = false }) {
  if (!options?.length) return null
  return (
    <div className="filter-group">
      <h3>{title}</h3>
      <div className={scroll ? 'filter-scroll' : undefined}>
        {options.map((option) => (
          <label className="filter-option" key={option.value}>
            <input
              type="checkbox"
              checked={selected.includes(option.value)}
              onChange={() => onToggle(option.value)}
            />
            <span>{option.label}</span>
            {option.count !== undefined && <span className="count">{option.count}</span>}
          </label>
        ))}
      </div>
    </div>
  )
}

export default function SearchFilters({ facets, filters, onChange, onReset }) {
  const [minScore, setMinScore] = useState(filters.min_score ?? 0)

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

  const simple = (list) => (list || []).map((f) => ({
    value: f.value, label: f.value, count: f.count,
  }))

  return (
    <aside className="filters" aria-label="Filtre">
      <CheckboxGroup
        title="Kategori"
        options={categories}
        selected={filters.category || []}
        onToggle={(v) => toggle('category', v)}
        scroll
      />
      <CheckboxGroup
        title="Status"
        options={simple(facets?.statuses)}
        selected={filters.status || []}
        onToggle={(v) => toggle('status', v)}
      />
      <CheckboxGroup
        title="Dokumenttype"
        options={simple(facets?.document_types)}
        selected={filters.document_type || []}
        onToggle={(v) => toggle('document_type', v)}
      />
      <CheckboxGroup
        title="Myndighed"
        options={simple(facets?.authorities)}
        selected={filters.authority || []}
        onToggle={(v) => toggle('authority', v)}
        scroll
      />

      <div className="filter-group">
        <h3>Mindste maritime score</h3>
        <div className="range-row">
          <input
            type="range" min="0" max="100" step="5" value={minScore}
            onChange={(e) => setMinScore(Number(e.target.value))}
            onMouseUp={() => onChange({ ...filters, min_score: minScore || null, page: 1 })}
            onTouchEnd={() => onChange({ ...filters, min_score: minScore || null, page: 1 })}
            onKeyUp={() => onChange({ ...filters, min_score: minScore || null, page: 1 })}
          />
          <strong style={{ fontFamily: 'var(--mono)', minWidth: 28 }}>{minScore}</strong>
        </div>
      </div>

      <div className="filter-group">
        <h3>Publiceret</h3>
        <label className="filter-option" style={{ display: 'block' }}>
          <span style={{ fontSize: 12, color: 'var(--ink-muted)' }}>Fra</span>
          <input
            type="date"
            value={filters.published_from || ''}
            onChange={(e) => onChange({ ...filters, published_from: e.target.value || null, page: 1 })}
          />
        </label>
        <label className="filter-option" style={{ display: 'block', marginTop: 6 }}>
          <span style={{ fontSize: 12, color: 'var(--ink-muted)' }}>Til</span>
          <input
            type="date"
            value={filters.published_to || ''}
            onChange={(e) => onChange({ ...filters, published_to: e.target.value || null, page: 1 })}
          />
        </label>
      </div>

      <button className="filter-reset" onClick={() => { setMinScore(0); onReset() }}>
        Ryd alle filtre
      </button>
    </aside>
  )
}
