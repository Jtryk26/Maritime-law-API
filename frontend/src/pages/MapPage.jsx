import { useEffect, useMemo, useRef, useState } from 'react'
// CSP-varianten af MapLibre: web workeren hentes fra vores eget domæne i
// stedet for en blob-URL, så den globale CSP (security-headers.conf) ikke
// skal lempes for kortsiden.
import maplibregl from 'maplibre-gl/dist/maplibre-gl-csp.js'
import workerUrl from 'maplibre-gl/dist/maplibre-gl-csp-worker.js?url'
import 'maplibre-gl/dist/maplibre-gl.css'
import { api } from '../lib/api.js'
import { ErrorBox, Loading } from '../components/Common.jsx'

/**
 * Kort over geografisk anvendelighed.
 *
 * Viser de kuraterede områder fra /api/geographic-areas/map som lag, der
 * kan slås til og fra pr. områdetype. Et klik på et område viser de
 * dokumenter, der gælder for, undtager eller definerer det; et klik på et
 * dokument fremhæver de områder, dokumentet nævner.
 *
 * Kortet er til uddannelse og overblik — ikke til navigation. Langt de
 * fleste områder er omtrentlige kortmærker ("Approximate area marker"),
 * ikke juridiske grænser, og fundene er automatisk genkendt. Hvert fund
 * viser API'ets `publication_label` ordret: kun en menneskelig godkendelse
 * af den aktuelle tekst hedder "Reviewed geographic applicability".
 * Markørerne har fast størrelse, så de ikke kan læses som en udstrækning.
 *
 * Baggrundskort: uden konfiguration bruges en tom stil med et gradnet, så
 * siden ikke henter noget fra fremmede domæner (se security-headers.conf).
 * VITE_MAP_STYLE_URL kan pege på en MapLibre-stil; domænet skal da også
 * tilføjes CSP'ens connect-src og img-src.
 */

export const AREA_TYPES = [
  { value: 'marpol_special_area', label: 'MARPOL særligt område', color: '#1f5a46' },
  { value: 'eca', label: 'Emissionskontrolområde (ECA)', color: '#b5562b' },
  { value: 'pssa', label: 'Særligt sårbart havområde (PSSA)', color: '#7a3f8f' },
  { value: 'routeing_measure', label: 'Rutesystem', color: '#9a7b4f' },
  { value: 'territorial_waters', label: 'Søterritorium', color: '#2b5f9e' },
  { value: 'sea_area', label: 'Havområde', color: '#3c7c8c' },
  { value: 'strait', label: 'Stræde / bælt', color: '#4a6fa5' },
  { value: 'polar_waters', label: 'Polarfarvande', color: '#5d7a8a' },
  { value: 'state', label: 'Stat (kun omtale)', color: '#66737f' },
]

maplibregl.setWorkerUrl(workerUrl)

const APPLICABILITY_LABELS = {
  APPLIES: 'Gælder',
  EXEMPTS: 'Undtaget',
  DEFINES: 'Definerer',
  HISTORICAL: 'Historisk',
  MENTIONS: 'Nævner',
}

function PublicationLabel({ status, label }) {
  const reviewed = status === 'reviewed' || status === 'reviewed_mention'
  return <span className={`publication-label ${reviewed ? 'is-reviewed' : 'is-detected'}`}>{label}</span>
}

function Designation({ value }) {
  if (!value || Object.keys(value).length === 0) return null
  const parts = []
  if (value.instrument) parts.push(value.instrument)
  if (value.annex !== undefined) parts.push(value.annex ? `Annex ${value.annex}` : 'annex not determined')
  if (value.pollutant !== undefined) parts.push(value.pollutant ? `pollutant: ${value.pollutant}` : 'pollutant not determined')
  return <p className="muted small">Designation: {parts.join(' · ')}</p>
}

function graticule(step = 10) {
  const features = []
  for (let lon = -180; lon <= 180; lon += step) {
    features.push({ type: 'Feature', properties: {}, geometry: { type: 'LineString', coordinates: [[lon, -85], [lon, 85]] } })
  }
  for (let lat = -80; lat <= 80; lat += step) {
    features.push({ type: 'Feature', properties: {}, geometry: { type: 'LineString', coordinates: [[-180, lat], [180, lat]] } })
  }
  return { type: 'FeatureCollection', features }
}

