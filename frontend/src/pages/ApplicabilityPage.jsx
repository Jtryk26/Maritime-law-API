/**
 * Skibsvurdering & Regelanvendelighed (Vessel Applicability Evaluation).
 *
 * Algoritmisk vurdering af hvilke maritime regler og bekendtgørelser,
 * der matcher et konkret skib ud fra fartøjstype, dimensioner,
 * operationsmønster, flagstat og skibsregister.
 *
 * BEMÆRK: Vurderingen er vejledende og udgør ikke en bindende
 * myndighedsafgørelse fra Søfartsstyrelsen. Hvert svar bygger på
 * verificerede regeluddrag fra Retsinformation med ordrette lovcitater.
 */

import { useCallback, useEffect, useMemo, useState } from 'react'
import { api } from '../lib/api.js'
import { displayTitle, formatDate } from '../lib/format.js'
import { Disclosure, Empty, ErrorBox, LegalNotice, Loading } from '../components/Common.jsx'

// Standard fartøjsprofiler til hurtig afprøvning
const PRESET_PROFILES = [
  {
    label: 'M/F Passagerfærge (3.200 BT)',
    description: 'Ro-ro passagerskib på international fart med 420 passagerer (Dansk flag, DAS).',
    profile: {
      profile_id: 'preset-ferry',
      vessel_name: 'M/F Kattegat Express',
      vessel_type: 'ro_ro_passenger_ship',
      operation_types: ['international_voyage'],
      dimensions: {
        gross_tonnage: { value: 3200, source: 'certificate' },
        length_overall_m: { value: 87.5, source: 'certificate' },
      },
      persons: {
        passenger_count: { value: 420, source: 'certificate' },
        crew_count: { value: 24, source: 'certificate' },
      },
      jurisdiction: {
        flag_state: 'DK',
        ship_registry: 'DAS',
        operating_areas: ['DK_TERRITORIAL', 'EU'],
      },
      lifecycle: {
        keel_laid_date: '2016-04-15',
      },
      cargo: {
        carries_dangerous_goods: false,
      },
    },
  },
  {
    label: 'Kemikalietanker (12.500 DWT)',
    description: 'Tankskib til kemikalier og olieprodukter i international fart (Dansk flag, DIS).',
    profile: {
      profile_id: 'preset-tanker',
      vessel_name: 'M/T Dania Chemist',
      vessel_type: 'chemical_tanker',
      additional_vessel_types: ['oil_tanker'],
      operation_types: ['international_voyage'],
      dimensions: {
        gross_tonnage: { value: 8500, source: 'certificate' },
        deadweight_tonnes: { value: 12500, source: 'certificate' },
        length_overall_m: { value: 134.0, source: 'certificate' },
      },
      persons: {
        passenger_count: { value: 0, source: 'certificate' },
        crew_count: { value: 16, source: 'certificate' },
      },
      jurisdiction: {
        flag_state: 'DK',
        ship_registry: 'DIS',
        operating_areas: ['WORLDWIDE'],
      },
      lifecycle: {
        keel_laid_date: '2018-09-01',
      },
      cargo: {
        cargo_types: ['chemicals', 'oil'],
        carries_dangerous_goods: true,
      },
    },
  },
  {
    label: 'Kystfiskefartøj (14,99 m / 24 BT)',
    description: 'Mindre erhvervsfiskekutter i Nordsøen og indre danske farvande (Dansk flag, DAS).',
    profile: {
      profile_id: 'preset-fishing',
      vessel_name: 'HG 120 Havstrygeren',
      vessel_type: 'fishing_vessel',
      operation_types: ['fishing_operation', 'near_coastal'],
      dimensions: {
        length_overall_m: { value: 14.99, source: 'certificate' },
        gross_tonnage: { value: 24, source: 'certificate' },
      },
      persons: {
        passenger_count: { value: 0, source: 'declared' },
        crew_count: { value: 3, source: 'declared' },
      },
      jurisdiction: {
        flag_state: 'DK',
        ship_registry: 'DAS',
        operating_areas: ['DK_TERRITORIAL', 'NORTH_SEA'],
      },
      lifecycle: {
        keel_laid_date: '2012-05-10',
      },
      cargo: {
        carries_dangerous_goods: false,
      },
    },
  },
  {
    label: 'ROV & Dykker Supportskib (4.200 BT)',
    description: 'Specialfartøj til offshore konstruktion, dykker- og undervandsrobotopgaver (Dansk flag, DIS).',
    profile: {
      profile_id: 'preset-rov',
      vessel_name: 'Njord Subsea Surveyor',
      vessel_type: 'rov_support_vessel',
      additional_vessel_types: ['offshore_support_vessel', 'dive_support_vessel'],
      operation_types: ['offshore_construction', 'rov_operation', 'dive_operation', 'wind_farm_service'],
      dimensions: {
        gross_tonnage: { value: 4200, source: 'certificate' },
        length_overall_m: { value: 92.4, source: 'certificate' },
      },
      persons: {
        passenger_count: { value: 0, source: 'certificate' },
        industrial_personnel: { value: 45, source: 'certificate' },
        crew_count: { value: 18, source: 'certificate' },
      },
      jurisdiction: {
        flag_state: 'DK',
        ship_registry: 'DIS',
        operating_areas: ['NORTH_SEA', 'EU'],
      },
      lifecycle: {
        keel_laid_date: '2021-11-20',
      },
      cargo: {
        carries_dangerous_goods: false,
      },
    },
  },
  {
    label: 'Containerfeeder (1.000 TEU)',
    description: 'Generelt tørlast- og containerskib til europæisk feederfart (Dansk flag, DIS).',
    profile: {
      profile_id: 'preset-container',
      vessel_name: 'Baltic Feeder III',
      vessel_type: 'container_ship',
      additional_vessel_types: ['general_cargo_ship'],
      operation_types: ['international_voyage'],
      dimensions: {
        gross_tonnage: { value: 9800, source: 'certificate' },
        deadweight_tonnes: { value: 12200, source: 'certificate' },
        length_overall_m: { value: 142.5, source: 'certificate' },
      },
      persons: {
        passenger_count: { value: 0, source: 'certificate' },
        crew_count: { value: 14, source: 'certificate' },
      },
      jurisdiction: {
        flag_state: 'DK',
        ship_registry: 'DIS',
        operating_areas: ['BALTIC_SEA', 'NORTH_SEA', 'EU'],
      },
      lifecycle: {
        keel_laid_date: '2019-02-14',
      },
      cargo: {
        cargo_types: ['containers', 'general_cargo'],
        carries_dangerous_goods: true,
      },
    },
  },
  {
    label: 'Fritidsfartøj / Mindre motorbåd (8,5 m)',
    description: 'Privat motorbåd til kystsejlads i danske farvande.',
    profile: {
      profile_id: 'preset-pleasure',
      vessel_name: 'M/B Sommerbris',
      vessel_type: 'pleasure_craft',
      operation_types: ['domestic_voyage', 'near_coastal'],
      dimensions: {
        length_overall_m: { value: 8.5, source: 'declared' },
      },
      persons: {
        passenger_count: { value: 6, source: 'declared' },
      },
      jurisdiction: {
        flag_state: 'DK',
        ship_registry: 'DAS',
        operating_areas: ['DK_TERRITORIAL'],
      },
      lifecycle: {
        keel_laid_date: '2020-06-01',
      },
      cargo: {
        carries_dangerous_goods: false,
      },
    },
  },
]

