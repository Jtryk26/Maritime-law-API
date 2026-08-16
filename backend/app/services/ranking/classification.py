"""Klassifikation af dokumenter: kernelov, speciallov eller støttedokument.

Hvorfor det er nødvendigt
=========================
Maritim relevans (0–100) siger *om* et dokument hører til i databasen.
Den siger intet om, hvor centralt det er. *Bekendtgørelse om sikkerhed
ved arbejdets udførelse på fiskeskibe* og *lov om sikkerhed til søs* er
begge utvivlsomt maritime og kan sagtens få samme relevansscore — men
den ene gælder alle danske skibe, og den anden gælder fiskeskibe.

Ved en bred søgning skal den brede regel stå først. Det kræver et
selvstændigt signal, og det er `law_class` sammen med `scope_score`.

De tre klasser
==============
``kernelaw``
    Bredt anvendelige, centrale regelsæt. Standardklassen for et maritimt
    dokument uden indsnævrende markør: har lovgiver ikke afgrænset
    anvendelsen, gælder reglen bredt.

``speciallaw``
    Dokumentet bærer mindst én nichemarkør i titel eller korttitel —
    fiskeskibe, Grønland, fritidsfartøjer, lodseri, offshore. Markørerne
    står i `config/ranking.yaml` og kan udvides uden kodeændring.

``support``
    Vejledninger, cirkulærer, ændringsbekendtgørelser og historiske
    versioner. De forklarer eller ændrer andre dokumenter frem for selv
    at være den regel, man skal læse.

Rækkefølgen af afgørelserne
===========================
Støttedokument prøves først, fordi klassen handler om dokumentets
*rolle*: en vejledning om fiskeskibe er en vejledning, uanset hvor smal
den er. Derefter niche, og til sidst kernelov som standard.

Retlig status indgår ikke
=========================
En historisk eller ophævet regel bliver ikke et støttedokument af at være
afløst — dens rolle er uændret. Status er et selvstændigt signal, som
rangeringen håndterer ét sted (``status_scores`` og ``historic_penalty``).
Blandedes de to, ville en ophævet særregel om fiskeskibe miste sin
nichemarkering og dermed ikke kunne findes ved en nichesøgning — og
nedjusteringen ville blive talt to gange.

To tal følger med
=================
``scope_score``      hvor bredt dokumentet gælder (0–1)
``authority_score``  hvor tungt det vejer som retskilde (0–1)

Begge bruges direkte i rangeringen og gemmes på dokumentet, så en søgning
ikke skal genberegne dem.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from app.core.text import fold, normalize_whitespace

from .config import NicheGroup, RankingConfig, get_ranking_config

__all__ = [
    "LawClass",
    "LawClassResult",
    "LawClassifier",
    "classify_law_class",
]


class LawClass:
    """Gyldige værdier. Ikke en Enum: værdien gemmes som streng i databasen
    og indgår i API'ets kontrakt, og en streng er der ét sted."""

    CORE = "kernelaw"
    SPECIAL = "speciallaw"
    SUPPORT = "support"

    ALL = (CORE, SPECIAL, SUPPORT)

    LABELS = {
        CORE: "Kernelov",
        SPECIAL: "Speciallov",
        SUPPORT: "Støttedokument",
    }


@dataclass(slots=True)
class LawClassResult:
    """Klassifikationen og de tal, rangeringen har brug for."""

    law_class: str
    scope_score: float
    authority_score: float
    #: Slugs for de nichegrupper dokumentet tilhører.
    niche_groups: list[str] = field(default_factory=list)
    #: De konkrete termer der udløste dem — så en afgørelse kan efterprøves.
    niche_terms: list[str] = field(default_factory=list)
    reasons: list[str] = field(default_factory=list)

    @property
    def label(self) -> str:
        return LawClass.LABELS.get(self.law_class, self.law_class)

    def to_json(self) -> dict:
        return {
            "law_class": self.law_class,
            "label": self.label,
            "scope_score": round(self.scope_score, 3),
            "authority_score": round(self.authority_score, 3),
            "niche_groups": list(self.niche_groups),
            "niche_terms": list(self.niche_terms),
            "reasons": list(self.reasons),
        }


def _clamp(value: float, low: float = 0.05, high: float = 1.0) -> float:
    return max(low, min(high, value))