function baseStyle() {
  const url = import.meta.env.VITE_MAP_STYLE_URL
  if (url) return url
  return {
    version: 8,
    sources: { graticule: { type: 'geojson', data: graticule() } },
    layers: [
      { id: 'background', type: 'background', paint: { 'background-color': '#e7ecf1' } },
      { id: 'graticule', type: 'line', source: 'graticule', paint: { 'line-color': '#cbd5df', 'line-width': 0.6 } },
    ],
  }
}

const colorExpression = [
  'match', ['get', 'area_type'],
  ...AREA_TYPES.flatMap((t) => [t.value, t.color]),
  '#66737f',
]

function addAreaLayers(map) {
  map.addSource('areas', { type: 'geojson', data: { type: 'FeatureCollection', features: [] } })
  const isPolygon = ['==', ['geometry-type'], 'Polygon']
  map.addLayer({ id: 'area-fill', type: 'fill', source: 'areas', filter: isPolygon,
    paint: { 'fill-color': colorExpression, 'fill-opacity': 0.18 } })
  map.addLayer({ id: 'area-line', type: 'line', source: 'areas',
    filter: ['!=', ['geometry-type'], 'Point'],
    paint: { 'line-color': colorExpression, 'line-width': 1.5 } })
  map.addLayer({ id: 'area-point', type: 'circle', source: 'areas',
    filter: ['==', ['geometry-type'], 'Point'],
    paint: {
      'circle-color': colorExpression,
      // Fast størrelse: et kortmærke må ikke ligne en udstrækning.
      'circle-radius': 7,
      'circle-stroke-color': '#fffdf8',
      'circle-stroke-width': 1.5,
      'circle-opacity': 0.85,
    } })
  map.addLayer({ id: 'area-highlight', type: 'circle', source: 'areas',
    filter: ['in', ['get', 'slug'], ['literal', []]],
    paint: { 'circle-radius': 18, 'circle-color': 'rgba(0,0,0,0)', 'circle-stroke-color': '#102a43', 'circle-stroke-width': 3 } })
  map.addLayer({ id: 'area-highlight-line', type: 'line', source: 'areas',
    filter: ['in', ['get', 'slug'], ['literal', []]],
    paint: { 'line-color': '#102a43', 'line-width': 3 } })
}