const VESSEL_TYPE_GROUPS = [
  {
    label: 'Passagerskibe',
    options: [
      { value: 'passenger_ship', label: 'Passagerskib (almindeligt)' },
      { value: 'ro_ro_passenger_ship', label: 'Ro-ro passagerskib (færge)' },
      { value: 'high_speed_passenger_craft', label: 'Hurtiggående passagerfartøj (HSC)' },
    ],
  },
  {
    label: 'Tankskibe',
    options: [
      { value: 'oil_tanker', label: 'Olietankskib' },
      { value: 'chemical_tanker', label: 'Kemikalietankskib' },
      { value: 'gas_carrier', label: 'Gastankskib (LPG/LNG)' },
    ],
  },
  {
    label: 'Tørlast & Containerskibe',
    options: [
      { value: 'container_ship', label: 'Containerskib' },
      { value: 'general_cargo_ship', label: 'Generelt fragtskib / tørlast' },
      { value: 'bulk_carrier', label: 'Bulkskib (massegods)' },
      { value: 'ro_ro_cargo_ship', label: 'Ro-ro fragtskib' },
    ],
  },
  {
    label: 'Fiskeri',
    options: [
      { value: 'fishing_vessel', label: 'Fiskefartøj / erhvervsfiskeri' },
    ],
  },
  {
    label: 'Specialfartøjer & Offshore',
    options: [
      { value: 'offshore_support_vessel', label: 'Offshore forsyningsfartøj (OSV)' },
      { value: 'rov_support_vessel', label: 'ROV Supportskib (undervandsrobot)' },
      { value: 'dive_support_vessel', label: 'Dykkerstøtteskib' },
      { value: 'cable_layer', label: 'Kabelskib' },
      { value: 'tug', label: 'Bugserbåd / slæbebåd' },
      { value: 'dredger', label: 'Uddybningsfartøj / sandsuger' },
      { value: 'training_vessel', label: 'Skoleskib' },
    ],
  },
  {
    label: 'Fritid & Øvrige',
    options: [
      { value: 'pleasure_craft', label: 'Fritidsfartøj / lystbåd' },
      { value: 'other', label: 'Anden fartøjstype' },
    ],
  },
]

