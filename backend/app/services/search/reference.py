"""Eksakte retskildehenvisninger: "BEK nr. 2207", "LBK 160", "2207".

Hvorfor et deterministisk opslag
================================
En søgning på ``BEK nr. 2207`` er ikke en begrebssøgning. Brugeren ved,
hvilket dokument der menes, og har skrevet dets nummer. Gennem den
almindelige rangering bliver forespørgslen derimod til ordet
"bekendtgørelse", som står i tusindvis af dokumenter, og et tal, der kun
vægter som ét token blandt mange. Domænereglerne gør det værre: er
dokumentet en ophævelsesakt eller et støttedokument, nedjusteres det —
og det efterspurgte dokument kan ende uden for første side.

Derfor læses henvisningen her med faste mønstre, og dokumenterne slås op
direkte på ``document_number`` (og ``retsinformation_id``). Træffene
placeres øverst uanset domæneregler. Resten af forespørgslen — "BEK 886
**olieforurening**" — søges som almindelig tekst og fylder siden op.

Formater
========
::

    BEK nr. 2207                         type + nummer
    LBK 160                              type + nummer
    Bekendtgørelse nr. 2207 af 30. november 2021
    BEK 2207/2021                        type + nummer + år
    nr. 2207                             nummer
    2207                                 nummer (hele forespørgslen)

Mønstrene er forankret i begyndelsen af forespørgslen. Et tal midt i en
almindelig søgning ("hviletid 24 timer") er ikke en henvisning.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.core.text import fold, normalize_whitespace
from app.models import Document

from .base import SearchQuery

__all__ = ["SourceReference", "parse_source_reference", "find_referenced_documents"]

#: Foldede typebetegnelser -> Retsinformations korte kode.
_TYPE_CODES: dict[str, str] = {
    "bek": "BEK",
    "bekendtgoerelse": "BEK",
    "lbk": "LBK",
    "lovbekendtgoerelse": "LBK",
    "lov": "LOV",
    "vej": "VEJ",
    "vejledning": "VEJ",
    "cir": "CIR",
    "cirk": "CIR",
    "cirkulaere": "CIR",
}

# Længste først, så "lovbekendtgoerelse" ikke læses som "lov".
_TYPE_ALTERNATIVES = "|".join(sorted(_TYPE_CODES, key=len, reverse=True))

#: Dato eller år efter nummeret: "af 30. november 2021", "af 30/11/2021",
#: "af 2021", "/2021". Kun årstallet bruges.
_DATE_TAIL = r"(?:\s*(?:af\s+|/\s*)(?P<date>[0-9./\s]*?(?:[a-z]+\s+)?(?P<year>(?:19|20)\d{2})))?"

#: "BEK nr. 2207 …", "bekendtgoerelse 2207 …", "lbk. 160 …".
_TYPED = re.compile(
    rf"^(?P<type>{_TYPE_ALTERNATIVES})\.?\s*(?:nr\.?\s*)?(?P<number>\d{{1,6}})\b{_DATE_TAIL}"
)
#: "nr. 2207 …".
_NUMBERED = re.compile(rf"^nr\.?\s*(?P<number>\d{{1,6}})\b{_DATE_TAIL}")
#: "2207" — kun når tallet er hele forespørgslen.
_BARE = re.compile(r"^(?P<number>\d{1,6})$")

#: Højst så mange dokumenter placeres øverst. Et rent tal kan ramme flere
#: dokumenttyper og år; flere end dette er ikke længere et "eksakt" svar.
MAX_EXACT_MATCHES = 10


@dataclass(frozen=True, slots=True)
class SourceReference:
    """En læst henvisning og det, der er tilbage af forespørgslen."""

    number: str
    doc_type: str | None = None
    year: int | None = None
    #: Forespørgslen uden henvisningen ("olieforurening"). Tom, hvis
    #: henvisningen var hele forespørgslen.
    remainder: str = ""

    @property
    def free_text(self) -> str:
        """Teksten den almindelige søgning skal køre på.

        Resten af forespørgslen, hvis der er en. Ellers nummeret: det finder
        dokumenter, der henviser til det efterspurgte — ikke alle dokumenter
        med ordet "bekendtgørelse".
        """
        return self.remainder or self.number


def parse_source_reference(text: str | None) -> SourceReference | None:
    """Læser en retskildehenvisning i begyndelsen af forespørgslen.

    Sammenligningen sker på foldet tekst (æ→ae, ø→oe, å→aa, små bogstaver),
    så "Bekendtgørelse", "bekendtgoerelse" og "BEK" er det samme.
    """
    folded = fold(normalize_whitespace(text or ""))
    if not folded:
        return None

    for pattern in (_TYPED, _NUMBERED, _BARE):
        match = pattern.match(folded)
        if not match:
            continue
        groups = match.groupdict()
        number = str(int(groups["number"]))  # "0886" og "886" er samme nummer
        doc_type = _TYPE_CODES.get(groups.get("type") or "")
        year = int(groups["year"]) if groups.get("year") else None
        remainder = _original_remainder(text or "", folded, match.end())
        return SourceReference(number=number, doc_type=doc_type, year=year, remainder=remainder)
    return None


def _original_remainder(text: str, folded: str, end: int) -> str:
    """Resten af forespørgslen i brugerens egen stavemåde.

    Foldningen ændrer længden (æ -> ae), så positionen i den foldede tekst
    kan ikke bruges direkte. Resten findes ved at folde den originale tekst
    ord for ord, til den foldede længde er nået.
    """
    original = normalize_whitespace(text)
    consumed = folded[:end].strip()
    words = original.split(" ")
    taken: list[str] = []
    for index, word in enumerate(words):
        taken.append(word)
        if len(fold(" ".join(taken))) >= len(consumed):
            return " ".join(words[index + 1 :]).strip()
    return ""


def _type_code(document_type: str | None) -> str | None:
    """Dokumentets type som kort kode, uanset om den er gemt kort eller lang."""
    folded = fold(document_type or "").strip(". ")
    return _TYPE_CODES.get(folded) or (folded.upper() or None)


def find_referenced_documents(
    session: Session, reference: SourceReference, query: SearchQuery, *, raw_text: str = ""
) -> list[Document]:
    """De dokumenter, henvisningen peger på — med brugerens filtre anvendt.

    Opslaget sker på ``document_number``. Er typen eller året angivet, skal
    de også passe; en "BEK 2207" er ikke en "LOV 2207". Er hele
    forespørgslen ét ord, prøves den også mod ``retsinformation_id``.

    Rækkefølgen ved flere træf: gældende ret først, derefter nyeste.
    """
    from .backends import _apply_filters

    numbers = {reference.number, reference.number.zfill(4)}
    conditions = [Document.document_number.in_(numbers)]
    single_token = raw_text.strip()
    if single_token and " " not in single_token:
        conditions.append(Document.retsinformation_id == single_token)

    stmt = _apply_filters(select(Document).where(or_(*conditions)), query)
    candidates = session.scalars(stmt).all()

    def matches(document: Document) -> bool:
        if document.retsinformation_id and document.retsinformation_id == single_token:
            return True
        if reference.doc_type and _type_code(document.document_type) != reference.doc_type:
            return False
        if reference.year and (
            document.published_date is None or document.published_date.year != reference.year
        ):
            return False
        return True

    found = [document for document in candidates if matches(document)]
    found.sort(
        key=lambda d: (
            0 if fold(d.status or "") == "gaeldende" else 1,
            -(d.published_date.toordinal() if d.published_date else 0),
            -d.id,
        )
    )
    return found[:MAX_EXACT_MATCHES]
