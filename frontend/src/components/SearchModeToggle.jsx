/**
 * Valg af søgetilstand.
 *
 * De tre tilstande gør noget forskelligt, og forskellen er ikke til at
 * gætte. Derfor står forklaringen i brugerfladen frem for i en
 * dokumentation, ingen læser:
 *
 *   Ordret     — ordene skal stå i teksten.
 *   Betydning  — teksten skal handle om det samme.
 *   Kombineret — begge dele.
 *
 * Kan systemet ikke levere den valgte tilstand — typisk fordi indekset
 * ikke er bygget endnu — SIGES det. En bruger, der tror der blev søgt på
 * betydning, kan ellers konkludere at et emne er ureguleret, alene fordi
 * bekendtgørelsen bruger et andet ord.
 */

const MODES = [
  {
    value: 'hybrid',
    label: 'Kombineret',
    hint: 'Både ordene og betydningen. Anbefalet.',
  },
  {
    value: 'lexical',
    label: 'Ordret',
    hint: 'Ordene skal stå i teksten. Bedst til paragraf- og nummerhenvisninger.',
  },
  {
    value: 'semantic',
    label: 'Betydning',
    hint: 'Finder beslægtede formuleringer, også når ordvalget er et andet.',
  },
]

export default function SearchModeToggle({ mode, onChange, actualMode, notice }) {
  const downgraded = actualMode && mode !== 'lexical' && actualMode !== mode

  return (
    <div className="mode-toggle">
      <div className="mode-buttons" role="group" aria-label="Søgetilstand">
        {MODES.map((option) => (
          <button
            key={option.value}
            type="button"
            title={option.hint}
            aria-pressed={mode === option.value}
            className={mode === option.value ? 'mode active' : 'mode'}
            onClick={() => onChange(option.value)}
          >
            {option.label}
          </button>
        ))}
      </div>

      <p className="mode-hint">
        {MODES.find((m) => m.value === mode)?.hint}
      </p>

      {(downgraded || notice) && (
        <p className="mode-notice" role="status">
          {notice || 'Betydningssøgning er ikke tilgængelig. Der blev søgt ordret.'}
        </p>
      )}
    </div>
  )
}

export { MODES }
