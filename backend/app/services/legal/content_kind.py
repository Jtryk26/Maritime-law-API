"""Hvad indeholder et dokument egentlig — lovtekst, eller kun metadata?

Hvorfor dette modul findes
==========================
En diagnostik af produktionskorpusset viste, at 2.438 af 3.411 maritime
dokumenter slet ikke indeholder paragraftegnet ``§``. Den nærliggende
konklusion var, at parseren tabte lovteksten. Direkte kontrol mod kilden
viste noget andet:

    https://www.retsinformation.dk/eli/accn/A18650999930/xml
    https://www.retsinformation.dk/eli/accn/B19300001605/xml

svarer begge HTTP 200 med et ``<Dokument>``, der **kun** har et
``<Meta>``-element. Kilden har ingen brødtekst for de dokumenter — de er
aldrig blevet digitaliseret som fuldtekst. Til sammenligning leverer
nyere dokumenter, fx accessionsnummer ``B20240123405``, både ``<Meta>``,
``<TitelGruppe>``, ``<DokumentIndhold>`` og ``<Bilag>``.

Forskellen er afgørende for driften, fordi de to tilfælde kræver hver sin
handling:

``metadata_only``
    Kilden har ikke teksten. Genimport hjælper ikke. Dokumentet kan indgå
    i søgning på metadata, men må aldrig tælle med, når man måler hvor
    stor en del af korpusset der er parset korrekt.

``text_without_paragraph_sign``
    Der ER brødtekst, men ingen ``§``. Enten er dokumenttypen sådan
    (cirkulærer, meddelelser, vejledninger), eller også er teksten hentet
    ind ad en vej, der tabte strukturen. Her ER genimport eller en
    parserrettelse relevant.

``full_text``
    Brødtekst med paragraffer. Kun disse kan bære anvendelighedsregler.

Uden den skelnen bliver et hvilket som helst dækningstal misvisende: man
kan ikke se forskel på "vi har ikke hentet teksten" og "teksten findes
ikke".
"""

from __future__ import annotations

__all__ = [
    "CONTENT_KIND_EMPTY",
    "CONTENT_KIND_METADATA_ONLY",
    "CONTENT_KIND_TEXT_WITHOUT_PARAGRAPH_SIGN",
    "CONTENT_KIND_FULL_TEXT",
    "CONTENT_KINDS",
    "PARAGRAPH_SIGN",
    "classify_content",
    "has_paragraph_sign",
]

#: Ingen tekst overhovedet — hverken metadata eller brødtekst.
CONTENT_KIND_EMPTY = "empty"

#: Kilden leverede kun metadata. Brødteksten findes ikke hos kilden.
CONTENT_KIND_METADATA_ONLY = "metadata_only"

#: Brødtekst findes, men uden paragraftegn.
CONTENT_KIND_TEXT_WITHOUT_PARAGRAPH_SIGN = "text_without_paragraph_sign"

#: Brødtekst med mindst én paragraf.
CONTENT_KIND_FULL_TEXT = "full_text"

#: Alle gyldige værdier, i stigende "hvor brugbart er dokumentet"-orden.
CONTENT_KINDS: tuple[str, ...] = (
    CONTENT_KIND_EMPTY,
    CONTENT_KIND_METADATA_ONLY,
    CONTENT_KIND_TEXT_WITHOUT_PARAGRAPH_SIGN,
    CONTENT_KIND_FULL_TEXT,
)

PARAGRAPH_SIGN = "§"


def has_paragraph_sign(content: str | None) -> bool:
    """Indeholder teksten mindst ét paragraftegn?"""
    return bool(content) and PARAGRAPH_SIGN in content


def classify_content(
    content: str | None,
    *,
    source_had_body: bool | None = None,
) -> str:
    """Afgør hvilken slags indhold der er tale om.

    :param content: Den brødtekst systemet har gemt eller netop har parset.
    :param source_had_body: Vidste kalderen — typisk XML-parseren — om
        kilden overhovedet leverede et brødtekstelement? ``False`` betyder
        "kilden havde kun metadata" og giver ``metadata_only``, også selv
        om der skulle ligge en rest af tekst. ``None`` betyder "det ved vi
        ikke", og så afgøres alt ud fra teksten selv. Det er tilfældet,
        når eksisterende rækker i databasen efterklassificeres.

    >>> classify_content("§ 1. Skibet skal være sødygtigt.")
    'full_text'
    >>> classify_content("", source_had_body=False)
    'metadata_only'
    >>> classify_content("Cirkulæret træder i kraft straks.")
    'text_without_paragraph_sign'
    >>> classify_content(None)
    'empty'
    """
    text = (content or "").strip()

    if source_had_body is False:
        # Kilden havde intet brødtekstelement. Det er en kildekendsgerning
        # og vejer tungere end hvad der måtte være faldet ud af parsingen.
        return CONTENT_KIND_METADATA_ONLY

    if not text:
        return CONTENT_KIND_EMPTY

    if has_paragraph_sign(text):
        return CONTENT_KIND_FULL_TEXT

    return CONTENT_KIND_TEXT_WITHOUT_PARAGRAPH_SIGN