class LawClassifier:
    """Afgør law_class, scope_score og authority_score for ét dokument.

    Bevidst uden databaseadgang: klassifikationen afhænger kun af
    dokumentets egne felter. Det gør den afprøvelig uden fixtures og
    kaldbar både under import og fra en genberegningskommando.
    """

    def __init__(self, config: RankingConfig | None = None) -> None:
        self.config = config or get_ranking_config()

    # -- Offentlig kontrakt -------------------------------------------------

    def classify(
        self,
        *,
        title: str | None,
        short_title: str | None = None,
        document_type: str | None = None,
        authority: str | None = None,
        status: str | None = None,
        maritime_score: int = 0,
        source_id: str | None = None,
    ) -> LawClassResult:
        cfg = self.config
        title_text = fold(normalize_whitespace(" ".join(filter(None, [title, short_title]))))
        folded_type = fold(document_type or "")
        folded_status = fold(status or "")

        matched_groups, matched_terms = self._match_niche(title_text)
        reasons: list[str] = []

        law_class = self._decide_class(
            folded_type=folded_type,
            folded_status=folded_status,
            title_text=title_text,
            matched_groups=matched_groups,
            maritime_score=maritime_score,
            authority=authority,
            source_id=source_id,
            reasons=reasons,
        )

        scope = self._scope_score(
            title_text=title_text,
            folded_type=folded_type,
            matched_groups=matched_groups,
            maritime_score=maritime_score,
        )
        authority_value = self._authority_score(
            document_type=document_type, authority=authority, law_class=law_class
        )

        return LawClassResult(
            law_class=law_class,
            scope_score=scope,
            authority_score=authority_value,
            niche_groups=[g.slug for g in matched_groups],
            niche_terms=matched_terms,
            reasons=reasons,
        )

    # -- Delafgørelser ------------------------------------------------------

    def _match_niche(self, title_text: str) -> tuple[list[NicheGroup], list[str]]:
        groups: list[NicheGroup] = []
        terms: list[str] = []
        for group in self.config.niche_groups:
            hits = group.matches(title_text)
            if hits:
                groups.append(group)
                terms.extend(hits)
        return groups, terms

    def _decide_class(
        self,
        *,
        folded_type: str,
        folded_status: str,
        title_text: str,
        matched_groups: list[NicheGroup],
        maritime_score: int,
        authority: str | None,
        source_id: str | None,
        reasons: list[str],
    ) -> str:
        cfg = self.config

        if source_id and str(source_id).strip() in cfg.core_source_ids:
            reasons.append("Manuelt udpeget som kernelov i konfigurationen")
            return LawClass.CORE

        # --- Støttedokument ---
        if folded_type and folded_type in cfg.support_types:
            reasons.append(f"Dokumenttypen er et støttedokument ({folded_type})")
            return LawClass.SUPPORT
        for pattern in cfg.support_patterns:
            if pattern and pattern in title_text:
                reasons.append(f"Titlen indeholder støttemønstret {pattern!r}")
                return LawClass.SUPPORT
        if folded_status and folded_status in cfg.support_statuses:
            reasons.append(f"Retlig status er {folded_status}")
            return LawClass.SUPPORT

        # --- Kernelov på eksplicit mønster ---
        for pattern in cfg.core_patterns:
            if pattern and pattern in title_text:
                reasons.append(f"Titlen matcher kernelovsmønstret {pattern!r}")
                return LawClass.CORE

        # --- Speciallov ---
        if matched_groups:
            labels = ", ".join(g.label for g in matched_groups)
            reasons.append(f"Titlen afgrænser anvendelsen til: {labels}")
            return LawClass.SPECIAL

        # --- Kernelov som standard ---
        if folded_type and folded_type in cfg.core_types:
            reasons.append("Dokumenttypen er en lov eller lovbekendtgørelse")
            return LawClass.CORE
        if (
            maritime_score >= cfg.core_min_maritime_score
            and fold(authority or "") in cfg.core_authorities
        ):
            reasons.append("Bred maritim regel fra en central myndighed")
            return LawClass.CORE

        reasons.append("Ingen indsnævrende markør i titlen")
        return LawClass.CORE

    def _scope_score(
        self,
        *,
        title_text: str,
        folded_type: str,
        matched_groups: list[NicheGroup],
        maritime_score: int,
    ) -> float:
        cfg = self.config
        score = 0.55

        if folded_type in cfg.core_types:
            score += 0.20
        if any(term and term in title_text for term in cfg.broad_terms):
            score += 0.10
        if maritime_score >= 80:
            score += 0.10

        # Hver nichemarkør indsnævrer. Styrken er gruppens egen: "Grønland"
        # afgrænser hårdere end "lods".
        for group in matched_groups:
            score -= 0.18 * group.strength

        return round(_clamp(score), 3)

    def _authority_score(
        self, *, document_type: str | None, authority: str | None, law_class: str
    ) -> float:
        cfg = self.config
        base = (
            cfg.type_weight * cfg.type_score(document_type)
            + cfg.authority_weight * cfg.authority_score(authority)
        )
        base += cfg.law_class_adjustment.get(law_class, 0.0)
        return round(_clamp(base), 3)


def classify_law_class(
    *,
    title: str | None,
    short_title: str | None = None,
    document_type: str | None = None,
    authority: str | None = None,
    status: str | None = None,
    maritime_score: int = 0,
    source_id: str | None = None,
    config: RankingConfig | None = None,
) -> LawClassResult:
    """Bekvemmelighedsfunktion for enkeltopslag."""
    return LawClassifier(config).classify(
        title=title,
        short_title=short_title,
        document_type=document_type,
        authority=authority,
        status=status,
        maritime_score=maritime_score,
        source_id=source_id,
    )
