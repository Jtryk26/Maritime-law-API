/**
 * Adgangsspærre foran driftssiden.
 *
 * Spærren er en brugerfladebekvemmelighed, ikke sikkerhedsmekanismen:
 * beskyttelsen ligger på API'et, som afviser ethvert kald uden gyldigt
 * token. Formålet her er at afprøve tokenet én gang og give en forståelig
 * besked, frem for at lade siden sende seks kald, der alle fejler.
 */

import { useCallback, useEffect, useState } from 'react'
import { adminToken, api } from '../lib/api.js'

export default function AdminGate({ children }) {
  const [session, setSession] = useState(null)
  const [checking, setChecking] = useState(true)
  const [value, setValue] = useState('')
  const [error, setError] = useState(null)
  const [submitting, setSubmitting] = useState(false)

  // Et token fra en tidligere side i samme fane afprøves med det samme.
  useEffect(() => {
    let cancelled = false
    const stored = adminToken.get()
    if (!stored) {
      setChecking(false)
      return undefined
    }
    api.adminSession()
      .then((data) => { if (!cancelled) setSession(data) })
      .catch(() => { if (!cancelled) adminToken.clear() })
      .finally(() => { if (!cancelled) setChecking(false) })
    return () => { cancelled = true }
  }, [])

  const signOut = useCallback(() => {
    adminToken.clear()
    setSession(null)
    setValue('')
  }, [])

  const submit = async (event) => {
    event.preventDefault()
    const candidate = value.trim()
    if (!candidate) return

    setSubmitting(true)
    setError(null)
    try {
      // Afprøves FØR det gemmes, så et forkert token ikke bliver liggende.
      const data = await api.adminSession(candidate)
      adminToken.set(candidate)
      setSession(data)
      setValue('')
    } catch (err) {
      setError(err)
    } finally {
      setSubmitting(false)
    }
  }

  if (checking) {
    return (
      <div className="search-hero">
        <h1>Drift</h1>
        <p>Kontrollerer adgang…</p>
      </div>
    )
  }

  if (!session) {
    return (
      <>
        <div className="search-hero">
          <h1>Drift</h1>
          <p>Denne side er forbeholdt den driftsansvarlige.</p>
        </div>

        <div className="panel">
          <h2>Administratortoken</h2>
          <div className="panel-body">
            <form onSubmit={submit}>
              <div className="field">
                <label htmlFor="admin-token">Token</label>
                <input
                  id="admin-token"
                  type="password"
                  autoComplete="off"
                  autoFocus
                  value={value}
                  onChange={(event) => setValue(event.target.value)}
                  placeholder="ADMIN_API_TOKEN"
                  style={{ width: '100%', maxWidth: 420 }}
                />
              </div>

              <div className="import-controls" style={{ marginTop: 12 }}>
                <button className="primary" type="submit" disabled={submitting || !value.trim()}>
                  {submitting ? 'Kontrollerer…' : 'Lås op'}
                </button>
              </div>
            </form>

            {error && (
              <div className="error-box" style={{ marginTop: 14, textAlign: 'left' }}>
                {error.status === 503
                  ? 'Serveren har ikke opsat administratoradgang. Sæt ADMIN_API_TOKEN i miljøet og genstart backenden.'
                  : error.message}
              </div>
            )}

            <p className="panel-hint">
              Tokenet er serverens <code>ADMIN_API_TOKEN</code>. Det gemmes kun i denne
              fane og forsvinder, når fanen lukkes.
            </p>
          </div>
        </div>
      </>
    )
  }

  return children({ session, signOut })
}
