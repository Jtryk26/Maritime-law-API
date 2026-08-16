/**
 * API-klient.
 *
 * Alle netværkskald samles her, så komponenter ikke kender endpoints
 * eller fejlformater.
 *
 * Kaldene er delt i to: de offentlige, som enhver besøgende må lave, og
 * de administrative, som kræver et token. Opdelingen står i denne fil og
 * ingen andre steder — en komponent kan ikke ved et uheld komme til at
 * sende tokenet et forkert sted hen.
 */

const BASE = import.meta.env.VITE_API_BASE || ''

/**
 * Tokenet gemmes i sessionStorage: det overlever et sideskift og et
 * genindlæs, men forsvinder når fanen lukkes. localStorage ville lade et
 * administratortoken ligge på en fælles skolecomputer på ubestemt tid.
 *
 * Afvejningen er bevidst: kunne en angriber køre JavaScript på siden,
 * kunne vedkommende læse tokenet — men også bare kalde API'et direkte fra
 * den åbne fane. Beskyttelsen mod det er Content-Security-Policy'en i
 * nginx, ikke hvor tokenet ligger.
 */
const TOKEN_KEY = 'maritim.admin.token'

export const adminToken = {
  get() {
    try { return sessionStorage.getItem(TOKEN_KEY) || '' } catch { return '' }
  },
  set(token) {
    try { sessionStorage.setItem(TOKEN_KEY, token) } catch { /* privat tilstand */ }
  },
  clear() {
    try { sessionStorage.removeItem(TOKEN_KEY) } catch { /* privat tilstand */ }
  },
  get isSet() {
    return Boolean(this.get())
  },
}

class ApiError extends Error {
  constructor(message, status, payload) {
    super(message)
    this.name = 'ApiError'
    this.status = status
    this.payload = payload
  }

  /** Tokenet mangler, er forkert, eller er ikke sat op på serveren. */
  get isAuthError() {
    return this.status === 401 || this.status === 503
  }

  get isRateLimited() {
    return this.status === 429
  }
}

async function request(path, options = {}) {
  const { admin = false, token, ...init } = options

  const headers = { 'Content-Type': 'application/json', ...(init.headers || {}) }
  if (admin) {
    const value = token ?? adminToken.get()
    if (value) headers.Authorization = `Bearer ${value}`
  }

  let response
  try {
    response = await fetch(`${BASE}${path}`, { ...init, headers })
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
      // Ikke JSON. Det sker, når en fejl kommer fra et lag foran
      // applikationen — en proxy eller Cloudflare — og svaret er en
      // HTML-side. Den må ikke ende som fejltekst i brugerfladen.
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
  // --- Offentligt -----------------------------------------------------------
  search: (params) => request(`/api/search${toQuery(params)}`),
  documents: (params) => request(`/api/documents${toQuery(params)}`),
  document: (id) => request(`/api/documents/${id}`),
  documentVersion: (id, n) => request(`/api/documents/${id}/versions/${n}`),
  categories: () => request('/api/categories'),
  facets: () => request('/api/facets'),
  similar: (id, limit = 6) => request(`/api/documents/${id}/similar${toQuery({ limit })}`),

  // --- Kræver administratortoken -------------------------------------------
  /** Afprøver et token uden at gemme det. Bruges af login-formularen. */
  adminSession: (token) => request('/api/admin/session', { admin: true, token }),
  stats: () => request('/api/stats', { admin: true }),
  importRuns: (params) => request(`/api/import/runs${toQuery(params)}`, { admin: true }),
  loggedQueries: (params) => request(`/api/search/queries${toQuery(params)}`, { admin: true }),
  relatedQueries: (params) => request(`/api/search/related${toQuery(params)}`, { admin: true }),
  embeddingStatus: () => request('/api/embeddings/status', { admin: true }),
  runEmbedding: (body) => request('/api/embeddings/run', {
    admin: true,
    method: 'POST',
    body: JSON.stringify(body ?? {}),
  }),
  runImport: (body) => request('/api/import/run', {
    admin: true,
    method: 'POST',
    body: JSON.stringify(body ?? {}),
  }),
}

export { ApiError }