const OPERATION_OPTIONS = [
  { value: 'international_voyage', label: 'International fart' },
  { value: 'domestic_voyage', label: 'Indenrigs fart' },
  { value: 'near_coastal', label: 'Nærfart / kystsejlads' },
  { value: 'harbour_service', label: 'Havnearbejde' },
  { value: 'fishing_operation', label: 'Fiskeriaktivitet' },
  { value: 'offshore_construction', label: 'Offshore anlægsarbejde' },
  { value: 'rov_operation', label: 'ROV-operation' },
  { value: 'dive_operation', label: 'Dykkeroperation' },
  { value: 'wind_farm_service', label: 'Vindmølleservice (CTV/SOV)' },
  { value: 'standby_rescue', label: 'Standby / redningsberedskab' },
  { value: 'towage', label: 'Bugsering / slæbning' },
  { value: 'inland_waterway', label: 'Indre vandveje / søer' },
  { value: 'laid_up', label: 'Oplagt / ude af drift' },
]

const VALUE_SOURCES = [
  { value: 'certificate', label: 'Målebrev / Certifikat (officielt)' },
  { value: 'registry', label: 'Skibsregister' },
  { value: 'declared', label: 'Erklæret af reder / ejer' },
  { value: 'estimated', label: 'Skønnet / foreløbigt estimat' },
]

const VERDICT_CONFIG = {
  APPLIES: {
    label: 'Vurdering: Gælder',
    badgeClass: 'verdict-applies',
    description: 'Reglens betingelser og grænseværdier er opfyldt for det angivne skib.',
  },
  POSSIBLY_APPLIES: {
    label: 'Vurdering: Gælder sandsynligvis',
    badgeClass: 'verdict-possibly',
    description: 'Reglen matcher overordnet, men visse data hviler på skøn.',
  },
  NEEDS_MANUAL_REVIEW: {
    label: 'Kræver faglig vurdering',
    badgeClass: 'verdict-review',
    description: 'Afgørende tekniske data mangler, eller reglen forudsætter juridisk skøn.',
  },
  DOES_NOT_APPLY: {
    label: 'Vurdering: Gælder ikke',
    badgeClass: 'verdict-not-applies',
    description: 'Skibet falder uden for reglens anvendelsesområde eller er udtrykkeligt undtaget.',
  },
}

function initialProfile() {
  return {
    profile_id: 'profil-1',
    vessel_name: '',
    vessel_type: 'ro_ro_passenger_ship',
    additional_vessel_types: [],
    operation_types: ['international_voyage'],
    dimensions: {
      gross_tonnage: { value: 3200, source: 'certificate' },
      length_overall_m: { value: 85, source: 'certificate' },
      deadweight_tonnes: null,
      dimensionstal: null,
    },
    persons: {
      passenger_count: { value: 250, source: 'certificate' },
      crew_count: { value: 20, source: 'certificate' },
      industrial_personnel: null,
    },
    jurisdiction: {
      flag_state: 'DK',
      ship_registry: 'DAS',
      operating_areas: ['DK_TERRITORIAL', 'EU'],
      port_states: [],
    },
    lifecycle: {
      keel_laid_date: '2015-06-01',
    },
    cargo: {
      cargo_types: [],
      carries_dangerous_goods: false,
    },
  }
}