export default function MapPage({ query = {} }) {
  const container = useRef(null)
  const mapRef = useRef(null)
  const [data, setData] = useState(null)
  const [error, setError] = useState(null)
  const [enabled, setEnabled] = useState(() => new Set(AREA_TYPES.filter((t) => t.value !== 'state').map((t) => t.value)))
  const [candidates, setCandidates] = useState([])
  const [area, setArea] = useState(null)
  const [documentId, setDocumentId] = useState(query.dokument ? Number(query.dokument) : null)

  useEffect(() => {
    api.geographyMap().then(setData).catch(setError)
  }, [])

  // Kortet oprettes én gang.
  useEffect(() => {
    if (!container.current) return undefined
    const map = new maplibregl.Map({
      container: container.current,
      style: baseStyle(),
      center: [10.5, 56.5],
      zoom: 3.6,
      attributionControl: { compact: true, customAttribution: 'Ikke til navigation' },
    })
    map.addControl(new maplibregl.NavigationControl({ showCompass: false }), 'top-right')
    map.on('load', () => {
      addAreaLayers(map)
      map.__ready = true
      map.fire('areas-ready')
    })
    const onClick = (event) => {
      const features = map.queryRenderedFeatures(event.point, { layers: ['area-point', 'area-fill', 'area-line'] })
      const unique = [...new Map(features.map((f) => [f.properties.slug, f.properties])).values()]
      setCandidates(unique)
      if (unique.length === 1) setArea(unique[0].slug)
    }
    map.on('click', onClick)
    for (const layer of ['area-point', 'area-fill']) {
      map.on('mouseenter', layer, () => { map.getCanvas().style.cursor = 'pointer' })
      map.on('mouseleave', layer, () => { map.getCanvas().style.cursor = '' })
    }
    mapRef.current = map
    return () => map.remove()
  }, [])

  // Data og lagfilter.
  useEffect(() => {
    const map = mapRef.current
    if (!map || !data) return undefined
    const apply = () => {
      const features = data.features.filter((f) => enabled.has(f.properties.area_type))
      map.getSource('areas')?.setData({ type: 'FeatureCollection', features })
    }
    if (map.__ready) apply()
    else map.once('areas-ready', apply)
    return undefined
  }, [data, enabled])

  return (
    <div className="map-page">
      <header className="map-header">
        <h1>Kort over geografisk anvendelighed</h1>
        <p className="map-disclaimer" role="note">
          <strong>Til uddannelse og overblik — ikke til navigation.</strong>{' '}
          Prikkerne er <em>Approximate area markers</em> — ikke juridiske grænser — og
          viser ikke, om en position eller rute ligger i et område. Flere juridisk
          forskellige områder (fx Østersøen, dens SOx-ECA og NOx-ECA) kan dele samme
          markør. Hvilke regler der gælder hvor, er genkendt automatisk og er ikke en
          juridisk vurdering, medmindre der står "Reviewed". Kontrollér altid den
          officielle tekst.
        </p>
      </header>

      {error && <ErrorBox error={error} />}

      <div className="map-layout">
        <aside className="map-layers" aria-label="Kortlag">
          <h2>Lag</h2>
          {AREA_TYPES.map((t) => (
            <label key={t.value} className="map-layer-toggle">
              <input
                type="checkbox"
                checked={enabled.has(t.value)}
                onChange={() => setEnabled((prev) => {
                  const next = new Set(prev)
                  if (next.has(t.value)) next.delete(t.value)
                  else next.add(t.value)
                  return next
                })}
              />
              <span className="swatch" style={{ background: t.color }} aria-hidden="true" />
              {t.label}
            </label>
          ))}
          {data?.unmapped?.length > 0 && (
            <>
              <h2>Uden kortgeometri</h2>
              {data.unmapped.map((a) => (
                <button key={a.slug} type="button" className="link-button" onClick={() => setArea(a.slug)}>
                  {a.name_da || a.name} ({a.reviewed_document_count} gennemgået)
                </button>
              ))}
            </>
          )}
        </aside>

        <div className="map-canvas-wrap">
          {!data && !error && <Loading label="Henter kortdata…" />}
          <div ref={container} className="map-canvas" />
          {candidates.length > 1 && (
            <div className="map-candidates" role="dialog" aria-label="Områder her">
              <strong>Flere områder her:</strong>
              {candidates.map((c) => (
                <button key={c.slug} type="button" className="link-button" onClick={() => setArea(c.slug)}>
                  {c.name} ({c.reviewed_document_count} gennemgået)
                </button>
              ))}
            </div>
          )}
        </div>

        <aside className="map-details">
          {documentId
            ? <DocumentAreas documentId={documentId} map={mapRef} onArea={setArea} onClose={() => setDocumentId(null)} />
            : null}
          {area
            ? <AreaDocuments slug={area} onDocument={setDocumentId} />
            : !documentId && <p className="muted">Klik på et område for at se, hvilke regler der gælder dér.</p>}
        </aside>
      </div>
    </div>
  )
}

function PreviousReview({ value }) {
  if (!value) return null
  return (
    <p className="muted small map-previous">
      Tidligere {value.review_status === 'rejected' ? 'afvist' : 'gennemgået'} af {value.reviewed_by || 'ukendt'}
      {value.reviewed_at && <> ({value.reviewed_at.slice(0, 10)})</>}
      {value.excerpt_unchanged ? ' — uddraget er uændret, men teksten eller konteksten er ændret.' : ' — uddraget er ændret.'}
      {' '}{value.note}
    </p>
  )
}

