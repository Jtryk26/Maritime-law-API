import { useRoute } from './lib/router.js'
import { adminToken } from './lib/api.js'
import SearchPage from './pages/SearchPage.jsx'
import DocumentPage from './pages/DocumentPage.jsx'
import AdminPage from './pages/AdminPage.jsx'

/**
 * Navigationen viser kun det, brugeren må bruge.
 *
 * "Import og drift" er ikke længere et fast punkt. Linket dukker først op,
 * når der ligger et administratortoken i fanen — altså efter at nogen har
 * åbnet #/drift og logget ind. For en almindelig besøgende er dette en ren
 * søgetjeneste, uden spor af en driftsflade.
 */
const PUBLIC_NAV = [{ href: '#/', label: 'Søg', route: 'search' }]

const ADMIN_NAV = { href: '#/drift', label: 'Import og drift', route: 'admin' }

export default function App() {
  const route = useRoute()
  const nav = adminToken.isSet || route.name === 'admin'
    ? [...PUBLIC_NAV, ADMIN_NAV]
    : PUBLIC_NAV

  return (
    <div className="app">
      <header className="masthead">
        <div className="masthead-inner">
          <a className="wordmark" href="#/">
            Maritim <span>Lovdatabase</span>
          </a>
          <nav>
            {nav.map((item) => (
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
        {route.name === 'search' && <SearchPage query={route.query} />}
        {route.name === 'document' && (
          <DocumentPage documentId={route.documentId} query={route.query} />
        )}
        {route.name === 'admin' && <AdminPage />}
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