export default function ApplicabilityPage({ ruleId }) {
  const [profile, setProfile] = useState(initialProfile)
  const [statusMode, setStatusMode] = useState('current')
  const [treatEstimatedAsUnknown, setTreatEstimatedAsUnknown] = useState(false)
  const [includeNonApplicable, setIncludeNonApplicable] = useState(true)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState(null)
  const [response, setResponse] = useState(null)
  const [activeVerdictTab, setActiveVerdictTab] = useState('ALL')
  const [inspectedRule, setInspectedRule] = useState(null)

  useEffect(() => {
    if (ruleId) {
      api.applicabilityRule(ruleId)
        .then(setInspectedRule)
        .catch((err) => console.error('Kunne ikke hente regel:', err))
    }
  }, [ruleId])

  const runEvaluation = useCallback(async (currentProfile = profile) => {
    setLoading(true)
    setError(null)

    const cleanPayload = {
      profile: {
        profile_id: currentProfile.profile_id || 'profil-1',
        vessel_name: currentProfile.vessel_name || null,
        vessel_type: currentProfile.vessel_type,
        additional_vessel_types: currentProfile.additional_vessel_types || [],
        operation_types: currentProfile.operation_types || [],
        dimensions: {
          gross_tonnage: currentProfile.dimensions?.gross_tonnage?.value != null
            ? currentProfile.dimensions.gross_tonnage : undefined,
          length_overall_m: currentProfile.dimensions?.length_overall_m?.value != null
            ? currentProfile.dimensions.length_overall_m : undefined,
          deadweight_tonnes: currentProfile.dimensions?.deadweight_tonnes?.value != null
            ? currentProfile.dimensions.deadweight_tonnes : undefined,
          dimensionstal: currentProfile.dimensions?.dimensionstal?.value != null
            ? currentProfile.dimensions.dimensionstal : undefined,
        },
        persons: {
          passenger_count: currentProfile.persons?.passenger_count?.value != null
            ? currentProfile.persons.passenger_count : undefined,
          crew_count: currentProfile.persons?.crew_count?.value != null
            ? currentProfile.persons.crew_count : undefined,
          industrial_personnel: currentProfile.persons?.industrial_personnel?.value != null
            ? currentProfile.persons.industrial_personnel : undefined,
        },
        jurisdiction: {
          flag_state: currentProfile.jurisdiction?.flag_state || null,
          ship_registry: currentProfile.jurisdiction?.ship_registry || null,
          operating_areas: currentProfile.jurisdiction?.operating_areas || [],
          port_states: currentProfile.jurisdiction?.port_states || [],
        },
        lifecycle: {
          keel_laid_date: currentProfile.lifecycle?.keel_laid_date || null,
        },
        cargo: {
          cargo_types: currentProfile.cargo?.cargo_types || [],
          carries_dangerous_goods: currentProfile.cargo?.carries_dangerous_goods,
        },
      },
      status_mode: statusMode,
      treat_estimated_as_unknown: treatEstimatedAsUnknown,
      include_non_applicable: includeNonApplicable,
      with_supporting_text: true,
    }

    try {
      const data = await api.evaluateApplicability(cleanPayload)
      setResponse(data)
    } catch (err) {
      setError(err)
    } finally {
      setLoading(false)
    }
  }, [profile, statusMode, treatEstimatedAsUnknown, includeNonApplicable])

  useEffect(() => {
    runEvaluation()
  }, [])

  const applyPreset = (preset) => {
    setProfile(preset.profile)
    runEvaluation(preset.profile)
  }

  const handleMeasuredChange = (section, field, value, source = 'certificate') => {
    setProfile((prev) => ({
      ...prev,
      [section]: {
        ...prev[section],
        [field]: value !== '' && value != null ? { value: Number(value), source } : null,
      },
    }))
  }

  const toggleOperation = (op) => {
    setProfile((prev) => {
      const current = prev.operation_types || []
      const next = current.includes(op)
        ? current.filter((x) => x !== op)
        : [...current, op]
      return { ...prev, operation_types: next }
    })
  }

  const filteredResults = useMemo(() => {
    if (!response?.results) return []
    if (activeVerdictTab === 'ALL') return response.results
    return response.results.filter((r) => r.verdict === activeVerdictTab)
  }, [response, activeVerdictTab])

  return (
    <div className="applicability-page">
      <header className="page-header">
        <div className="inner">
          <div className="header-breadcrumbs">
            <a href="#/">Forside</a>
            <span className="sep">/</span>
            <span>Skibsvurdering</span>
          </div>
          <h1>Vurdering af regelanvendelighed</h1>
          <p className="lead">
            Vejledende algoritmisk vurdering af hvilke danske søfartslove og tekniske forskrifter,
            der gælder for et konkret skib ud fra type, dimensioner, flag og register.
          </p>
        </div>
      </header>

      <div className="app-container applicability-layout">
        {/* Venstre kolonne: Fartøjsprofil Formular */}
        <aside className="applicability-sidebar">
          <div className="panel profile-builder-panel">
            <div className="panel-header">
              <h2>Fartøjsprofil</h2>
              <span className="panel-subtitle">Indtast tekniske skibsdata</span>
            </div>

            {/* Hurtigvalg af templates */}
            <div className="preset-selector">
              <label htmlFor="preset-select" className="field-label">Vælg referencefartøj:</label>
              <select
                id="preset-select"
                className="select-input"
                onChange={(e) => {
                  const p = PRESET_PROFILES[Number(e.target.value)]
                  if (p) applyPreset(p)
                }}
                defaultValue="0"
              >
                {PRESET_PROFILES.map((p, idx) => (
                  <option key={idx} value={idx}>{p.label}</option>
                ))}
              </select>
            </div>

            <form onSubmit={(e) => { e.preventDefault(); runEvaluation() }} className="profile-form">
              <div className="form-group">
                <label className="field-label" htmlFor="vessel_name">Skibsnavn / Kendingssignal</label>
                <input
                  id="vessel_name"
                  type="text"
                  className="text-input"
                  placeholder="fx M/F Dania Express"
                  value={profile.vessel_name || ''}
                  onChange={(e) => setProfile({ ...profile, vessel_name: e.target.value })}
                />
              </div>

              <div className="form-group">
                <label className="field-label" htmlFor="vessel_type">Primær fartøjstype *</label>
                <select
                  id="vessel_type"
                  className="select-input"
                  value={profile.vessel_type}
                  onChange={(e) => setProfile({ ...profile, vessel_type: e.target.value })}
                >
                  {VESSEL_TYPE_GROUPS.map((group) => (
                    <optgroup key={group.label} label={group.label}>
                      {group.options.map((opt) => (
                        <option key={opt.value} value={opt.value}>{opt.label}</option>
                      ))}
                    </optgroup>
                  ))}
                </select>
              </div>

              <fieldset className="form-section">
                <legend>Dimensioner & Tonnage</legend>

                <div className="form-row">
                  <div className="form-group col">
                    <label className="field-label" htmlFor="gross_tonnage">Bruttotonnage (BT)</label>
                    <input
                      id="gross_tonnage"
                      type="number"
                      min="0"
                      step="any"
                      className="text-input"
                      placeholder="fx 3200"
                      value={profile.dimensions?.gross_tonnage?.value ?? ''}
                      onChange={(e) => handleMeasuredChange('dimensions', 'gross_tonnage', e.target.value, profile.dimensions?.gross_tonnage?.source || 'certificate')}
                    />
                  </div>
                  <div className="form-group col-source">
                    <label className="field-label" htmlFor="gt_source">Kilde</label>
                    <select
                      id="gt_source"
                      className="select-input-sm"
                      value={profile.dimensions?.gross_tonnage?.source || 'certificate'}
                      onChange={(e) => handleMeasuredChange('dimensions', 'gross_tonnage', profile.dimensions?.gross_tonnage?.value, e.target.value)}
                    >
                      {VALUE_SOURCES.map((s) => (
                        <option key={s.value} value={s.value}>{s.label}</option>
                      ))}
                    </select>
                  </div>
                </div>

                <div className="form-row">
                  <div className="form-group col">
                    <label className="field-label" htmlFor="length_oa">Længde overalt (L_oa i meter)</label>
                    <input
                      id="length_oa"
                      type="number"
                      min="0"
                      step="0.1"
                      className="text-input"
                      placeholder="fx 85.0"
                      value={profile.dimensions?.length_overall_m?.value ?? ''}
                      onChange={(e) => handleMeasuredChange('dimensions', 'length_overall_m', e.target.value, profile.dimensions?.length_overall_m?.source || 'certificate')}
                    />
                  </div>
                  <div className="form-group col">
                    <label className="field-label" htmlFor="dwt">Dødvægt (DWT i tons)</label>
                    <input
                      id="dwt"
                      type="number"
                      min="0"
                      step="any"
                      className="text-input"
                      placeholder="fx 12000"
                      value={profile.dimensions?.deadweight_tonnes?.value ?? ''}
                      onChange={(e) => handleMeasuredChange('dimensions', 'deadweight_tonnes', e.target.value, 'certificate')}
                    />
                  </div>
                </div>
              </fieldset>

              <fieldset className="form-section">
                <legend>Personer om bord</legend>
                <div className="form-row">
                  <div className="form-group col">
                    <label className="field-label" htmlFor="passenger_count">Maks. Passagertal</label>
                    <input
                      id="passenger_count"
                      type="number"
                      min="0"
                      className="text-input"
                      placeholder="fx 250"
                      value={profile.persons?.passenger_count?.value ?? ''}
                      onChange={(e) => handleMeasuredChange('persons', 'passenger_count', e.target.value, 'certificate')}
                    />
                  </div>
                  <div className="form-group col">
                    <label className="field-label" htmlFor="crew_count">Besætning</label>
                    <input
                      id="crew_count"
                      type="number"
                      min="0"
                      className="text-input"
                      placeholder="fx 18"
                      value={profile.persons?.crew_count?.value ?? ''}
                      onChange={(e) => handleMeasuredChange('persons', 'crew_count', e.target.value, 'certificate')}
                    />
                  </div>
                </div>
              </fieldset>

              <fieldset className="form-section">
                <legend>Jurisdiktion, Flag & Register</legend>

                {/* Flagstat og Skibsregister er adskilte juridiske begreber */}
                <div className="form-row">
                  <div className="form-group col">
                    <label className="field-label" htmlFor="flag_state">Flagstat</label>
                    <select
                      id="flag_state"
                      className="select-input"
                      value={profile.jurisdiction?.flag_state || 'DK'}
                      onChange={(e) => setProfile({
                        ...profile,
                        jurisdiction: { ...profile.jurisdiction, flag_state: e.target.value },
                      })}
                    >
                      <option value="DK">Danmark (DK flag)</option>
                      <option value="FO">Færøerne (FO flag)</option>
                      <option value="EU">Andet EU/EØS-flag</option>
                      <option value="OTHER">Tredjelandsflag</option>
                    </select>
                  </div>
                  <div className="form-group col">
                    <label className="field-label" htmlFor="ship_registry">Skibsregister</label>
                    <select
                      id="ship_registry"
                      className="select-input"
                      value={profile.jurisdiction?.ship_registry || 'DAS'}
                      onChange={(e) => setProfile({
                        ...profile,
                        jurisdiction: { ...profile.jurisdiction, ship_registry: e.target.value },
                      })}
                    >
                      <option value="DAS">DAS (Almindeligt register)</option>
                      <option value="DIS">DIS (Internationalt register)</option>
                      <option value="FAS">FAS (Færøsk register)</option>
                      <option value="FOREIGN">Udenlandsk register</option>
                    </select>
                  </div>
                </div>

                <div className="form-row">
                  <div className="form-group col">
                    <label className="field-label" htmlFor="keel_date">Køllægningsdato</label>
                    <input
                      id="keel_date"
                      type="date"
                      className="text-input"
                      value={profile.lifecycle?.keel_laid_date || ''}
                      onChange={(e) => setProfile({
                        ...profile,
                        lifecycle: { ...profile.lifecycle, keel_laid_date: e.target.value || null },
                      })}
                    />
                  </div>
                </div>

                <div className="form-group">
                  <span className="field-label">Operationsformer:</span>
                  <div className="checkbox-grid">
                    {OPERATION_OPTIONS.map((op) => (
                      <label key={op.value} className="checkbox-label">
                        <input
                          type="checkbox"
                          checked={(profile.operation_types || []).includes(op.value)}
                          onChange={() => toggleOperation(op.value)}
                        />
                        <span>{op.label}</span>
                      </label>
                    ))}
                  </div>
                </div>

                <div className="form-group">
                  <label className="checkbox-label">
                    <input
                      type="checkbox"
                      checked={Boolean(profile.cargo?.carries_dangerous_goods)}
                      onChange={(e) => setProfile({
                        ...profile,
                        cargo: { ...profile.cargo, carries_dangerous_goods: e.target.checked },
                      })}
                    />
                    <span>Fører farligt gods (IMDG / IBC / IGC)</span>
                  </label>
                </div>
              </fieldset>

              <div className="eval-options-box">
                <label className="checkbox-label">
                  <input
                    type="checkbox"
                    checked={treatEstimatedAsUnknown}
                    onChange={(e) => setTreatEstimatedAsUnknown(e.target.checked)}
                  />
                  <span>Streng revision (afvis skønnede værdier som ukendte)</span>
                </label>
                <label className="checkbox-label">
                  <input
                    type="checkbox"
                    checked={includeNonApplicable}
                    onChange={(e) => setIncludeNonApplicable(e.target.checked)}
                  />
                  <span>Vis også regler, der vurderes ikke at gælde</span>
                </label>
              </div>

              <button
                type="submit"
                className="btn btn-primary btn-block"
                disabled={loading}
              >
                {loading ? 'Beregner...' : 'Kør skibsvurdering'}
              </button>
            </form>
          </div>
        </aside>

        {/* Højre kolonne: Vurderingsresultat & Regeloversigt */}
        <main className="applicability-content">
          {error && <ErrorBox error={error} />}

          {/* Maskinel kontrol information med juridisk forbehold */}
          {response && (
            <div className="engine-status-card">
              <div className="engine-meta">
                <span className="engine-badge">
                  <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z"/></svg>
                  Algoritmisk tærskel- og betingelseskontrol
                </span>
                <span className="engine-hash" title="SHA-256 fingeraftryk af inputprofil">
                  Fingeraftryk: <code>{response.engine?.inputs_hash?.slice(0, 16) || 'eval-ok'}...</code>
                </span>
              </div>
              <p className="engine-desc">
                <strong>Vejledende vurdering:</strong> Regelmotoren kontrollerer 6 tekniske porte (Gyldighed, Jurisdiktion, Fartøjstype, Tærskelværdier, Undtagelser og Dækning).
                Svarene bygger på verificerede regeluddrag i databasen og udgør <em>ikke</em> en bindende myndighedsafgørelse.
              </p>
            </div>
          )}

          {/* Tællere & Filter-tabs */}
          {response?.counts && (
            <div className="verdict-summary-bar">
              <button
                type="button"
                className={`verdict-tab ${activeVerdictTab === 'ALL' ? 'active' : ''}`}
                onClick={() => setActiveVerdictTab('ALL')}
              >
                <span className="tab-title">Alle evaluerede</span>
                <span className="tab-count">{response.rules_evaluated}</span>
              </button>

              {['APPLIES', 'POSSIBLY_APPLIES', 'NEEDS_MANUAL_REVIEW', 'DOES_NOT_APPLY'].map((vKey) => {
                const count = response.counts[vKey] || 0
                const cfg = VERDICT_CONFIG[vKey]
                return (
                  <button
                    key={vKey}
                    type="button"
                    className={`verdict-tab tab-${vKey.toLowerCase()} ${activeVerdictTab === vKey ? 'active' : ''}`}
                    onClick={() => setActiveVerdictTab(vKey)}
                  >
                    <span className="tab-title">{cfg.label.replace('Vurdering: ', '')}</span>
                    <span className="tab-count">{count}</span>
                  </button>
                )
              })}
            </div>
          )}

          {loading && <Loading label="Evaluerer skibsprofil mod gældende regelværk..." />}

          {!loading && filteredResults.length > 0 && (
            <div className="rule-cards-list">
              {filteredResults.map((card, idx) => (
                <RuleCard key={card.rule_id || idx} card={card} />
              ))}
            </div>
          )}

          {!loading && response && filteredResults.length === 0 && (
            <Empty
              title="Ingen regler i denne kategori"
              hint="Prøv at vælge en anden fane eller juster skibsprofilen."
            />
          )}

          <div style={{ marginTop: 32 }}>
            <LegalNotice />
          </div>
        </main>
      </div>
    </div>
  )
}

