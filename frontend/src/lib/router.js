/**
 * Minimal hash-baseret routing.
 *
 * Bevidst uden router-bibliotek: applikationen har tre sider, og en
 * ekstra afhængighed ville koste mere end den giver.
 *
 * Ruter:
 *   #/                 søgeside
 *   #/dokument/:id     dokumentside
 *   #/dokument/:id?p=  dokumentside, rullet til en paragraf (se format.js)
 *   #/drift            import- og driftsvisning (kræver administratortoken)
 *
 * Driftssiden er flyttet fra #/import til #/drift og er ikke længere
 * linket fra navigationen. Det er ikke sikkerhed i sig selv — beskyttelsen
 * er tokenet på API'et — men en offentlig tjeneste skal ikke reklamere
 * for, at der findes en driftsflade, og en almindelig bruger skal ikke
 * kunne klikke sig ind på en side, der kun kan svare "adgang nægtet".
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
  if (segments[0] === 'anvendelighed' || segments[0] === 'skibsvurdering') {
    return { name: 'applicability', ruleId: segments[1] ? Number(segments[1]) : null, query }
  }
  return { name: 'search', query }
}

export function useRoute() {
  const [route, setRoute] = useState(() => parseHash(window.location.hash))

  useEffect(() => {
    const onChange = () => {
      const next = parseHash(window.location.hash)
      setRoute(next)
      // Et paragrafanker ruller selv til sit mål; at springe til toppen
      // først ville give et synligt hop op og ned igen.
      if (!(next.name === 'document' && next.query.p)) window.scrollTo(0, 0)
    }
    window.addEventListener('hashchange', onChange)
    return () => window.removeEventListener('hashchange', onChange)
  }, [])

  return route
}

export function navigate(path) {
  window.location.hash = path
}
