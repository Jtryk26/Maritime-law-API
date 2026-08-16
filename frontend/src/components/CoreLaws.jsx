/**
 * "Start her" — de centrale maritime love.
 *
 * En tom søgeside, der viser hele databasen i vilkårlig rækkefølge,
 * fortæller ikke en ny bruger, hvor man begynder. Denne sektion gør:
 * den viser de brede, gældende regelsæt, som er udgangspunktet for
 * næsten ethvert maritimt spørgsmål.
 *
 * Udvælgelsen kommer fra `law_class` — samme klassifikation, som
 * rangeringen bruger. Der er altså ikke en håndholdt liste ved siden af
 * søgemaskinen, som kan komme ud af trit med den.
 *
 * Sektionen vises kun på en ufiltreret forside. Har brugeren søgt eller
 * filtreret, er svaret på skærmen mere relevant end vores udgangspunkt.
 */

import { useEffect, useState } from 'react'
import { api } from '../lib/api.js'
import { displayTitle } from '../lib/format.js'
import { ScoreTag } from './Common.jsx'

export default function CoreLaws({ visible }) {
  const [items, setItems] = useState(null)

  useEffect(() => {
    if (!visible) return undefined
    let cancelled = false
    api.coreLaws(8)
      .then((data) => { if (!cancelled) setItems(data) })
      .catch(() => { if (!cancelled) setItems([]) })
    return () => { cancelled = true }
  }, [visible])

  if (!visible || !items || items.length === 0) return null

  return (
    <section className="core-laws" aria-labelledby="core-laws-heading">
      <div className="core-laws-head">
        <h2 id="core-laws-heading">Start her — centrale maritime regler</h2>
        <p>
          Brede, gældende regelsæt der gælder skibsfarten generelt. Særregler for
          fiskeskibe, fritidsfartøjer eller grønlandske farvande finder du ved at
          søge på dem.
        </p>
      </div>

      <div className="core-laws-grid">
        {items.map((item) => (
          <a className="core-law" key={item.id} href={`#/dokument/${item.id}`}>
            <span className="core-law-title">{displayTitle(item)}</span>
            <span className="core-law-meta">
              <span>{item.document_type || 'Dokument'}</span>
              {item.published_date && <span>· {item.published_date.slice(0, 4)}</span>}
              <ScoreTag score={item.maritime_score} />
            </span>
          </a>
        ))}
      </div>
    </section>
  )
}