/**
 * Enkelt regelskort med afgørelse, betingelser og citater.
 */
function RuleCard({ card }) {
  const [expanded, setExpanded] = useState(false)
  const verdictCfg = VERDICT_CONFIG[card.verdict] || {
    label: card.verdict_label || card.verdict,
    badgeClass: 'verdict-review',
    description: '',
  }

  return (
    <article className={`rule-card card-border-${card.verdict.toLowerCase()}`}>
      <div className="rule-card-header">
        <div className="rule-card-title-row">
          <h3 className="rule-card-title">
            <a href={`#/dokument/${card.document_id}`} title="Åbn det fulde dokument">
              {card.title || 'Uden titel'}
            </a>
          </h3>
          <span className={`verdict-badge ${verdictCfg.badgeClass}`}>
            {verdictCfg.label}
          </span>
        </div>

        <div className="rule-card-meta">
          <span className="rule-ref-tag">{card.rule_ref}</span>
          {card.document_type && <span className="badge-plain">{card.document_type}</span>}
          {card.authority && <span className="meta-item">{card.authority}</span>}
          {card.in_force_from && (
            <span className="meta-item">Gældende fra: {formatDate(card.in_force_from)}</span>
          )}
          <span className="confidence-pill" title="Profilkomplethed: andel af reglens nødvendige parametre der er oplyst">
            Profilkomplethed: {card.confidence} %
          </span>
        </div>
      </div>

      <div className="rule-card-body">
        <p className="rule-headline"><strong>{card.headline}</strong></p>
        {card.summary && <p className="rule-summary">{card.summary}</p>}

        {card.reasons?.length > 0 && (
          <ul className="reasons-list">
            {card.reasons.map((reason, rIdx) => (
              <li key={rIdx} className={`reason-item tone-${reason.tone}`}>
                <span className="reason-bullet">•</span>
                <span className="reason-text">{reason.text}</span>
              </li>
            ))}
          </ul>
        )}

        {card.missing_inputs?.length > 0 && (
          <div className="missing-inputs-alert">
            <h4>Følgende oplysninger er nødvendige for endelig afklaring:</h4>
            <ul>
              {card.missing_inputs.map((m, mIdx) => (
                <li key={mIdx}>
                  <strong>{m.label}</strong> ({m.field}): {m.hint || 'Ingen kildeværdi angivet i profilen.'}
                </li>
              ))}
            </ul>
          </div>
        )}

        {card.citations?.length > 0 && (
          <div className="statutory-citations">
            <span className="citations-header">Ordret skoptekst fra Retsinformation:</span>
            {card.citations.map((cite, cIdx) => (
              <blockquote key={cIdx} className="statutory-quote">
                <span className="quote-ref">{cite.ref}:</span> &ldquo;{cite.text}&rdquo;
              </blockquote>
            ))}
          </div>
        )}

        <div className="rule-card-footer">
          <button
            type="button"
            className="linklike toggle-details-btn"
            onClick={() => setExpanded(!expanded)}
          >
            {expanded ? '▲ Skjul teknisk beslutningstræ' : '▼ Vis teknisk beslutningstræ & betingelseskontrol'}
          </button>
        </div>

        {expanded && (
          <div className="rule-decision-details">
            <h4>Betingelseskontrol ({card.conditions?.length || 0} kontrolpunkter)</h4>
            {card.conditions?.length > 0 ? (
              <table className="conditions-table">
                <thead>
                  <tr>
                    <th>Felt / Parameter</th>
                    <th>Krav</th>
                    <th>Faktisk værdi</th>
                    <th>Udfald</th>
                  </tr>
                </thead>
                <tbody>
                  {card.conditions.map((cond, cIdx) => (
                    <tr key={cIdx} className={`cond-row cond-${cond.result.toLowerCase()}`}>
                      <td><code>{cond.field}</code><br /><small>{cond.label}</small></td>
                      <td>{cond.op} {String(cond.expected)}</td>
                      <td>
                        {cond.actual !== null ? String(cond.actual) : 'Ikke oplyst'}
                        {cond.actual_source && <small> ({cond.actual_source})</small>}
                        {cond.near_threshold && <span className="threshold-pill">Tæt på grænse</span>}
                      </td>
                      <td>
                        <span className={`result-tag result-${cond.result.toLowerCase()}`}>
                          {cond.result}
                        </span>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            ) : (
              <p className="hint">Ingen specifikke betingelser defineret for dette regeluddrag.</p>
            )}

            {card.decision_path?.length > 0 && (
              <div className="decision-path-flow">
                <h4>Trinvise kontrolporte</h4>
                <ol className="path-steps">
                  {card.decision_path.map((step, sIdx) => (
                    <li key={sIdx} className={`step-item step-${step.outcome.toLowerCase()}`}>
                      <strong>{step.gate_label}:</strong> {step.summary}
                      <span className="step-outcome-pill">{step.outcome}</span>
                    </li>
                  ))}
                </ol>
              </div>
            )}

            {card.supporting_fragments?.length > 0 && (
              <div className="supporting-fragments">
                <h4>Understøttende lovuddrag fundet ved vektorsøgning</h4>
                <p className="subtle-note">{card.supporting_fragments[0]?.badge}</p>
                {card.supporting_fragments.map((frag, fIdx) => (
                  <div key={fIdx} className="supporting-fragment-box">
                    <span className="frag-ref">{frag.ref}</span>
                    <p className="frag-text">{frag.text}</p>
                  </div>
                ))}
              </div>
            )}
          </div>
        )}
      </div>
    </article>
  )
}
