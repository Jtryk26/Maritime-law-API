/**
 * API-klient.
 *
 * Alle netværkskald samles her, så komponenter ikke kender endpoints
 * eller fejlformater.
 */

const BASE = import.meta.env.VITE_API_BASE || ''

class ApiError extends Error {
  constructor(message, status, payload) {
    super(message)
    this.name = 'ApiError'
    this.status = status
    this.payload = payload
  }
}

async function request(path, options = {}) {
  let response
  try {
    response = await fetch(`${BASE}${path}`, {
      headers: { 'Content-Type': 'application/json' },
      ...options,
    })
  } catch (cause) {
    throw new ApiError(
      'Kunne ikke kontakte serveren. Kører backenden?', 0, { cause: String(cause) },
    )
  }

  let payload = null
  const text = await response.text()
  if (text) {
    try { payload = JSON.parse(text) } catch { payload = { detail: text } }
  }

  if (!response.ok) {
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
  categories: () => request('/api/categories'),
  facets: () => request('/api/facets'),
  stats: () => request('/api/stats'),
  importRuns: (params) => request(`/api/import/runs${toQuery(params)}`),
  runImport: (body) => request('/api/import/run', {
    method: 'POST',
    body: JSON.stringify(body ?? {}),
  }),
}

export { ApiError }
