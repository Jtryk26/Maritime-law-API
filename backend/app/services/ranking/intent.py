"""Hvad slags søgning er det?

En søgning på ``hviletid`` og en søgning på ``grønlandske lodser
hviletid`` skal ikke give samme rækkefølge. Den første er et fagområde:
brugeren vil have hovedreglen om hviletid for søfarende. Den anden er en
konkret undtagelse, og da skal netop den bekendtgørelse stå øverst — også
selv om den er smal og sjældent brugt.

Tre kategorier
==============
``broad``
    Få betydningsbærende ord, ingen nichemarkør. "hviletid", "brand
    passagerskib". Kernelove opjusteres, speciallove nedjusteres.

``semi``
    Længere formulering uden nichemarkør, eller en svag markør.
    Justeringen er den samme som ved ``broad``, men mildere: jo flere ord
    brugeren har skrevet, jo mere ved vedkommende selv, hvad der søges.

``niche``
    Mindst én tydelig nichemarkør. De speciallove, der hører til netop
    den niche, opjusteres kraftigt; speciallove for *andre* nicher
    nedjusteres fortsat — en søgning efter fiskeskibe skal ikke trække
    grønlandske særregler med op.

Metoden er den samme ordliste som klassifikationen bruger på titler,
hvilket ikke er tilfældigt: søgningen og dokumentet skal måles med samme
målestok, ellers kan de ikke bringes til at mødes.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from app.core.text import fold, tokenize

from .config import RankingConfig, get_ranking_config

__all__ = ["QueryIntent", "classify_query_intent", "refine_intent", "INTENT_KINDS"]

INTENT_KINDS = ("broad", "semi", "niche")

_INTENT_LABELS = {
    "broad": "Bred søgning",
    "semi": "Semispecifik søgning",
    "niche": "Nichesøgning",
}


@dataclass(slots=True)
class QueryIntent:
    """Resultatet af at læse søgestrengen."""

    kind: str = "broad"
    query: str = ""
    #: Betydningsbærende ord (stopord fjernet).
    tokens: list[str] = field(default_factory=list)
    #: Slugs for de nichegrupper søgningen peger på.
    niche_groups: list[str] = field(default_factory=list)
    #: Læsbare navne til de samme grupper ("Grønland", "Lodseri").
    #: Uden dem ville brugerfladen skulle vise slugs — og "groenland"
    #: er ikke noget, en bruger skal møde.
    niche_labels: list[str] = field(default_factory=list)
    niche_terms: list[str] = field(default_factory=list)
    #: Højeste styrke blandt de matchede grupper. 0.0 uden match.
    strength: float = 0.0
    #: Sat hvis klassifikationen blev ændret efter delsøgningen — se
    #: :func:`refine_intent`. Bevarer den oprindelige vurdering, så
    #: ændringen kan forklares frem for bare at ske.
    refined_from: str | None = None
    #: Klartekstbegrundelse for en eventuel ændring.
    refinement_reason: str | None = None

    @property
    def label(self) -> str:
        return _INTENT_LABELS.get(self.kind, self.kind)

    @property
    def is_niche(self) -> bool:
        return self.kind == "niche"

    def to_json(self) -> dict:
        return {
            "kind": self.kind,
            "label": self.label,
            "tokens": list(self.tokens),
            "niche_groups": list(self.niche_groups),
            "niche_labels": list(self.niche_labels),
            "niche_terms": list(self.niche_terms),
            "strength": round(self.strength, 3),
            "refined_from": self.refined_from,
            "refinement_reason": self.refinement_reason,
        }


def classify_query_intent(
    query: str | None, *, config: RankingConfig | None = None
) -> QueryIntent:
    """Klassificerer en søgestreng.

    En tom søgning er ``broad``: det er en gennemsynsliste, og der skal de
    centrale love stå øverst.
    """
    cfg = config or get_ranking_config()
    text = (query or "").strip()
    if not text:
        return QueryIntent(kind="broad", query="")

    folded = fold(text)
    stopwords = {fold(str(w)) for w in (cfg.intent.get("stopwords") or [])}
    tokens = [t for t in tokenize(text) if t not in stopwords]

    matched_slugs: list[str] = []
    matched_labels: list[str] = []
    matched_terms: list[str] = []
    strength = 0.0
    for group in cfg.niche_groups:
        hits = group.matches(folded)
        if hits:
            matched_slugs.append(group.slug)
            matched_labels.append(group.label)
            matched_terms.extend(hits)
            strength = max(strength, group.strength)

    threshold = float(cfg.intent.get("niche_min_strength", 0.6))
    broad_max = int(cfg.intent.get("broad_max_tokens", 2))

    if matched_slugs and strength >= threshold:
        kind = "niche"
    elif matched_slugs:
        kind = "semi"
    elif len(tokens) <= broad_max:
        kind = "broad"
    else:
        kind = "semi"

    return QueryIntent(
        kind=kind,
        query=text,
        tokens=tokens,
        niche_groups=matched_slugs,
        niche_labels=matched_labels,
        niche_terms=matched_terms,
        strength=strength,
    )


def refine_intent(
    intent: QueryIntent,
    lexical_result_count: int,
    *,
    config: RankingConfig | None = None,
) -> QueryIntent:
    """Justerer klassifikationen, når delsøgningen er kørt.

    Ordvalget alene er ikke nok til at afgøre, om en søgning er bred.
    ``trawlspil`` er ét ord uden nichemarkør og læses derfor først som
    bred — men termen findes i ét eneste dokument. Uden denne justering
    ville de brede domæneregler nedjustere netop det dokument, brugeren
    ledte efter, under dokumenter der slet ikke indeholder ordet.

    Antallet af leksikalske træf er det direkte mål for, hvor almindeligt
    det brugeren skrev, faktisk er i materialet. Er det lille, er
    søgningen specifik, uanset hvordan den er formuleret.

    Justeringen går kun én vej — mod mere specifik. En bred søgning med
    mange træf er allerede korrekt klassificeret.
    """
    cfg = config or get_ranking_config()
    threshold = int(cfg.intent.get("specific_max_results", 5))

    if intent.kind == "niche" or lexical_result_count <= 0 or lexical_result_count > threshold:
        return intent

    return QueryIntent(
        kind="niche",
        query=intent.query,
        tokens=list(intent.tokens),
        niche_groups=list(intent.niche_groups),
        niche_labels=list(intent.niche_labels),
        niche_terms=list(intent.niche_terms),
        strength=intent.strength,
        refined_from=intent.kind,
        refinement_reason=(
            f"Søgeordene findes kun i {lexical_result_count} "
            f"{'dokument' if lexical_result_count == 1 else 'dokumenter'} — "
            "søgningen behandles derfor som specifik."
        ),
    )
