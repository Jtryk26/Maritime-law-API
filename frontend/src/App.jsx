import { useRoute } from './lib/router.js'
import SearchPage from './pages/SearchPage.jsx'
import DocumentPage from './pages/DocumentPage.jsx'
import AdminPage from './pages/AdminPage.jsx'

const NAV = [
  { href: '#/', label: 'Søg', route: 'search' },
  { href: '#/import', label: 'Import og drift', route: 'admin' },
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
            {NAV.map((item) => (
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
        {route.name === 'document' && <DocumentPage documentId={route.documentId} />}
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
