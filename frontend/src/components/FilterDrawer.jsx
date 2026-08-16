/**
 * Filterskuffe til mobil.
 *
 * En permanent sidebjælke på en telefon er enten for smal til at kunne
 * bruges eller så bred, at resultaterne forsvinder. Derfor er filtrene
 * på mobil en skuffe, der glider op nedefra, med en klæbende bundlinje.
 *
 * Ændringer slår igennem med det samme — knappen hedder derfor "Vis N
 * resultater" og ikke "Anvend". Alt andet ville kræve en midlertidig
 * kopi af filtertilstanden, og to sandheder om hvad der er valgt er
 * netop det, der gør filterpaneler forvirrende. "Ryd alt" ligger ved
 * siden af, så vejen tilbage er lige så kort som vejen ind.
 *
 * Skuffen fanger tastaturfokus, lukkes på Escape og låser baggrundens
 * scroll, mens den er åben.
 */

import { useEffect, useRef } from 'react'

export default function FilterDrawer({ open, onClose, onReset, resultCount, loading, children }) {
  const panel = useRef(null)

  useEffect(() => {
    if (!open) return undefined

    const previous = document.body.style.overflow
    document.body.style.overflow = 'hidden'

    const onKeyDown = (event) => {
      if (event.key === 'Escape') onClose()
    }
    document.addEventListener('keydown', onKeyDown)

    // Fokus flyttes ind i skuffen, så en skærmlæser og et tastatur
    // følger med derhen, hvor indholdet nu er.
    panel.current?.focus()

    return () => {
      document.body.style.overflow = previous
      document.removeEventListener('keydown', onKeyDown)
    }
  }, [open, onClose])

  if (!open) return null

  return (
    <div className="drawer-root">
      <div className="drawer-backdrop" onClick={onClose} aria-hidden="true" />
      <div
        className="drawer"
        role="dialog"
        aria-modal="true"
        aria-label="Filtre"
        tabIndex={-1}
        ref={panel}
      >
        <div className="drawer-head">
          <h2>Filtre</h2>
          <button type="button" className="drawer-close" onClick={onClose} aria-label="Luk filtre">
            ✕
          </button>
        </div>

        <div className="drawer-body">{children}</div>

        <div className="drawer-foot">
          <button type="button" className="drawer-clear" onClick={onReset}>
            Ryd
          </button>
          <button type="button" className="primary drawer-apply" onClick={onClose}>
            {loading
              ? 'Søger…'
              : `Vis ${resultCount ?? 0} ${resultCount === 1 ? 'resultat' : 'resultater'}`}
          </button>
        </div>
      </div>
    </div>
  )
}