function ItemList({ items, onDocument }) {
  return (
    <ul className="map-doc-list">
      {items.map((item) => (
        <li key={item.document_id}>
          <PublicationLabel status={item.publication_status} label={item.publication_label} />
          <div>
            <span className={`applicability-tag applicability-${item.applicability.toLowerCase()}`}>
              {APPLICABILITY_LABELS[item.applicability]}
            </span>{' '}
            <a href={item.document_path}>{item.title}</a>
          </div>
          <div className="muted small">
            {item.status && <>{item.status} · </>}
            {item.effective_date && <>i kraft {item.effective_date} · </>}
            {item.detection_method}
            {item.result_kind === 'automatic_detection' && <> · {Math.round(item.confidence * 100)} % regelbaseret sikkerhed</>}
            {item.vessel_relevance.length > 0 && <> · {item.vessel_relevance.join(', ')}</>}
          </div>
          {item.source_excerpt && <blockquote className="map-context">{item.source_excerpt}</blockquote>}
          <PreviousReview value={item.previous_review} />
          <button type="button" className="link-button small" onClick={() => onDocument(item.document_id)}>
            Vis dokumentets områder på kortet
          </button>
        </li>
      ))}
    </ul>
  )
}

function AreaDocuments({ slug, onDocument }) {
  const [detail, setDetail] = useState(null)
  const [reviewed, setReviewed] = useState(null)
  const [detections, setDetections] = useState(null)
  const [showDetections, setShowDetections] = useState(false)
  const [withMentions, setWithMentions] = useState(false)
  const [error, setError] = useState(null)

  useEffect(() => {
    setDetail(null); setError(null); setShowDetections(false)
    api.geographicArea(slug).then(setDetail).catch(setError)
  }, [slug])

  const applicability = withMentions ? Object.keys(APPLICABILITY_LABELS) : undefined

  useEffect(() => {
    setReviewed(null)
    api.geographicAreaDocuments(slug, { applicability, page_size: 50 }).then(setReviewed).catch(setError)
  }, [slug, withMentions])

  useEffect(() => {
    setDetections(null)
    if (!showDetections) return
    api.geographicAreaDetections(slug, { applicability, page_size: 50 }).then(setDetections).catch(setError)
  }, [slug, withMentions, showDetections])

  if (error) return <ErrorBox error={error} />
  if (!detail) return <Loading />

  return (
    <section aria-labelledby="area-heading">
      <h2 id="area-heading">{detail.name_da || detail.name}</h2>
      <p className="muted">
        {AREA_TYPES.find((t) => t.value === detail.area_type)?.label || detail.area_type}
        {detail.metadata_verified && detail.legal_basis && <> · {detail.legal_basis}</>}
      </p>
      <Designation value={detail.designation} />
      <p className="muted small">Kortvisning: {detail.geometry_label}</p>
      {detail.provisional && (
        <details className="map-warning">
          <summary>{detail.provisional.status_note}</summary>
          {detail.provisional.legal_basis && <p>Provisional legal basis: {detail.provisional.legal_basis}</p>}
          {detail.provisional.effective_from && <p>Provisional effective from: {detail.provisional.effective_from}</p>}
        </details>
      )}
      {detail.reference_url && (
        <p><a href={detail.reference_url} target="_blank" rel="noreferrer">Reference ↗</a></p>
      )}
      <label className="map-layer-toggle">
        <input type="checkbox" checked={withMentions} onChange={(e) => setWithMentions(e.target.checked)} />
        Medtag dokumenter, der kun nævner området
      </label>

      <h3>Gennemgået geografisk anvendelighed</h3>
      <p className="muted small">{reviewed?.notice}</p>
      {!reviewed && <Loading />}
      {reviewed?.items.length === 0 && <p className="muted">Ingen gennemgåede dokumenter for området endnu.</p>}
      {reviewed && <ItemList items={reviewed.items} onDocument={onDocument} />}

      <section className="map-detections" aria-labelledby="detections-heading">
        <h3 id="detections-heading">Automatiske fund — ikke gennemgået</h3>
        <label className="map-layer-toggle">
          <input type="checkbox" checked={showDetections} onChange={(e) => setShowDetections(e.target.checked)} />
          Vis automatiske fund ({detail.detected_document_count} dokumenter)
        </label>
        {showDetections && (
          <>
            <p className="map-warning">{detections?.notice || 'Henter…'}</p>
            {detections?.items.length === 0 && <p className="muted">Ingen automatiske fund.</p>}
            {detections && <ItemList items={detections.items} onDocument={onDocument} />}
          </>
        )}
      </section>
    </section>
  )
}

