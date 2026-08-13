/**
 * Minimal hash-baseret routing.
 *
 * Bevidst uden router-bibliotek: applikationen har tre sider, og en
 * ekstra afhængighed ville koste mere end den giver.
 *
 * Ruter:
 *   #/                 søgeside
 *   #/dokument/:id     dokumentside
 *   #/import           import- og adminvisning
 */

import { useEffect, useState } from 'react'

export function parseHash(hash) {
  const raw = (hash || '').replace(/^#/, '') || '/'
  const [path, queryString] = raw.split('?')
  const segments = path.split('/').filter(Boolean)
  const query = Object.fromEntries(new URLSearchParams(queryString || ''))

  if (segments[0] === 'dokument' && segments[1]) {
    return { name: 'document', documentId: Number(segments[1]), query }
  }
  if (segments[0] === 'import') return { name: 'admin', query }
  return { name: 'search', query }
}

export function useRoute() {
  const [route, setRoute] = useState(() => parseHash(window.location.hash))

  useEffect(() => {
    const onChange = () => {
      setRoute(parseHash(window.location.hash))
      window.scrollTo(0, 0)
    }
    window.addEventListener('hashchange', onChange)
    return () => window.removeEventListener('hashchange', onChange)
  }, [])

  return route
}

export function navigate(path) {
  window.location.hash = path
}
