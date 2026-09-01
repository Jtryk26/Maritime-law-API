/**
 * API-klient for den offentlige maritime søge- og analyseplatform.
 *
 * Alle netværkskald samles her, så komponenter ikke kender endpoints
 * eller fejlformater.
 *
 * Applikationen er rent læse- og evalueringsbaseret i den offentlige brugerflade.
 * Administrative opgaver (import, vektorisering, regeludtræk og godkendelse)
 * udføres via det interne administrations-CLI i et lukket vedligeholdelsesmiljø.
 */

const BASE = import.meta.env.VITE_API_BASE || ''

class ApiError extends Error {
  constructor(message, status, payload) {
    super(message)
    this.name = 'ApiError'
    this.status = status
    this.payload = payload
  }

  get isRateLimited() {
    return this.status === 429
  }
}

async function request(path, options = {}) {
  const headers = { 'Content-Type': 'application/json', ...(options.headers || {}) }

  let response
  try {
    response = await fetch(`${BASE}${path}`, { ...options, headers })
  } catch (cause) {
    throw new ApiError(
      'Kunne ikke kontakte serveren. Kører backenden?', 0, { cause: String(cause) },
    )
  }

  let payload = null
  const text = await response.text()
  if (text) {
    try {
      payload = JSON.parse(text)
    } catch {
      const isText = (response.headers.get('Content-Type') || '').startsWith('text/plain')
      payload = isText ? { detail: text } : null
    }
  }

  if (!response.ok) {
    if (response.status === 429) {
      const retry = response.headers.get('Retry-After')
      throw new ApiError(
        payload?.detail
          || `For mange forespørgsler. Prøv igen${retry ? ` om ${retry} sekunder` : ' om lidt'}.`,
        429, payload,
      )
    }
    throw new ApiError(payload?.detail || `Serverfejl (${response.status})`,
                       response.status, payload)
  }
  return payload
}

/** Bygger en querystring hvor arrays bliver til gentagne parametre. */
function toQuery(params) {
  const search = new URLSearchParams()
  for (const [key, value] of Object.entries(params)) {
    if (value === null || value === undefined || value === '') continue
    if (Array.isArray(value)) {
      value.forEach((v) => search.append(key, v))
    } else {
      search.append(key, value)
    }
  }
  const qs = search.toString()
  return qs ? `?${qs}` : ''
}

export const api = {
  search: (params) => request(`/api/search${toQuery(params)}`),
  documents: (params) => request(`/api/documents${toQuery(params)}`),
  document: (id) => request(`/api/documents/${id}`),
  documentVersion: (id, n) => request(`/api/documents/${id}/versions/${n}`),
  documentStructure: (id) => request(`/api/documents/${id}/structure`),
  coreLaws: (limit = 8) => request(`/api/core-laws${toQuery({ limit })}`),
  categories: () => request('/api/categories'),
  facets: () => request('/api/facets'),
  similar: (id, limit = 6) => request(`/api/documents/${id}/similar${toQuery({ limit })}`),
  applicabilityFields: () => request('/api/applicability/fields'),
  evaluateApplicability: (body) => request('/api/applicability/evaluate', {
    method: 'POST',
    body: JSON.stringify(body),
  }),
  applicabilityRule: (id) => request(`/api/applicability/rules/${id}`),
}


export { ApiError }