function AreaRow({ a, onArea }) {
  return (
    <li key={a.area.slug}>
      <span className={`applicability-tag applicability-${a.applicability.toLowerCase()}`}>
        {APPLICABILITY_LABELS[a.applicability]}
      </span>{' '}
      <button type="button" className="link-button" onClick={() => onArea(a.area.slug)}>
        {a.area.name_da || a.area.name}
      </button>
      <div><PublicationLabel status={a.publication_status} label={a.publication_label} /></div>
    </li>
  )
}

function DocumentAreas({ documentId, map, onArea, onClose }) {
  const [geo, setGeo] = useState(null)
  const [detections, setDetections] = useState(null)
  const [showDetections, setShowDetections] = useState(false)
  const [error, setError] = useState(null)

  useEffect(() => {
    setGeo(null); setDetections(null); setShowDetections(false); setError(null)
    api.documentGeography(documentId).then(setGeo).catch(setError)
  }, [documentId])

  useEffect(() => {
    if (!showDetections) return
    api.documentGeographyDetections(documentId).then(setDetections).catch(setError)
  }, [documentId, showDetections])

  // Kun gennemgåede områder fremhæves, medmindre de automatiske fund er slået til.
  const slugs = useMemo(() => {
    const reviewed = geo ? geo.areas.map((a) => a.area.slug) : []
    const detected = showDetections && detections ? detections.areas.map((a) => a.area.slug) : []
    return [...reviewed, ...detected]
  }, [geo, detections, showDetections])

  useEffect(() => {
    const instance = map.current
    if (!instance) return undefined
    const apply = () => {
      const filter = ['in', ['get', 'slug'], ['literal', slugs]]
      instance.setFilter('area-highlight', ['all', ['==', ['geometry-type'], 'Point'], filter])
      instance.setFilter('area-highlight-line', ['all', ['!=', ['geometry-type'], 'Point'], filter])
    }
    if (instance.__ready) apply()
    else instance.once('areas-ready', apply)
    return () => {
      if (instance.__ready && instance.getLayer('area-highlight')) {
        instance.setFilter('area-highlight', ['in', ['get', 'slug'], ['literal', []]])
        instance.setFilter('area-highlight-line', ['in', ['get', 'slug'], ['literal', []]])
      }
    }
  }, [slugs, map])

  if (error) return <ErrorBox error={error} />
  if (!geo) return <Loading />

  return (
    <section className="map-document" aria-labelledby="doc-geo-heading">
      <h2 id="doc-geo-heading"><a href={`#/dokument/${geo.document_id}`}>{geo.title}</a></h2>
      <button type="button" className="link-button small" onClick={onClose}>Luk</button>
      <h3>Gennemgået geografisk anvendelighed</h3>
      {geo.areas.length === 0 && <p className="muted">Ingen gennemgåede områder.</p>}
      <ul className="map-doc-list">
        {geo.areas.map((a) => <AreaRow key={a.area.slug} a={a} onArea={onArea} />)}
      </ul>
      {geo.review_history.length > 0 && (
        <details className="muted small">
          <summary>Historik: {geo.review_history.length} gennemgang(e) af en tidligere tekst — gælder ikke</summary>
          <ul>
            {geo.review_history.map((m) => (
              <li key={m.id}>{m.publication_label}: {m.match_text} ({m.review_status}, {m.reviewed_by})</li>
            ))}
          </ul>
        </details>
      )}
      <section className="map-detections">
        <label className="map-layer-toggle">
          <input type="checkbox" checked={showDetections} onChange={(e) => setShowDetections(e.target.checked)} />
          Vis automatiske fund — ikke gennemgået ({geo.detected_area_count} områder)
        </label>
        {showDetections && detections && (
          <>
            <p className="map-warning">{detections.notice}</p>
            <ul className="map-doc-list">
              {detections.areas.map((a) => <AreaRow key={a.area.slug} a={a} onArea={onArea} />)}
            </ul>
          </>
        )}
      </section>
    </section>
  )
}
