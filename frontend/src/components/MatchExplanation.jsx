/**
 * Hvorfor står dette dokument her?
 *
 * Ved kombineret søgning kommer et resultat fra to uafhængige kilder, og
 * rækkefølgen er en sammensmeltning af to rangeringer. Uden en markering
 * ville brugeren ikke kunne se forskel på "ordene står i teksten" og
 * "teksten handler om noget beslægtet" — og netop den forskel afgør, om
 * man kan bruge dokumentet som henvisning.
 */

const LABELS = {
  lexical: { text: 'Ordmatch', title: 'Søgeordene står i dokumentet.' },
  semantic: {
    text: 'Betydningsmatch',
    title: 'Dokumentet handler om noget beslægtet, men bruger andre ord.',
  },
  both: {
    text: 'Ord + betydning',
    title: 'Fundet af både ordsøgning og betydningssøgning.',
  },
}

export default function MatchExplanation({ item }) {
  const label = LABELS[item.match_source] || LABELS.lexical
  const percent =
    item.semantic_score !== null && item.semantic_score !== undefined
      ? Math.round(item.semantic_score * 100)
      : null

  return (
    <span className="match-explanation">
      <span className={`match-tag match-${item.match_source}`} title={label.title}>
        {label.text}
      </span>
      {percent !== null && (
        <span className="match-similarity" title="Lighed med det bedst matchende stykke lovtekst">
          {percent} % lighed
        </span>
      )}
      {item.matched_heading && (
        <span className="match-heading" title="Stykket der matchede">
          {item.matched_heading}
        </span>
      )}
    </span>
  )
}
