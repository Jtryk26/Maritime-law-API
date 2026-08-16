"""Juridisk struktur: parsing af lovtekst og normalisering af titler.

To ting hører sammen her, fordi de begge handler om at forstå et
dokuments *form* frem for dets emne:

* :mod:`app.services.legal.structure` deler en lovtekst i præambel,
  kapitler, afsnit og paragraffer. En paragraf er den enhed en jurist
  henviser til, og derfor systemets primære retrieval-enhed.
* :mod:`app.services.legal.titles` danner en kort visningstitel ved
  siden af den juridisk korrekte originaltitel.
"""

from .structure import (
    LegalChapter,
    LegalDocumentStructure,
    LegalParagraph,
    LegalSubsection,
    paragraph_sort_key,
    parse_legal_structure,
)
from .titles import derive_display_title, split_leading_type

__all__ = [
    "LegalChapter",
    "LegalDocumentStructure",
    "LegalParagraph",
    "LegalSubsection",
    "parse_legal_structure",
    "paragraph_sort_key",
    "derive_display_title",
    "split_leading_type",
]
