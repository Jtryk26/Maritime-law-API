import { lazy, Suspense } from 'react'
import { useRoute } from './lib/router.js'
import { GEOGRAPHY_ENABLED } from './lib/features.js'
import SearchPage from './pages/SearchPage.jsx'
import DocumentPage from './pages/DocumentPage.jsx'
import ApplicabilityPage from './pages/ApplicabilityPage.jsx'
import { Loading } from './components/Common.jsx'

// Kortet (eksperimentelt) trækker MapLibre med sig; det hentes først, når
// siden åbnes, og findes kun, når frontend er bygget med VITE_GEOGRAPHY_ENABLED.
const MapPage = GEOGRAPHY_ENABLED ? lazy(() => import('./pages/MapPage.jsx')) : null

/**
 * Offentlig navigation for den maritime lovdatabase.
 *
 * Alle brugere tilgår søgeværktøjet og skibsvurderingen ens.
 * Tjenesten indeholder ingen offentlig administrations- eller loginflade.
 */
const NAV_ITEMS = [
  { href: '#/', label: 'Søg i lovgivning', route: 'search' },
  { href: '#/anvendelighed', label: 'Skibsvurdering', route: 'applicability' },
  ...(GEOGRAPHY_ENABLED ? [{ href: '#/kort', label: 'Kort (eksperimentel)', route: 'map' }] : []),
]

export default function App() {
  const route = useRoute()

  return (
    <div className="app">
      <header className="masthead">
        <div className="masthead-inner">
          <a className="wordmark" href="#/">
            Maritim <span>Lovdatabase</span>
          </a>
          <nav>
            {NAV_ITEMS.map((item) => (
              <a
                key={item.href}
                href={item.href}
                className={route.name === item.route ? 'active' : ''}
              >
                {item.label}
              </a>
            ))}
          </nav>
        </div>
      </header>

      <main>
        {(route.name === 'search' || (route.name === 'map' && !MapPage)) && <SearchPage query={route.query} />}
        {route.name === 'document' && (
          <DocumentPage documentId={route.documentId} query={route.query} />
        )}
        {route.name === 'applicability' && (
          <ApplicabilityPage ruleId={route.ruleId} query={route.query} />
        )}
        {route.name === 'map' && MapPage && (
          <Suspense fallback={<Loading label="Henter kort…" />}>
            <MapPage query={route.query} />
          </Suspense>
        )}
      </main>

      <footer>
        <div className="inner">
          Dokumentdata er hentet fra <a href="https://www.retsinformation.dk" target="_blank"
            rel="noreferrer">Retsinformation</a>, som er den officielle retskilde.
          Denne tjeneste er et lokalt søge- og analyseværktøj og erstatter ikke den
          officielle kundgørelse. Kontrollér altid den gældende officielle tekst ved
          juridisk anvendelse.
        </div>
      </footer>
    </div>
  )
}
